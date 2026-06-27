"""请求错误 / 登录态 / 验证态判断逻辑。

从 DouyinAPI 中拆出的纯逻辑：构造验证提示、提取 API 错误消息、
识别登录态失效与验证码拦截。ApiErrors 持有 DouyinAPI 实例引用，
共享 cookie、headers 等状态。原方法保留为薄代理，确保外部调用兼容。
"""

import urllib.parse


class ApiErrors:
    """请求错误与登录/验证态判断服务。"""

    def __init__(self, api):
        self._api = api

    @property
    def debug_mode(self) -> bool:
        return self._api.debug_mode

    def _build_verify_hint(self, uri: str, params: dict, response=None) -> tuple[dict, bool]:
        """构造统一的验证提示结果。"""
        verify_url = 'https://www.douyin.com/'

        try:
            if uri and ('discover/search' in uri or 'general/search' in uri):
                keyword = params.get('keyword', '')
                if keyword:
                    verify_url = f"https://www.douyin.com/jingxuan/search/{urllib.parse.quote(str(keyword))}?type=user"
            elif uri and 'user/profile' in uri:
                sec_uid = params.get('sec_user_id', '')
                if sec_uid:
                    verify_url = f'https://www.douyin.com/user/{sec_uid}'
            elif uri and 'aweme/post' in uri:
                sec_uid = params.get('sec_user_id', '')
                if sec_uid:
                    verify_url = f'https://www.douyin.com/user/{sec_uid}'
            elif uri and 'aweme/favorite' in uri:
                verify_url = 'https://www.douyin.com/'
            elif uri and 'module/feed' in uri:
                verify_url = 'https://www.douyin.com/?recommend=1'
            elif uri and 'aweme/detail' in uri:
                aweme_id = params.get('aweme_id', '')
                if aweme_id:
                    verify_url = f'https://www.douyin.com/video/{aweme_id}'
            elif uri and 'comment/list' in uri:
                aweme_id = params.get('aweme_id', '')
                if aweme_id:
                    verify_url = f'https://www.douyin.com/video/{aweme_id}'
        except Exception:
            pass

        message = '需要完成验证后重试'
        if response is not None:
            try:
                if getattr(response, 'status_code', 0):
                    message = f'请求被拒绝（HTTP {response.status_code}），请完成验证后重试'
            except Exception:
                pass

        return {
            '_need_verify': True,
            '_verify_url': verify_url,
            'message': message,
        }, False

    def _extract_api_message(self, data: dict, fallback: str = '请求失败') -> str:
        if not isinstance(data, dict):
            return fallback

        for key in ('message', 'status_msg', 'log_pb'):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return fallback

    def _looks_like_logged_out_error(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False

        status_code = data.get('status_code')
        if status_code in (8, '8'):
            return True

        text_parts = []
        for key in ('message', 'status_msg', 'prompts', 'status_msg_extra'):
            value = data.get(key)
            if isinstance(value, str):
                text_parts.append(value)
            elif value is not None:
                text_parts.append(str(value))

        text = ' '.join(text_parts).lower()
        return any(
            token in text
            for token in (
                '用户未登录',
                '未登录',
                '登录态',
                '重新登录',
                'session expired',
                'not login',
                'not logged in',
                'login required',
            )
        )

    def _build_login_required_error(self, data: dict | None = None) -> dict:
        data = data if isinstance(data, dict) else {}
        api_message = self._extract_api_message(data, '用户未登录')
        return {
            '_need_login': True,
            'status_code': data.get('status_code'),
            'status_msg': data.get('status_msg', ''),
            'message': f'{api_message}，请在设置中重新登录并刷新 Cookie',
        }

    def _looks_like_login_or_verify_error(self, uri: str, data: dict) -> bool:
        if not isinstance(data, dict):
            return False

        text_parts = []
        for key in ('message', 'status_msg', 'prompts', 'status_msg_extra'):
            value = data.get(key)
            if isinstance(value, str):
                text_parts.append(value)
            elif value is not None:
                text_parts.append(str(value))

        filter_detail = data.get('filter_detail')
        if isinstance(filter_detail, dict):
            text_parts.extend(str(value) for value in filter_detail.values() if value is not None)

        text = ' '.join(text_parts).lower()
        if not text:
            return False

        if any(token in text for token in ('verify', 'captcha', 'passport', 'login')):
            return True
        if any(token in text for token in ('验证', '登录', 'cookie', '风控', '访问频繁', '请稍后重试')):
            return True

        sensitive_uri = any(
            fragment in (uri or '')
            for fragment in ('aweme/post', 'aweme/favorite', 'module/feed', 'tab/feed', 'user/profile', 'comment/list')
        )
        return sensitive_uri and '请求失败' in text
