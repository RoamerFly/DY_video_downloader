"""回归测试：评论动作拆分后的委托链兼容性。

覆盖评论点赞/发布拆分到 CommentActions 后，旧公开入口仍可调用且
正确委托，避免类似 IM 图片上传拆分时出现的委托断点。
"""
import asyncio

from src.api.api import DouyinAPI
from src.api.comment_actions import CommentActions
from src.api.comment_client import CommentClient


def test_comment_client_actions_property_returns_bound_service():
    """CommentClient.actions 返回绑定到该 client 的 CommentActions 实例。"""
    api = DouyinAPI("")
    client = api.comment
    actions = client.actions
    assert isinstance(actions, CommentActions)
    assert actions._client is client
    # 懒加载稳定：再次访问返回同一实例
    assert client.actions is actions


def test_set_comment_liked_delegates_to_actions(monkeypatch):
    """api.set_comment_liked 经 comment 委托到 CommentActions.set_comment_liked。"""
    captured = {}

    async def fake_set_comment_liked(self, aweme_id, comment_id, liked, level=1):
        captured["args"] = (aweme_id, comment_id, liked, level)
        return {"message": "ok"}, True

    monkeypatch.setattr(CommentActions, "set_comment_liked", fake_set_comment_liked)

    api = DouyinAPI("")
    result, success = asyncio.run(
        api.set_comment_liked("aweme1", "cid1", True, level=2)
    )

    assert captured["args"] == ("aweme1", "cid1", True, 2)
    assert success is True
    assert result == {"message": "ok"}


def test_publish_comment_delegates_to_actions(monkeypatch):
    """api.publish_comment 经 comment 委托到 CommentActions.publish_comment。"""
    captured = {}

    async def fake_publish_comment(self, aweme_id, text, reply_id="", reply_to_reply_id=""):
        captured["args"] = (aweme_id, text, reply_id, reply_to_reply_id)
        return {"message": "published"}, True

    monkeypatch.setattr(CommentActions, "publish_comment", fake_publish_comment)

    api = DouyinAPI("")
    result, success = asyncio.run(
        api.publish_comment("aweme2", "hello", reply_id="r1", reply_to_reply_id="r2")
    )

    assert captured["args"] == ("aweme2", "hello", "r1", "r2")
    assert success is True
    assert result == {"message": "published"}


def test_comment_client_publish_comment_direct_delegate(monkeypatch):
    """CommentClient.publish_comment 直接委托到 actions，签名保持兼容。"""
    captured = {}

    async def fake_publish_comment(self, aweme_id, text, reply_id="", reply_to_reply_id=""):
        captured["called"] = True
        captured["args"] = (aweme_id, text, reply_id, reply_to_reply_id)
        return {}, False

    monkeypatch.setattr(CommentActions, "publish_comment", fake_publish_comment)

    client = CommentClient(DouyinAPI(""))
    result, success = asyncio.run(
        client.publish_comment("a", "t", reply_id="r")
    )

    assert captured["called"] is True
    assert captured["args"] == ("a", "t", "r", "")
    assert success is False
