"""AI 背景图生成 — 封装 OpenAI 图像生成 API。

支持自定义 Base URL（兼容第三方中转站）。
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Optional, List
from PIL import Image


# ── 异常体系 ─────────────────────────────────────────────────────────
class AIBackgroundError(Exception):
    """AI 背景图生成的基类异常。"""

class AIAuthError(AIBackgroundError):
    """API Key 无效或缺失。"""

class AIRateLimitError(AIBackgroundError):
    """触发 API 限流（429）。"""

class AIQuotaError(AIBackgroundError):
    """配额耗尽（insufficient_quota）。"""

class AIBaseURLError(AIBackgroundError):
    """Base URL 不通或图像接口不支持。"""

class AINetworkError(AIBackgroundError):
    """网络超时或连接错误。"""


# ── 配置 ─────────────────────────────────────────────────────────────
@dataclass
class AIConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-image-2"
    timeout: float = 120.0


# ── 比例 → size 映射 ────────────────────────────────────────────────
# gpt-image-2 支持自定义尺寸（宽高均为 16 的倍数，比例 1:3~3:1，总像素 65 万~830 万）
SIZE_MAP_BY_MODEL = {
    "gpt-image": {
        "1:1":  "1024x1024",
        "16:9": "1536x864",    # 精确 16:9
        "9:16": "864x1536",    # 精确 9:16
        "4:3":  "1536x1152",   # 精确 4:3
        "3:4":  "1152x1536",   # 精确 3:4
    },
    # dall-e-3：只支持 1024x1024、1792x1024、1024x1792
    "dall-e-3": {
        "1:1":  "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3":  "1792x1024",   # 用 16:9 近似 4:3
        "3:4":  "1024x1792",   # 用 9:16 近似 3:4
    },
    # 其他模型：fallback 到 1024x1024
    "default": {
        "1:1":  "1024x1024",
        "16:9": "1024x1024",
        "9:16": "1024x1024",
        "4:3":  "1024x1024",
        "3:4":  "1024x1024",
    },
}

# 向后兼容别名。
SIZE_MAP = SIZE_MAP_BY_MODEL["dall-e-3"]


def _resolve_size(model: str, aspect_ratio: str) -> str:
    if model.startswith("gpt-image"):
        table = SIZE_MAP_BY_MODEL["gpt-image"]
    elif model.startswith("dall-e-3") or model == "dall-e-3":
        table = SIZE_MAP_BY_MODEL["dall-e-3"]
    else:
        table = SIZE_MAP_BY_MODEL["default"]
    return table.get(aspect_ratio, "1024x1024")


def _build_http_client(base_url: str, timeout: float):
    """构造带显式证书链和系统代理探测的 httpx client。

    当前只做 localhost/127.* 直连判断；未实现 no_proxy 的完整匹配规则，
    取舍原因是本期目标是修复打包环境下最常见的证书与系统代理问题，避免在
    单文件补丁里引入更大范围的代理规则解析分支。
    """
    import httpx

    try:
        import ssl
        import urllib.request
        from urllib.parse import urlparse

        import certifi

        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        is_localhost = host == "localhost" or host.startswith("127.")

        proxy_url = None
        if not is_localhost:
            proxies = urllib.request.getproxies()
            proxy_url = proxies.get("https") or proxies.get("http")

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        return httpx.Client(verify=ssl_context, proxy=proxy_url, timeout=timeout)
    except Exception:
        # 加固步骤失败（如打包环境缺 certifi 证书文件、getproxies 异常等）→
        # 降级到 httpx 默认行为，不让"加固"本身成为崩溃源。
        return httpx.Client(timeout=timeout)


def _format_network_error(e: Exception, base_url: str) -> str:
    import socket
    import ssl

    chain = []
    seen = set()
    current = e
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__

    chain_text = " | ".join(f"{type(err).__name__}: {err}" for err in chain)
    lowered = chain_text.lower()

    if any(isinstance(err, ssl.SSLError) for err in chain) or "ssl" in lowered or "certificate" in lowered:
        reason = "SSL 证书验证失败，可能是打包环境证书问题"
    elif any(isinstance(err, socket.gaierror) for err in chain) or "name or service not known" in lowered or "nodename nor servname" in lowered:
        reason = "域名解析失败，检查 Base URL 是否正确"
    elif "connection refused" in lowered:
        reason = "连接被拒绝，目标服务可能未运行"
    elif type(e).__name__ == "APITimeoutError" or "timed out" in lowered:
        reason = "连接超时"
    else:
        reason = "网络连接失败"

    root = chain[-1] if chain else e
    return f"{reason}。Base URL: {base_url}。底层异常: {type(root).__name__}: {root}"


def _response_data(resp: Any) -> list:
    """Extract image result items from SDK, dict, or JSON-string responses."""
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError as exc:
            raise AIBackgroundError(
                "API 返回了纯文本，无法解析为图片数据。请检查 Base URL 是否指向兼容 OpenAI Images API 的地址。"
            ) from exc

    if isinstance(resp, dict):
        data = resp.get("data")
        output_items = _output_image_items(resp.get("output"))
        if isinstance(data, list) and data:
            return data
        if output_items:
            return output_items
        if isinstance(data, list):
            return data
        raise AIBackgroundError("API 返回格式无法解析（缺少 data 数组）")

    data = getattr(resp, "data", None)
    output_items = _output_image_items(getattr(resp, "output", None))
    if isinstance(data, list) and data:
        return data
    if output_items:
        return output_items
    if isinstance(data, list):
        return data
    raise AIBackgroundError("API 返回格式无法解析（缺少 data 数组）")


def _output_image_items(output: Any) -> list:
    if not isinstance(output, list):
        return []
    return [item for item in output if _has_image_value(item)]


def _has_image_value(item: Any) -> bool:
    return bool(_item_value(item, "b64_json") or _item_value(item, "result") or _item_value(item, "url"))


def _item_value(item: Any, key: str):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


# ── 核心 API ─────────────────────────────────────────────────────────
def generate_backgrounds(
    config: AIConfig,
    prompt: str,
    n: int = 1,
    aspect_ratio: str = "4:3",
) -> List[Image.Image]:
    """生成 n 张背景图，返回 PIL Image 列表。"""
    try:
        from openai import OpenAI, AuthenticationError, RateLimitError, APIConnectionError, APITimeoutError, NotFoundError, BadRequestError
    except ImportError:
        raise AIBackgroundError("openai SDK 未安装，请运行 `pip install openai`")

    http_client = _build_http_client(config.base_url, config.timeout)
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        http_client=http_client,
    )

    size = _resolve_size(config.model, aspect_ratio)

    try:
        try:
            resp = client.images.generate(
                model=config.model,
                prompt=prompt,
                size=size,
                n=n,
            )
        except AuthenticationError as e:
            raise AIAuthError(f"API Key 无效: {e}") from e
        except RateLimitError as e:
            msg = str(e).lower()
            if "quota" in msg or "insufficient" in msg or "billing" in msg:
                raise AIQuotaError(f"配额耗尽: {e}") from e
            raise AIRateLimitError(f"限流: {e}") from e
        except (APIConnectionError, APITimeoutError) as e:
            detail = _format_network_error(e, config.base_url)
            raise AINetworkError(detail) from e
        except NotFoundError as e:
            raise AIBaseURLError(f"模型 '{config.model}' 不存在或 Base URL '{config.base_url}' 不支持图像生成: {e}") from e
        except BadRequestError as e:
            raise AIBackgroundError(f"参数错误: {e}") from e
        except Exception as e:
            raise AIBackgroundError(f"未知错误: {e}") from e
    finally:
        http_client.close()

    images = []
    for item in _response_data(resp):
        b64_json = _item_value(item, "b64_json")
        if not b64_json:
            b64_json = _item_value(item, "result")
        url = _item_value(item, "url")
        if b64_json:
            import base64
            img_bytes = base64.b64decode(b64_json)
            img = Image.open(io.BytesIO(img_bytes))
        elif url:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "rongjing/2.0"})
            with urllib.request.urlopen(req, timeout=config.timeout) as r:
                img = Image.open(io.BytesIO(r.read()))
        else:
            raise AIBackgroundError("API 返回格式无法解析（无 b64_json 也无 url）")
        # 创建全新图片只保留像素数据，彻底剥离 C2PA / EXIF 等所有元数据
        clean = Image.new(img.mode, img.size)
        clean.paste(img)
        images.append(clean)

    return images


def test_connection(config: AIConfig) -> dict:
    """测试连接性，返回 {ok, model_used, size_used, image_count, error}。"""
    result = {"ok": False, "model_used": config.model, "size_used": "1024x1024",
              "image_count": 0, "error": None}
    try:
        imgs = generate_backgrounds(config, "A laptop on a desk, screen is solid black", n=1, aspect_ratio="1:1")
        result["ok"] = True
        result["image_count"] = len(imgs)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result
