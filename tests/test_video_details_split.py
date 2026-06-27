"""回归测试：作品详情拆分后的 UserManager 兼容入口。"""
import asyncio

from src.api.api import DouyinAPI
from src.downloader.downloader import DouyinDownloader
from src.user.user_manager import DouyinUserManager
from src.user.video_details import VideoDetailsService


def _manager():
    api = DouyinAPI("")
    return DouyinUserManager(api, DouyinDownloader(api))


def test_video_details_property_returns_bound_service():
    manager = _manager()

    service = manager.video_details

    assert isinstance(service, VideoDetailsService)
    assert service._mgr is manager
    assert manager.video_details is service


def test_get_media_info_delegates_to_video_details(monkeypatch):
    captured = {}

    def fake_get_media_info(self, post):
        captured["self"] = self
        captured["post"] = post
        return "video", [{"type": "video", "url": "https://example.test/a.mp4"}]

    monkeypatch.setattr(VideoDetailsService, "get_media_info", fake_get_media_info)

    manager = _manager()
    post = {"aweme_id": "1"}
    result = manager.get_media_info(post)

    assert captured["self"] is manager.video_details
    assert captured["post"] is post
    assert result == ("video", [{"type": "video", "url": "https://example.test/a.mp4"}])


def test_get_video_detail_delegates_to_video_details(monkeypatch):
    captured = {}

    async def fake_get_video_detail(self, aweme_id):
        captured["self"] = self
        captured["aweme_id"] = aweme_id
        return {"aweme_id": aweme_id}

    monkeypatch.setattr(VideoDetailsService, "get_video_detail", fake_get_video_detail)

    manager = _manager()
    result = asyncio.run(manager.get_video_detail("12345"))

    assert captured["self"] is manager.video_details
    assert captured["aweme_id"] == "12345"
    assert result == {"aweme_id": "12345"}


def test_parse_share_link_keeps_incomplete_fallback(monkeypatch):
    async def fake_get_video_detail(self, aweme_id):
        return None

    monkeypatch.setattr(VideoDetailsService, "get_video_detail", fake_get_video_detail)

    manager = _manager()
    result = asyncio.run(
        manager.parse_share_link("看这个 https://www.douyin.com/video/987654321?foo=bar")
    )

    assert result["aweme_id"] == "987654321"
    assert result["media_type"] == "unknown"
    assert result["_incomplete"] is True
