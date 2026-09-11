"""PPT / Word 资料导出服务。

转换核心不依赖 Qt，可由 CLI 或后续 GUI Worker 直接调用。输入边界、文件名清洗和
输出目录去重统一复用 M1 模块。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal, Sequence

from core.file_policy import is_valid_input_file, scan_input_files
from core.office_staging import (
    OfficeStagingRun,
    cleanup_powerpoint_staging_run,
    create_powerpoint_staging_run,
    refresh_powerpoint_staging_run,
)
from core.output_paths import allocate_unique_directory


DocumentType = Literal["ppt", "word"]
Backend = Literal["ppt_mac", "ppt_com", "word_mac", "word_com", "libreoffice"]

BACKEND_PPT_MAC: Backend = "ppt_mac"
BACKEND_PPT_COM: Backend = "ppt_com"
BACKEND_WORD_MAC: Backend = "word_mac"
BACKEND_WORD_COM: Backend = "word_com"
BACKEND_LIBREOFFICE: Backend = "libreoffice"

ALL_BACKENDS: tuple[Backend, ...] = (
    BACKEND_PPT_MAC,
    BACKEND_PPT_COM,
    BACKEND_WORD_MAC,
    BACKEND_WORD_COM,
    BACKEND_LIBREOFFICE,
)

_BACKENDS_BY_TYPE: dict[DocumentType, tuple[Backend, ...]] = {
    "ppt": (BACKEND_PPT_MAC, BACKEND_PPT_COM, BACKEND_LIBREOFFICE),
    "word": (BACKEND_WORD_MAC, BACKEND_WORD_COM, BACKEND_LIBREOFFICE),
}


@dataclass(frozen=True)
class DocumentExportResult:
    """单个来源文件的导出结果。"""

    source_file: str
    success: bool
    output_dir: str
    pages_exported: int = 0
    backend_used: str = ""
    error: str = ""


@dataclass(frozen=True)
class DocumentExportSummary:
    """一次调用的结构化汇总，只包含本轮实际任务。"""

    success_count: int
    failed_count: int
    output_dir: str
    failed_files: list[dict[str, str]] = field(default_factory=list)
    skipped_count: int = 0
    results: list[DocumentExportResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["results"] = [asdict(result) for result in self.results]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _require_document_type(document_type: str) -> DocumentType:
    if document_type not in _BACKENDS_BY_TYPE:
        raise ValueError("document_type 只支持 'ppt' 或 'word'")
    return document_type  # type: ignore[return-value]


def collect_document_files(
    input_path: str | os.PathLike[str], document_type: str
) -> list[Path]:
    """收集本轮任务文件；目录递归扫描，单文件严格校验类型。"""
    mode = _require_document_type(document_type)
    source = Path(input_path).expanduser()
    if source.is_dir():
        return scan_input_files(source, mode, recursive=True)
    if source.is_file():
        if not is_valid_input_file(source, mode):
            raise ValueError(f"输入文件与 document_type={mode} 不匹配：{source}")
        return [source]
    raise FileNotFoundError(f"输入路径不存在或不是普通文件/目录：{source}")


def _find_libreoffice() -> str | None:
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    elif sys.platform == "win32":
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(os.path.join(root, "LibreOffice", "program", "soffice.exe"))
    for command in ("soffice", "libreoffice"):
        executable = shutil.which(command)
        if executable:
            candidates.append(executable)
    return next((candidate for candidate in candidates if os.path.isfile(candidate)), None)


def _detect_com(application: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import comtypes.client

        app = comtypes.client.CreateObject(f"{application}.Application")
        app.Quit()
        return True
    except Exception:
        return False


def detect_backends(document_type: str | None = None) -> list[Backend]:
    """返回当前系统可用后端，指定类型时不探测另一类 Office。"""
    mode = _require_document_type(document_type) if document_type is not None else None
    available: list[Backend] = []
    if sys.platform == "darwin":
        if mode in (None, "ppt") and os.path.isdir("/Applications/Microsoft PowerPoint.app"):
            available.append(BACKEND_PPT_MAC)
        if mode in (None, "word") and os.path.isdir("/Applications/Microsoft Word.app"):
            available.append(BACKEND_WORD_MAC)
    elif sys.platform == "win32":
        if mode in (None, "ppt") and _detect_com("PowerPoint"):
            available.append(BACKEND_PPT_COM)
        if mode in (None, "word") and _detect_com("Word"):
            available.append(BACKEND_WORD_COM)
    if _find_libreoffice():
        available.append(BACKEND_LIBREOFFICE)
    return available


def backends_for_type(
    document_type: str,
    available: Sequence[str],
    requested: str | None = None,
) -> list[Backend]:
    """按资料类型和用户选择过滤后端。"""
    mode = _require_document_type(document_type)
    compatible = _BACKENDS_BY_TYPE[mode]
    if requested and requested != "auto":
        if requested not in ALL_BACKENDS:
            raise ValueError(f"不支持的转换后端：{requested}")
        if requested not in compatible:
            raise ValueError(f"后端 {requested} 不能转换 {mode} 文件")
        if requested not in available:
            raise RuntimeError(f"指定后端当前不可用：{requested}")
        return [requested]  # type: ignore[list-item]
    return [backend for backend in compatible if backend in available]


def backend_display_name(backend: str) -> str:
    return {
        BACKEND_PPT_MAC: "PowerPoint (AppleScript)",
        BACKEND_PPT_COM: "PowerPoint (COM)",
        BACKEND_WORD_MAC: "Word (AppleScript)",
        BACKEND_WORD_COM: "Word (COM)",
        BACKEND_LIBREOFFICE: "LibreOffice",
    }.get(backend, backend)


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _export_pdf_mac(source: Path, pdf_path: Path, backend: Backend) -> None:
    source_literal = _applescript_string(str(source.resolve()))
    pdf_literal = _applescript_string(str(pdf_path.resolve()))
    if backend == BACKEND_PPT_MAC:
        application = "/Applications/Microsoft PowerPoint.app"
        command = (
            f"set openedDeck to open POSIX file {source_literal}\n"
            f"save openedDeck in POSIX file {pdf_literal} as save as PDF\n"
            "close openedDeck saving no"
        )
    elif backend == BACKEND_WORD_MAC:
        application = "/Applications/Microsoft Word.app"
        command = (
            f"open file name POSIX file {source_literal} read only true "
            "add to recent files false open and repair true\n"
            f"save as active document file name {pdf_literal} file format format PDF\n"
            "close active document saving no"
        )
    else:
        raise ValueError(f"不是 macOS Office 后端：{backend}")

    completed = subprocess.run(
        ["osascript", "-e", f'tell application "{application}"\n{command}\nend tell'],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0 or not pdf_path.is_file():
        detail = completed.stderr.strip() or "未生成 PDF"
        raise RuntimeError(f"{backend_display_name(backend)} 导出 PDF 失败：{detail}")


def _export_pdf_com(source: Path, pdf_path: Path, backend: Backend) -> None:
    import comtypes.client

    source_win = str(source.resolve()).replace("/", "\\")
    pdf_win = str(pdf_path.resolve()).replace("/", "\\")
    if backend == BACKEND_PPT_COM:
        app = comtypes.client.CreateObject("PowerPoint.Application")
        try:
            presentation = app.Presentations.Open(source_win, -1, 0, 0)
            try:
                presentation.ExportAsFixedFormat(pdf_win, 2)
            finally:
                presentation.Close()
        finally:
            app.Quit()
    elif backend == BACKEND_WORD_COM:
        app = comtypes.client.CreateObject("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        try:
            document = app.Documents.Open(
                source_win,
                ReadOnly=True,
                AddToRecentFiles=False,
                ConfirmConversions=False,
                OpenAndRepair=True,
            )
            try:
                document.SaveAs(pdf_win, FileFormat=17)
            finally:
                document.Close(False)
        finally:
            app.Quit()
    else:
        raise ValueError(f"不是 Windows Office 后端：{backend}")
    if not pdf_path.is_file():
        raise RuntimeError(f"{backend_display_name(backend)} 未生成 PDF")


def _export_pdf_libreoffice(source: Path, pdf_dir: Path) -> Path:
    soffice = _find_libreoffice()
    if not soffice:
        raise RuntimeError("未找到 LibreOffice")
    with tempfile.TemporaryDirectory(prefix="rongjing_libreoffice_profile_") as profile_name:
        profile_url = Path(profile_name).resolve().as_uri()
        completed = subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={profile_url}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_dir.resolve()),
                str(source.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    expected_pdf = pdf_dir / f"{source.stem}.pdf"
    pdfs = [expected_pdf] if expected_pdf.is_file() else sorted(pdf_dir.glob("*.pdf"))
    if completed.returncode != 0 or not pdfs:
        details = [f"返回码 {completed.returncode}"]
        if completed.stdout.strip():
            details.append(f"stdout: {completed.stdout.strip()}")
        if completed.stderr.strip():
            details.append(f"stderr: {completed.stderr.strip()}")
        if len(details) == 1:
            details.append(f"未在输出目录生成 PDF：{pdf_dir.resolve()}")
        raise RuntimeError(f"LibreOffice 导出 PDF 失败：{'；'.join(details)}")
    return pdfs[0]


def _render_pdf_page_range(
    pdf_path: Path,
    output_dir: Path,
    start: int,
    stop: int,
) -> None:
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        for index in range(start, stop):
            pixmap = document[index].get_pixmap(matrix=fitz.Matrix(2, 2))
            pixmap.save(str(output_dir / f"{index + 1}.png"))
    finally:
        document.close()


def _pdf_to_png(pdf_path: Path, output_dir: Path, max_pages: int) -> int:
    import fitz

    with fitz.open(str(pdf_path)) as document:
        page_count = min(len(document), max_pages)
    if page_count < 1:
        return 0

    worker_count = min(4, page_count, max(1, (os.cpu_count() or 2) - 1))
    if worker_count == 1:
        _render_pdf_page_range(pdf_path, output_dir, 0, page_count)
        return page_count

    chunk_size = (page_count + worker_count - 1) // worker_count
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _render_pdf_page_range,
                pdf_path,
                output_dir,
                start,
                min(start + chunk_size, page_count),
            )
            for start in range(0, page_count, chunk_size)
        ]
        for future in futures:
            future.result()
    return page_count


def convert_document(
    source_file: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    document_type: str,
    max_pages: int,
    backends: Sequence[Backend],
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[int, Backend]:
    """用后端回退链将一个资料文件转换为 PNG 页面。"""
    mode = _require_document_type(document_type)
    source = Path(source_file)
    destination = Path(output_dir)
    if max_pages < 1:
        raise ValueError("max_pages 必须大于 0")
    if not is_valid_input_file(source, mode):
        raise ValueError(f"输入文件与 document_type={mode} 不匹配：{source}")

    last_error: Exception | None = None
    for backend in backends:
        if backend not in _BACKENDS_BY_TYPE[mode]:
            continue
        try:
            if backend == BACKEND_PPT_MAC:
                staging = create_powerpoint_staging_run(source)
                try:
                    _export_pdf_mac(staging.source_copy, staging.pdf_path, backend)
                    refresh_powerpoint_staging_run(staging)
                    pages = _pdf_to_png(staging.pdf_path, destination, max_pages)
                    if pages < 1:
                        raise RuntimeError("PDF 没有可导出的页面")
                    return pages, backend
                finally:
                    try:
                        refresh_powerpoint_staging_run(staging)
                    except Exception as exc:
                        if log:
                            log(f"PowerPoint 中转副本身份记录失败，已保留并报告：{exc}")
                    cleanup_result = cleanup_powerpoint_staging_run(
                        staging.run_id
                    )
                    if cleanup_result["errors"] and log:
                        log("PowerPoint 中转副本未能完整清理，启动时将再次检查")

            with tempfile.TemporaryDirectory(prefix="rongjing_document_") as temp_name:
                pdf_dir = Path(temp_name)
                pdf_path = pdf_dir / "output.pdf"
                if backend == BACKEND_WORD_MAC:
                    _export_pdf_mac(source, pdf_path, backend)
                elif backend in (BACKEND_PPT_COM, BACKEND_WORD_COM):
                    _export_pdf_com(source, pdf_path, backend)
                elif backend == BACKEND_LIBREOFFICE:
                    pdf_path = _export_pdf_libreoffice(source, pdf_dir)
                else:
                    continue
                pages = _pdf_to_png(pdf_path, destination, max_pages)
                if pages < 1:
                    raise RuntimeError("PDF 没有可导出的页面")
                return pages, backend
        except Exception as exc:
            last_error = exc
            if log:
                log(f"{backend_display_name(backend)} 转换失败：{exc}")

    if last_error:
        raise RuntimeError(f"所有转换后端均失败；最后错误：{last_error}") from last_error
    raise RuntimeError(f"没有可用于 {mode} 的转换后端")


def export_material(
    *,
    document_type: str,
    input_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    max_pages: int = 17,
    backend: str | None = None,
    log: Callable[[str], None] | None = None,
) -> DocumentExportSummary:
    """导出一个文件或目录中的单一资料类型，并返回本轮汇总。"""
    mode = _require_document_type(document_type)
    if max_pages < 1:
        raise ValueError("max_pages 必须大于 0")
    sources = collect_document_files(input_path, mode)
    if not sources:
        raise ValueError(f"没有找到可处理的 {mode} 文件")

    output_root = Path(output_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    available = detect_backends(mode)
    selected = backends_for_type(mode, available, backend)
    if not selected:
        raise RuntimeError(f"没有可用于 {mode} 的转换后端")

    results: list[DocumentExportResult] = []
    failed_files: list[dict[str, str]] = []
    for source in sources:
        reserved_dir = allocate_unique_directory(output_root, source.name)
        try:
            pages, used_backend = convert_document(
                source,
                reserved_dir,
                mode,
                max_pages,
                selected,
                log=log,
            )
            result = DocumentExportResult(
                source_file=str(source),
                success=True,
                output_dir=str(reserved_dir),
                pages_exported=pages,
                backend_used=used_backend,
            )
        except Exception as exc:
            error = str(exc)
            result = DocumentExportResult(
                source_file=str(source),
                success=False,
                output_dir=str(reserved_dir),
                error=error,
            )
            failed_files.append({"file": str(source), "error": error})
        results.append(result)

    success_count = sum(result.success for result in results)
    return DocumentExportSummary(
        success_count=success_count,
        failed_count=len(failed_files),
        skipped_count=0,
        output_dir=str(output_root),
        failed_files=failed_files,
        results=results,
    )
