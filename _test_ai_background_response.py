import base64
import io
import json

from PIL import Image

from core.ai_background import AIBackgroundError, _response_data, normalize_base_url


def _tiny_png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main():
    assert normalize_base_url("https://api.tu-zi.com") == "https://api.tu-zi.com/v1"
    assert normalize_base_url("https://api.tu-zi.com/") == "https://api.tu-zi.com/v1"
    assert normalize_base_url("https://api.tu-zi.com/v1") == "https://api.tu-zi.com/v1"
    assert normalize_base_url('{"url":"https://api.tu-zi.com"}') == "https://api.tu-zi.com/v1"

    payload = {"data": [{"b64_json": _tiny_png_b64()}]}
    assert _response_data(payload)[0]["b64_json"]
    assert _response_data(json.dumps(payload))[0]["b64_json"]
    output_payload = {"output": [{"type": "image_generation_call", "result": _tiny_png_b64()}]}
    assert _response_data(output_payload)[0]["result"]
    mixed_output = {
        "output": [
            {"type": "message", "content": "done"},
            {"type": "image_generation_call", "result": _tiny_png_b64()},
        ]
    }
    assert len(_response_data(mixed_output)) == 1
    assert _response_data(mixed_output)[0]["result"]
    assert _response_data({"data": [], "output": mixed_output["output"]})[0]["result"]

    class SDKLike:
        data = [{"b64_json": _tiny_png_b64()}]

    assert _response_data(SDKLike())[0]["b64_json"]

    class SDKOutputLike:
        class ImageItem:
            result = _tiny_png_b64()

        data = None
        output = [
            {"type": "message", "content": "done"},
            ImageItem(),
        ]

    assert _response_data(SDKOutputLike())[0].result

    class SDKEmptyDataOutputLike(SDKOutputLike):
        data = []

    assert _response_data(SDKEmptyDataOutputLike())[0].result

    try:
        _response_data("not json")
    except AIBackgroundError as exc:
        assert "纯文本" in str(exc)
    else:
        raise AssertionError("plain text response should fail with readable error")

    print("ai background response tests passed")


if __name__ == "__main__":
    main()
