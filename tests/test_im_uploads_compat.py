"""回归测试：IM 图片上传拆分后的运行时兼容性。

覆盖两处历史回归点：
1. IMClient._get_im_image_upload_config 必须是绑定实例方法（不能残留 @staticmethod），
   否则 send_im_image_message 执行时会 TypeError。
2. DouyinAPI 上保留的旧 AWS helper 兼容入口必须可调用，委托到 im_uploads 模块。
"""

import asyncio

from src.api import im_uploads
from src.api.api import DouyinAPI


def test_im_image_upload_config_is_bound_method(monkeypatch):
    """实例调用 _get_im_image_upload_config 不应因 self 绑定问题 TypeError。"""
    # 避免真实网络请求：拦截 im_uploads 中的实际实现。
    captured = {}

    async def fake_get_im_image_upload_config(common_request_fn):
        captured["called"] = True
        return {"message": "fake"}, False

    monkeypatch.setattr(
        im_uploads,
        "get_im_image_upload_config",
        fake_get_im_image_upload_config,
    )

    api = DouyinAPI("")
    im = api.im

    # 绑定方法：访问时不应抛出 "missing 1 required positional argument: 'self'"
    bound = im._get_im_image_upload_config
    assert callable(bound)

    result, success = asyncio.run(bound())
    assert captured.get("called") is True
    assert success is False
    assert result == {"message": "fake"}


def test_douyin_api_legacy_aws_helpers_callable():
    """DouyinAPI 旧 AWS helper 兼容入口委托到 im_uploads，可正常调用。"""
    api = DouyinAPI("")

    # _aws_quote：纯函数，委托 im_uploads._aws_quote
    assert api._aws_quote("a b/c") == im_uploads._aws_quote("a b/c")

    # _aws_canonical_query：纯函数
    params = {"b": "2", "a": "1"}
    assert api._aws_canonical_query(params) == im_uploads._aws_canonical_query(params)

    # _aws_signing_key：纯函数
    key1 = api._aws_signing_key("secret", "20260101")
    key2 = im_uploads._aws_signing_key("secret", "20260101")
    assert key1 == key2 and isinstance(key1, bytes)

    # _aws_vod_auth_headers：实例方法，委托 im_uploads._aws_vod_auth_headers（不触网）
    signed_headers, headers = api._aws_vod_auth_headers(
        method="GET",
        query_params={"a": "1"},
        access_key_id="AKID",
        secret_access_key="SECRET",
        session_token="TOKEN",
        payload_hash="e3b0c442",
    )
    assert isinstance(signed_headers, str) and signed_headers
    assert isinstance(headers, dict)
    assert "x-amz-date" in headers
