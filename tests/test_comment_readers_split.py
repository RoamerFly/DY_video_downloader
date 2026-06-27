"""回归测试：评论读取拆分后的委托链兼容性。

覆盖评论列表/回复读取拆分到 CommentReaders 后，旧公开入口仍可调用且
正确委托。
"""
import asyncio

from src.api.api import DouyinAPI
from src.api.comment_client import CommentClient
from src.api.comment_readers import CommentReaders


def test_comment_client_readers_property_returns_bound_service():
    """CommentClient.readers 返回绑定到该 client 的 CommentReaders 实例。"""
    api = DouyinAPI("")
    client = api.comment
    readers = client.readers
    assert isinstance(readers, CommentReaders)
    assert readers._client is client
    assert client.readers is readers  # 懒加载稳定


def test_get_comments_delegates_to_readers(monkeypatch):
    """api.get_comments 经 comment 委托到 CommentReaders.get_comments。"""
    captured = {}

    async def fake_get_comments(self, aweme_id, count=20, cursor=0):
        captured["args"] = (aweme_id, count, cursor)
        return {"comments": []}, True

    monkeypatch.setattr(CommentReaders, "get_comments", fake_get_comments)

    api = DouyinAPI("")
    result, success = asyncio.run(api.get_comments("aweme1", count=15, cursor=3))

    assert captured["args"] == ("aweme1", 15, 3)
    assert success is True
    assert result == {"comments": []}


def test_get_comment_replies_delegates_to_readers(monkeypatch):
    """api.get_comment_replies 经 comment 委托到 CommentReaders.get_comment_replies。"""
    captured = {}

    async def fake_get_comment_replies(self, aweme_id, comment_id, count=6, cursor=0):
        captured["args"] = (aweme_id, comment_id, count, cursor)
        return {"comments": []}, True

    monkeypatch.setattr(CommentReaders, "get_comment_replies", fake_get_comment_replies)

    api = DouyinAPI("")
    result, success = asyncio.run(
        api.get_comment_replies("aweme2", "cid2", count=8, cursor=5)
    )

    assert captured["args"] == ("aweme2", "cid2", 8, 5)
    assert success is True
    assert result == {"comments": []}


def test_comment_client_get_comment_replies_direct_delegate(monkeypatch):
    """CommentClient.get_comment_replies 直接委托到 readers，签名保持兼容。"""
    captured = {}

    async def fake_get_comment_replies(self, aweme_id, comment_id, count=6, cursor=0):
        captured["args"] = (aweme_id, comment_id, count, cursor)
        return {}, False

    monkeypatch.setattr(CommentReaders, "get_comment_replies", fake_get_comment_replies)

    client = CommentClient(DouyinAPI(""))
    _, success = asyncio.run(client.get_comment_replies("a", "c"))

    assert captured["args"] == ("a", "c", 6, 0)
    assert success is False
