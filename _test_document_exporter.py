import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli
from core import document_exporter as exporter


class DocumentInputRoutingTest(unittest.TestCase):
    def test_directory_scan_is_recursive_and_type_specific(self):
        visible = [Path("/inputs/chapter2.pptx"), Path("/inputs/nested/chapter10.ppt")]
        with mock.patch.object(Path, "is_dir", return_value=True), mock.patch.object(
            exporter, "scan_input_files", return_value=visible
        ) as scan:
            result = exporter.collect_document_files("/inputs", "ppt")

        self.assertEqual(result, visible)
        scan.assert_called_once_with(Path("/inputs"), "ppt", recursive=True)

    def test_single_file_must_match_selected_type(self):
        with mock.patch.object(Path, "is_dir", return_value=False), mock.patch.object(
            Path, "is_file", return_value=True
        ), mock.patch.object(exporter, "is_valid_input_file", return_value=False):
            with self.assertRaisesRegex(ValueError, "不匹配"):
                exporter.collect_document_files("/inputs/lesson.docx", "ppt")

    def test_document_type_is_strict(self):
        with self.assertRaisesRegex(ValueError, "ppt.*word"):
            exporter.collect_document_files("/inputs", "pdf")


class DocumentExportServiceTest(unittest.TestCase):
    def test_ppt_mac_uses_same_staging_root_and_always_cleans_up(self):
        root = Path("/fixed/融景Office中转")
        run = exporter.OfficeStagingRun(
            root=root,
            run_id="d" * 32,
            source_copy=root / "rongjing-office-copy.pptx",
            pdf_path=root / "rongjing-office-output.pdf",
            manifest_path=root / "rongjing-office.manifest.json",
        )
        with mock.patch.object(exporter, "is_valid_input_file", return_value=True), \
             mock.patch.object(exporter, "create_powerpoint_staging_run", return_value=run) as create, \
             mock.patch.object(exporter, "_export_pdf_mac") as export_pdf, \
             mock.patch.object(exporter, "_pdf_to_png", return_value=3), \
             mock.patch.object(exporter, "cleanup_powerpoint_staging_run") as cleanup:
            pages, backend = exporter.convert_document(
                "/inputs/lesson.pptx",
                "/output",
                "ppt",
                8,
                [exporter.BACKEND_PPT_MAC],
                office_staging_root=root,
            )

        self.assertEqual((pages, backend), (3, exporter.BACKEND_PPT_MAC))
        create.assert_called_once_with(Path("/inputs/lesson.pptx"), root=root)
        export_pdf.assert_called_once_with(
            run.source_copy, run.pdf_path, exporter.BACKEND_PPT_MAC
        )
        cleanup.assert_called_once_with(root, run.run_id)

    def test_ppt_mac_cleanup_runs_when_export_fails(self):
        root = Path("/fixed/融景Office中转")
        run = exporter.OfficeStagingRun(
            root=root,
            run_id="e" * 32,
            source_copy=root / "copy.pptx",
            pdf_path=root / "copy.pdf",
            manifest_path=root / "copy.manifest.json",
        )
        with mock.patch.object(exporter, "is_valid_input_file", return_value=True), \
             mock.patch.object(exporter, "create_powerpoint_staging_run", return_value=run), \
             mock.patch.object(exporter, "_export_pdf_mac", side_effect=RuntimeError("mock fail")), \
             mock.patch.object(exporter, "cleanup_powerpoint_staging_run") as cleanup:
            with self.assertRaisesRegex(RuntimeError, "所有转换后端均失败"):
                exporter.convert_document(
                    "/inputs/lesson.pptx",
                    "/output",
                    "ppt",
                    8,
                    [exporter.BACKEND_PPT_MAC],
                    office_staging_root=root,
                )

        cleanup.assert_called_once_with(root, run.run_id)

    def test_libreoffice_still_uses_temporary_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            temp_pdf_dir = Path(folder) / "lo-temp"
            temp_pdf_dir.mkdir()
            pdf_path = temp_pdf_dir / "lesson.pdf"
            with mock.patch.object(exporter, "is_valid_input_file", return_value=True), \
                 mock.patch.object(exporter.tempfile, "TemporaryDirectory") as temporary, \
                 mock.patch.object(exporter, "_export_pdf_libreoffice", return_value=pdf_path) as export_pdf, \
                 mock.patch.object(exporter, "_pdf_to_png", return_value=1), \
                 mock.patch.object(exporter, "create_powerpoint_staging_run") as create:
                temporary.return_value.__enter__.return_value = str(temp_pdf_dir)
                pages, backend = exporter.convert_document(
                    "/inputs/lesson.pptx",
                    "/output",
                    "ppt",
                    8,
                    [exporter.BACKEND_LIBREOFFICE],
                )

        self.assertEqual((pages, backend), (1, exporter.BACKEND_LIBREOFFICE))
        temporary.assert_called_once_with(prefix="rongjing_document_")
        export_pdf.assert_called_once_with(Path("/inputs/lesson.pptx"), temp_pdf_dir)
        create.assert_not_called()

    def test_summary_failure_and_atomic_allocator_only_cover_current_run(self):
        sources = [Path("/inputs/lesson1.pptx"), Path("/inputs/lesson2.ppt")]
        allocated = [Path("/output/lesson1"), Path("/output/lesson2")]

        def fake_convert(source, output_dir, document_type, max_pages, backends, *, log=None):
            if source.name == "lesson2.ppt":
                raise RuntimeError("mock conversion failed")
            return 4, exporter.BACKEND_PPT_MAC

        with mock.patch.object(exporter, "collect_document_files", return_value=sources), \
             mock.patch.object(Path, "mkdir"), \
             mock.patch.object(exporter, "detect_backends", return_value=[exporter.BACKEND_PPT_MAC]) as detect, \
             mock.patch.object(exporter, "allocate_unique_directory", side_effect=allocated) as allocate, \
             mock.patch.object(exporter, "convert_document", side_effect=fake_convert):
            summary = exporter.export_material(
                document_type="ppt",
                input_path="/inputs",
                output_dir="/output",
                max_pages=8,
            )

        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.skipped_count, 0)
        self.assertEqual(summary.output_dir, "/output")
        self.assertEqual(summary.failed_files[0]["file"], "/inputs/lesson2.ppt")
        self.assertEqual([result.output_dir for result in summary.results], [str(p) for p in allocated])
        self.assertEqual(allocate.call_count, 2)
        detect.assert_called_once_with("ppt")

    def test_consecutive_runs_request_new_m1_directory_each_time(self):
        source = Path("/inputs/lesson.pptx")
        with mock.patch.object(exporter, "collect_document_files", return_value=[source]), \
             mock.patch.object(Path, "mkdir"), \
             mock.patch.object(exporter, "detect_backends", return_value=[exporter.BACKEND_PPT_MAC]), \
             mock.patch.object(
                 exporter,
                 "allocate_unique_directory",
                 side_effect=[Path("/output/lesson"), Path("/output/lesson_2")],
             ) as allocate, \
             mock.patch.object(
                 exporter, "convert_document", return_value=(2, exporter.BACKEND_PPT_MAC)
             ):
            first = exporter.export_material(
                document_type="ppt", input_path="/inputs", output_dir="/output"
            )
            second = exporter.export_material(
                document_type="ppt", input_path="/inputs", output_dir="/output"
            )

        self.assertEqual(first.results[0].output_dir, "/output/lesson")
        self.assertEqual(second.results[0].output_dir, "/output/lesson_2")
        self.assertEqual(allocate.call_count, 2)

    def test_backend_selection_never_crosses_document_type(self):
        available = [
            exporter.BACKEND_WORD_MAC,
            exporter.BACKEND_PPT_MAC,
            exporter.BACKEND_LIBREOFFICE,
        ]
        self.assertEqual(
            exporter.backends_for_type("ppt", available),
            [exporter.BACKEND_PPT_MAC, exporter.BACKEND_LIBREOFFICE],
        )
        with self.assertRaisesRegex(ValueError, "不能转换"):
            exporter.backends_for_type("word", available, exporter.BACKEND_PPT_MAC)

    def test_powerpoint_applescript_uses_resolvable_application_path(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temp_name:
            pdf_path = Path(temp_name) / "output.pdf"

            def fake_run(command, **kwargs):
                pdf_path.write_bytes(b"pdf")
                return completed

            with mock.patch.object(exporter.subprocess, "run", side_effect=fake_run) as run:
                exporter._export_pdf_mac(
                    Path("/inputs/lesson.pptx"), pdf_path, exporter.BACKEND_PPT_MAC
                )

        script = run.call_args.args[0][2]
        self.assertIn('tell application "/Applications/Microsoft PowerPoint.app"', script)
        self.assertIn("save active presentation", script)

    def test_libreoffice_uses_separate_profile_and_expected_pdf_name(self):
        completed = mock.Mock(returncode=0, stdout="converted", stderr="")
        with tempfile.TemporaryDirectory() as temp_name:
            pdf_dir = Path(temp_name)
            source = pdf_dir / "lesson notes.docx"
            source.write_bytes(b"docx")

            def fake_run(command, **kwargs):
                (pdf_dir / "lesson notes.pdf").write_bytes(b"pdf")
                return completed

            with mock.patch.object(exporter, "_find_libreoffice", return_value="/soffice"), \
                 mock.patch.object(exporter.subprocess, "run", side_effect=fake_run) as run:
                result = exporter._export_pdf_libreoffice(source, pdf_dir)

        command = run.call_args.args[0]
        profile_arg = next(value for value in command if value.startswith("-env:UserInstallation="))
        self.assertNotIn(str(pdf_dir), profile_arg)
        self.assertEqual(result.name, "lesson notes.pdf")


class DocumentExportCliTest(unittest.TestCase):
    def test_export_material_cli_parses_alias_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as temp_name:
            output_dir = Path(temp_name) / "notes"
            output_dir.mkdir()
            (output_dir / "1.png").write_bytes(b"png")
            summary = exporter.DocumentExportSummary(
                success_count=1,
                failed_count=0,
                skipped_count=0,
                output_dir=temp_name,
                results=[
                    exporter.DocumentExportResult(
                        source_file="/inputs/notes.docx",
                        success=True,
                        output_dir=str(output_dir),
                        pages_exported=1,
                        backend_used=exporter.BACKEND_LIBREOFFICE,
                    )
                ],
            )
            stdout = io.StringIO()
            with mock.patch.object(exporter, "export_material", return_value=summary) as service, \
                 redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "export-material",
                        "--type", "word",
                        "--input", "/inputs/notes.docx",
                        "--output", "/output",
                        "--max-slides", "9",
                        "--backend", "libreoffice",
                    ]
                )

        self.assertEqual(exit_code, 0)
        service.assert_called_once()
        self.assertEqual(service.call_args.kwargs["document_type"], "word")
        self.assertEqual(service.call_args.kwargs["max_pages"], 9)
        self.assertEqual(service.call_args.kwargs["backend"], "libreoffice")
        self.assertEqual(json.loads(stdout.getvalue())["success_count"], 1)

    def test_export_material_cli_returns_nonzero_for_failed_summary(self):
        summary = exporter.DocumentExportSummary(
            success_count=0,
            failed_count=1,
            skipped_count=0,
            output_dir="/output",
            failed_files=[{"file": "/inputs/broken.pptx", "error": "failed"}],
            results=[
                exporter.DocumentExportResult(
                    source_file="/inputs/broken.pptx",
                    success=False,
                    output_dir="/output/broken",
                    error="failed",
                )
            ],
        )
        with mock.patch.object(exporter, "export_material", return_value=summary), \
             redirect_stdout(io.StringIO()):
            exit_code = cli.export_material_cmd("ppt", "/inputs", "/output", 17, None)

        self.assertEqual(exit_code, 1)

    def test_export_material_cli_returns_nonzero_when_png_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_name:
            output_dir = Path(temp_name) / "lesson"
            output_dir.mkdir()
            summary = exporter.DocumentExportSummary(
                success_count=1,
                failed_count=0,
                skipped_count=0,
                output_dir=temp_name,
                results=[
                    exporter.DocumentExportResult(
                        source_file="/inputs/lesson.pptx",
                        success=True,
                        output_dir=str(output_dir),
                        pages_exported=1,
                        backend_used=exporter.BACKEND_PPT_MAC,
                    )
                ],
            )
            with mock.patch.object(exporter, "export_material", return_value=summary), \
                 redirect_stdout(io.StringIO()):
                exit_code = cli.export_material_cmd("ppt", "/inputs", temp_name, 17, None)

        self.assertEqual(exit_code, 1)

    def test_export_material_cli_returns_nonzero_for_zero_pages(self):
        with tempfile.TemporaryDirectory() as temp_name:
            output_dir = Path(temp_name) / "lesson"
            output_dir.mkdir()
            summary = exporter.DocumentExportSummary(
                success_count=1,
                failed_count=0,
                skipped_count=0,
                output_dir=temp_name,
                results=[
                    exporter.DocumentExportResult(
                        source_file="/inputs/lesson.pptx",
                        success=True,
                        output_dir=str(output_dir),
                        pages_exported=0,
                        backend_used=exporter.BACKEND_PPT_MAC,
                    )
                ],
            )
            with mock.patch.object(exporter, "export_material", return_value=summary), \
                 redirect_stdout(io.StringIO()):
                exit_code = cli.export_material_cmd("ppt", "/inputs", temp_name, 17, None)

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
