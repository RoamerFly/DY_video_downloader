"""Ticket Guard / 签名 / 鉴权 headers 辅助逻辑。

从 DouyinAPI 中拆出的请求签名与安全头构造逻辑：Cookie 解析、msToken、
s_v_web_id、webid/csrf token 获取、Ticket Guard 头、关系动作签名、
dtrait/uid_hash 以及请求参数装配。TicketGuard 持有 DouyinAPI 实例引用，
共享 cookie、headers 等状态。原方法保留为薄代理，确保外部与子模块调用兼容。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import re
import string
import time
import urllib.parse

from src.api import sign as douyin_sign
from src.api.http_client import api_get as _api_get, get_api_session as _get_api_session


class TicketGuard:
    """Ticket Guard / 签名 / 鉴权 headers 辅助服务。"""

    def __init__(self, api):
        self._api = api

    @property
    def cookie(self) -> str:
        return self._api.cookie

    @property
    def debug_mode(self) -> bool:
        return self._api.debug_mode

    @property
    def _cached_webid(self):
        return self._api._cached_webid

    @_cached_webid.setter
    def _cached_webid(self, value):
        self._api._cached_webid = value

    @property
    def _webid_time(self):
        return self._api._webid_time

    @_webid_time.setter
    def _webid_time(self, value):
        self._api._webid_time = value

    @property
    def _cached_csrf_token(self):
        return self._api._cached_csrf_token

    @_cached_csrf_token.setter
    def _cached_csrf_token(self, value):
        self._api._cached_csrf_token = value

    @property
    def _csrf_time(self):
        return self._api._csrf_time

    @_csrf_time.setter
    def _csrf_time(self, value):
        self._api._csrf_time = value

    async def _get_webid(self, headers: dict, url: str = '') -> str:
        """获取webid（缓存10分钟）"""
        if self._cached_webid and (time.time() - self._webid_time) < 600:
            return self._cached_webid
        try:
            url = url or 'https://www.douyin.com/?recommend=1'
            h = headers.copy()
            h['sec-fetch-dest'] = 'document'
            h['sec-fetch-mode'] = 'navigate'
            h['sec-fetch-site'] = 'none'
            h['accept'] = 'text/html,application/xhtml+xml'
            h['upgrade-insecure-requests'] = '1'
            if self.cookie:
                h['Cookie'] = self.cookie

            response = await asyncio.to_thread(_api_get, url, headers=h, timeout=10, verify=False)
            if self.debug_mode:
                print(f"\033[93m[API] _get_webid 响应状态: {response.status_code}, 内容长度: {len(response.text)}\033[0m")
            if response.status_code != 200 or not response.text:
                if self.debug_mode:
                    print(f"\033[91m[API] 获取webid失败: {response.status_code}\033[0m")
                return None

            # Try multiple patterns
            for pattern in [
                r'\\"user_unique_id\\":\\"(\d+)\\"',
                r'"user_unique_id":"(\d+)"',
                r'"webid":"(\d+)"',
                r'webid=(\d+)',
            ]:
                match = re.search(pattern, response.text)
                if match:
                    webid = match.group(1)
                    self._cached_webid = webid
                    self._webid_time = time.time()
                    if self.debug_mode:
                        print(f"\033[93m[API] 获取到webid: {webid}\033[0m")
                    return webid

            if self.debug_mode:
                print(f"\033[91m[API] 未能从页面提取webid\033[0m")
        except Exception as e:
            if self.debug_mode:
                print(f"\033[91m[API] 获取webid异常: {e}\033[0m")
        return None

    def _generate_fake_webid(self, random_length: int = 19) -> str:
        """生成 Spider 同款兜底 webid。"""
        return ''.join(random.choices(string.digits, k=random_length))

    async def _get_csrf_token(self, headers: dict, force_refresh: bool = False) -> str:
        """获取抖音动作接口需要的 csrf token（缓存10分钟）。"""
        if not force_refresh and self._cached_csrf_token and (time.time() - self._csrf_time) < 600:
            return self._cached_csrf_token

        h = dict(headers or {})
        h.update({
            'accept': '*/*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://www.douyin.com/?recommend=1',
            'sec-ch-ua': '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'x-secsdk-csrf-request': '1',
            'x-secsdk-csrf-version': '1.2.22',
        })
        h.pop('content-type', None)
        h.pop('Content-Type', None)
        try:
            response = await asyncio.to_thread(
                _get_api_session().head,
                'https://www.douyin.com/service/2/abtest_config/',
                headers=h,
                timeout=(10, 30),
            )
            raw_token = response.headers.get('x-ware-csrf-token') or response.headers.get('X-Ware-Csrf-Token') or ''
            parts = [part.strip() for part in raw_token.split(',')]
            token = parts[1] if len(parts) > 1 and parts[1] else next((part for part in parts if len(part) > 16), '')
            if token:
                self._cached_csrf_token = token
                self._csrf_time = time.time()
                return token
        except Exception as e:
            if self.debug_mode:
                print(f"\033[91m[API] 获取 csrf token 失败: {e}\033[0m")

        return ''

    async def _deal_params(self, params: dict, headers: dict) -> dict:
        """处理请求参数"""
        try:
            # 添加cookie到headers
            if self.cookie:
                headers['Cookie'] = self.cookie

            cookie = headers.get('cookie') or headers.get('Cookie')
            if not cookie:
                return params

            cookie_dict = self._cookies_to_dict(cookie)

            # 从cookie中提取参数
            params['msToken'] = self._get_ms_token()
            params['screen_width'] = cookie_dict.get('dy_swidth', params.get('screen_width', 1680))
            params['screen_height'] = cookie_dict.get('dy_sheight', params.get('screen_height', 1050))
            params['cpu_core_num'] = cookie_dict.get('device_web_cpu_core', params.get('cpu_core_num', 8))
            params['device_memory'] = cookie_dict.get('device_web_memory_size', params.get('device_memory', 8))
            s_v_web_id = cookie_dict.get('s_v_web_id') or self._generate_s_v_web_id()
            params['verifyFp'] = s_v_web_id
            params['fp'] = s_v_web_id

            # 从cookie中提取uifid并添加到header和参数
            uifid = cookie_dict.get('UIFID', '')
            if uifid:
                headers['uifid'] = uifid
                params['uifid'] = uifid

            # Spider 在提取失败时会生成 19 位数字 webid，动作接口不能省略它。
            params['webid'] = await self._get_webid(headers) or self._generate_fake_webid()

            return params
        except Exception as e:
            if self.debug_mode:
                print(f"\033[91m[API] 处理参数失败: {e}\033[0m")
            return params

    def _cookies_to_dict(self, cookie_str: str) -> dict:
        """将cookie字符串转换为字典"""
        cookie_dict = {}
        if not cookie_str:
            return cookie_dict

        try:
            for item in cookie_str.split(';'):
                if '=' in item:
                    key, value = item.strip().split('=', 1)
                    cookie_dict[key] = value
        except Exception as e:
            if self.debug_mode:
                print(f"\033[91m[API] 解析cookie失败: {e}\033[0m")

        return cookie_dict

    def _ticket_guard_headers_from_cookie(self) -> dict:
        cookie_dict = self._cookies_to_dict(self.cookie)
        raw_legacy_client_data = cookie_dict.get('bd_ticket_guard_client_data') or ''
        raw_client_data_v2 = cookie_dict.get('bd_ticket_guard_client_data_v2') or ''
        raw_client_data = raw_client_data_v2 or cookie_dict.get('bd_ticket_guard_client_data') or ''
        if not raw_client_data:
            return {}

        headers = {}
        if raw_client_data_v2 and raw_legacy_client_data:
            try:
                legacy_decoded = urllib.parse.unquote(raw_legacy_client_data)
                legacy_payload = json.loads(base64.b64decode(legacy_decoded).decode('utf-8'))
                for key, value in legacy_payload.items():
                    if key.startswith('bd-ticket-guard-'):
                        headers[key] = str(value)
            except Exception:
                pass

        try:
            decoded_cookie = urllib.parse.unquote(raw_client_data)
            payload = json.loads(base64.b64decode(decoded_cookie).decode('utf-8'))
        except Exception:
            return {}

        if raw_client_data_v2:
            headers['bd-ticket-guard-client-data'] = decoded_cookie
        for key, value in payload.items():
            if key.startswith('bd-ticket-guard-'):
                headers[key] = str(value)
        if 'bd-ticket-guard-ree-public-key' not in headers and payload.get('ree_public_key'):
            headers['bd-ticket-guard-ree-public-key'] = str(payload['ree_public_key'])
        headers.setdefault('bd-ticket-guard-web-sign-type', '1' if raw_client_data_v2 else '0')
        return headers

    def _decode_relation_ecdh_key(self, value: str) -> bytes | None:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            if len(text) == 64 and re.fullmatch(r'[0-9a-fA-F]+', text):
                return bytes.fromhex(text)
            return base64.b64decode(text)
        except Exception:
            return None

    def _relation_ticket_guard_headers(self, path: str) -> dict:
        try:
            from src.config.config import Config
            signer = Config.RELATION_SIGNER if isinstance(Config.RELATION_SIGNER, dict) else None
        except Exception:
            signer = None

        if not signer:
            return self._ticket_guard_headers_from_cookie()

        ticket = str(signer.get('ticket') or '').strip()
        ts_sign = str(signer.get('ts_sign') or '').strip()
        public_key = str(signer.get('public_key') or signer.get('ree_public_key') or '').strip()
        ecdh_key = self._decode_relation_ecdh_key(str(signer.get('ecdh_key') or ''))
        if not ticket or not ts_sign or not public_key or not ecdh_key:
            if self.debug_mode:
                print('\033[93m[API] 关系动作 signer 不完整，降级使用 Cookie 中的 TicketGuard 头\033[0m')
            return self._ticket_guard_headers_from_cookie()

        timestamp = int(time.time())
        sign_data = f'ticket={ticket}&path={path}&timestamp={timestamp}'
        req_sign = base64.b64encode(
            hmac.new(ecdh_key, sign_data.encode('utf-8'), hashlib.sha256).digest()
        ).decode('ascii')
        client_data = base64.b64encode(json.dumps({
            'ts_sign': ts_sign,
            'req_content': 'ticket,path,timestamp',
            'req_sign': req_sign,
            'timestamp': timestamp,
        }, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).decode('ascii')

        return {
            'bd-ticket-guard-ree-public-key': public_key,
            'bd-ticket-guard-web-version': '2',
            'bd-ticket-guard-web-sign-type': '1',
            'bd-ticket-guard-version': '2',
            'bd-ticket-guard-iteration-version': '1',
            'bd-ticket-guard-client-data': client_data,
        }

    def _spider_ticket_guard_headers(self, path: str) -> dict:
        """TicketGuard headers exactly like Douyin_Spider Header.with_bd."""
        try:
            from src.config.config import Config
            signer = Config.RELATION_SIGNER if isinstance(Config.RELATION_SIGNER, dict) else None
        except Exception:
            signer = None

        if not signer:
            return {}

        ticket = str(signer.get('ticket') or '').strip()
        ts_sign = str(signer.get('ts_sign') or '').strip()
        private_key = str(signer.get('private_key') or '').strip().replace('\\n', '\n')
        if not ticket or not ts_sign or not private_key:
            return {}

        timestamp = int(time.time())
        sign_data = f'ticket={ticket}&path={path}&timestamp={timestamp}'
        client_data = base64.urlsafe_b64encode(json.dumps({
            'ts_sign': ts_sign,
            'req_content': 'ticket,path,timestamp',
            'req_sign': douyin_sign.get_req_sign(sign_data, private_key),
            'timestamp': timestamp,
        }, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).decode('utf-8')

        return {
            'bd-ticket-guard-client-data': client_data,
            'bd-ticket-guard-iteration-version': '1',
            'bd-ticket-guard-ree-public-key': douyin_sign.get_ree_key(private_key),
            'bd-ticket-guard-version': '2',
            'bd-ticket-guard-web-version': '1',
        }

    def _relation_uid_hash(self) -> str:
        try:
            from src.config.config import Config
            signer = Config.RELATION_SIGNER if isinstance(Config.RELATION_SIGNER, dict) else None
        except Exception:
            signer = None
        uid = str((signer or {}).get('uid') or '').strip()
        if not uid:
            cookie_dict = self._cookies_to_dict(self.cookie)
            uid = str(cookie_dict.get('uid_tt') or cookie_dict.get('uid_tt_ss') or '').strip()
        if not uid:
            return ''
        if len(uid) == 32 and re.fullmatch(r'[0-9a-fA-F]+', uid):
            return uid.lower()
        return hashlib.md5(uid.encode('utf-8')).hexdigest()

    def _relation_dtrait(self) -> str:
        try:
            from src.config.config import Config
            signer = Config.RELATION_SIGNER if isinstance(Config.RELATION_SIGNER, dict) else None
        except Exception:
            signer = None
        dtrait = str((signer or {}).get('dtrait') or '').strip()
        if dtrait:
            return dtrait

        for key in ('DOUYIN_RELATION_DTRAIT', 'DOUYIN_DTRAIT', 'X_TT_SESSION_DTRAIT'):
            dtrait = str(os.environ.get(key) or '').strip()
            if dtrait:
                return dtrait

        try:
            config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'sign_config.json'))
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                dtrait = str(data.get('x_tt_session_dtrait') or data.get('dtrait') or '').strip()
                if dtrait:
                    return dtrait
        except Exception:
            pass

        return ''

    def _get_ms_token(self) -> str:
        """生成msToken"""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=107))

    def _generate_s_v_web_id(self) -> str:
        """生成s_v_web_id (verifyFp)"""
        charset = string.ascii_lowercase + string.digits
        random_str = ''.join(random.choices(charset, k=16))
        return f"verify_0{random_str}"
