"""下载任务创建路由回归测试。

回归 35c7142：download_tasks.py 拆分搬迁时遗漏 dr = _deps() 初始化与
部分注入依赖前缀，导致 /api/download_single_video 与 /api/download_user_video
在参数校验阶段就抛 NameError 返回 500。这里固定两条路由的参数校验行为，
确保拆分变量遗漏不会让核心下载入口 500。
"""
from src.web.web_app import app


def test_download_single_video_missing_aweme_id_returns_400_not_500():
    """空 body 应返回 400（aweme_id 为空），不得因拆分遗漏返回 500。"""
    client = app.test_client()
    response = client.post("/api/download_single_video", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "作品ID" in payload["message"]


def test_download_user_video_missing_sec_uid_returns_400_not_500():
    """空 body 应返回 400（sec_uid 为空），不得因拆分遗漏返回 500。"""
    client = app.test_client()
    response = client.post("/api/download_user_video", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "sec_uid" in payload["message"]
