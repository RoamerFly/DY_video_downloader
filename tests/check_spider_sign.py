import pytest
from pathlib import Path

from src.api import sign


S4 = b"Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe="


def _decode_custom_base64(value: str) -> bytes:
    table = {chr(byte): index for index, byte in enumerate(S4[:64])}
    output = bytearray()
    for offset in range(0, len(value), 4):
        chunk = value[offset : offset + 4]
        if len(chunk) < 4:
            chunk += "=" * (4 - len(chunk))
        padding = chunk.count("=")
        numbers = [0 if char == "=" else table[char] for char in chunk]
        packed = (
            (numbers[0] << 18)
            | (numbers[1] << 12)
            | (numbers[2] << 6)
            | numbers[3]
        )
        output.append((packed >> 16) & 0xFF)
        if padding < 2:
            output.append((packed >> 8) & 0xFF)
        if padding < 1:
            output.append(packed & 0xFF)
    return bytes(output)


def _spider_plain_block(a_bogus: str) -> bytes:
    decoded = _decode_custom_base64(a_bogus)
    return sign.rc4_encrypt(decoded[12:], b"y")


def _without_time_fields(block: bytes) -> bytes:
    mutable = bytearray(block)
    for index in (1, 11, 21, 26, 30, 31, 33, 34, 36, 37, 38, 39, 108):
        mutable[index] = 0
    return bytes(mutable)


@pytest.mark.parametrize(
    "body",
    [
        (
            "aweme_id=7640032041598198757&comment_send_celltime=3000"
            "&comment_video_celltime=2000&text=test&text_extra=%5B%5D"
        ),
        (
            "aweme_id=7640032041598198757&comment_send_celltime=3000"
            "&comment_video_celltime=2000&text=%E4%BD%A0%E5%A5%BD&text_extra=%5B%5D"
        ),
        (
            "aweme_id=7640032041598198757&comment_send_celltime=12345"
            "&comment_video_celltime=9876&reply_id=1&text=test&text_extra=%5B%5D"
        ),
    ],
)
def test_spider_publish_sign_matches_legacy_js_shape(body):
    pytest.importorskip("execjs")
    import execjs

    js_path = Path("src/api/static/dy_ab.js")
    if not js_path.exists():
        pytest.skip("legacy dy_ab.js fixture is not available")

    params = (
        "app_name=aweme&enter_from=discover&previous_page=discover"
        "&device_platform=webapp&aid=6383&channel=channel_pc_web"
        "&pc_client_type=1&update_version_code=170400&version_code=170400"
        "&version_name=17.4.0&cookie_enabled=true&screen_width=1707"
        "&screen_height=960&browser_language=zh-CN&browser_platform=Win32"
        "&browser_name=Edge&browser_version=125.0.0.0&browser_online=true"
        "&engine_name=Blink&engine_version=125.0.0.0&os_name=Windows"
        "&os_version=10&cpu_core_num=32&device_memory=8&platform=PC"
        "&downlink=10&effective_type=4g&round_trip_time=100&webid=123"
        "&msToken=abc"
    )
    ctx = execjs.compile(open(js_path, encoding="utf-8").read(), cwd="src/api/node_modules")
    legacy_js = ctx.call("get_ab", params, body)
    native = sign.sign_spider_publish(params, body)

    assert len(_decode_custom_base64(native)) == len(_decode_custom_base64(legacy_js)) == 121
    assert _without_time_fields(_spider_plain_block(native)) == _without_time_fields(
        _spider_plain_block(legacy_js)
    )
