import asyncio
import urllib.parse
import urllib.request
import os
import re
import json
import base64
import binascii
import sys
import random
import string
import time
import hmac
import hashlib
import logging
import uuid
from src.api import sign as douyin_sign
from src.api import douyin_im_proto
from src.api.http_client import (
    api_get as _api_get,
    api_post as _api_post,
    api_post_stateless as _api_post_stateless,
    get_api_session as _get_api_session,
    redact_headers as _redact_headers,
    redact_params as _redact_params,
    sign_spider_a_bogus as _sign_spider_a_bogus,
    splice_params as _splice_params,
)
from src.api.im_formatters import (
    collect_sec_uid_records,
    collect_spotlight_sec_user_ids,
    first_url,
    normalize_share_friends,
    share_sorted_sec_uids,
)
from src.api import temp_cookie
from src.api.im_client import IMClient
from src.api.comment_client import CommentClient

logger = logging.getLogger('api')

class DouyinAPI:
    """抖音API封装类"""
    
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.host = 'https://www.douyin.com'
        self._cached_webid = None
        self._webid_time = 0
        self._cached_csrf_token = None
        self._csrf_time = 0

        # 检查是否启用调试模式
        self.debug_mode = os.environ.get('DEBUG_MODE', '').lower() in ('true', '1', 'yes')
        if self.debug_mode:
            print("\033[93m[API] 调试模式已启用\033[0m")
        # 通用请求参数
        self.common_params = {
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'update_version_code': '0',
            'pc_client_type': '1',
            'version_code': '190600',
            'version_name': '19.6.0',
            'cookie_enabled': 'true',
            'screen_width': '1680',
            'screen_height': '1050',
            'browser_language': 'zh-CN',
            'browser_platform': 'MacIntel',
            'browser_name': 'Edge',
            'browser_version': '145.0.0.0',
            'browser_online': 'true',
            'engine_name': 'Blink',
            'engine_version': '145.0.0.0',
            'os_name': 'Mac OS',
            'os_version': '10.15.7',
            'cpu_core_num': '8',
            'device_memory': '8',
            'platform': 'PC',
            'downlink': '10',
            'effective_type': '4g',
            'round_trip_time': '50',
            'pc_libra_divert': 'Mac',
            'support_h265': '1',
            'support_dash': '1',
            'disable_rs': '0',
            'need_filter_settings': '1',
            'list_type': 'single',
        }

        # 通用请求头
        self.common_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "sec-ch-ua-platform": '"macOS"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua": '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
            "referer": "https://www.douyin.com/",
            "priority": "u=1, i",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "accept": "application/json, text/plain, */*",
        }

        # IM 客户端（延迟初始化）
        self._im_client: IMClient | None = None
        # 评论客户端（延迟初始化）
        self._comment_client: CommentClient | None = None

    @property
    def im(self) -> IMClient:
        """获取 IM 客户端实例（懒加载）。"""
        if self._im_client is None:
            self._im_client = IMClient(self)
        return self._im_client

    @property
    def comment(self) -> CommentClient:
        """获取评论客户端实例（懒加载）。"""
        if self._comment_client is None:
            self._comment_client = CommentClient(self)
        return self._comment_client

    async def _get_webid(self, headers: dict, url: str = '') -> str:
        """获取webid（缓存10分钟）"""
        import time
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
        import time
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

    async def common_request(self, uri: str, params: dict, headers: dict, host: str = None, skip_sign: bool = False, method: str = 'GET') -> tuple[dict, bool]:
        """
        请求 douyin
        :param uri: 请求路径
        :param params: 请求参数
        :param headers: 请求头
        :param host: 可选的自定义host
        :param skip_sign: 跳过a_bogus签名（部分接口不需要）
        :param method: 请求方法 ('GET' 或 'POST')
        :return: 返回数据和是否成功
        """
        base_host = host or self.host
        url = f'{base_host}{uri}'
        params.update(self.common_params)
        # 先应用通用头，再用自定义头覆盖
        merged_headers = dict(self.common_headers)
        merged_headers.update(headers)
        headers = merged_headers
        params = await self._deal_params(params, headers)

        if not skip_sign:
            query = '&'.join([f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items()])
            try:
                if 'reply' in uri:
                    a_bogus = douyin_sign.sign_reply(query, headers["User-Agent"])
                else:
                    a_bogus = douyin_sign.sign_detail(query, headers["User-Agent"])
            except Exception as e:
                if self.debug_mode:
                    print(f"\033[91m[API] 生成 a_bogus 失败: {e}\033[0m")
                return {
                    'status_code': -1,
                    'status_msg': '签名生成失败',
                    'message': f'签名生成失败: {e}',
                }, False
            params["a_bogus"] = a_bogus

        if self.debug_mode:
            print(f'\033[94m[API] 请求URL: {url}\033[0m')
            print(f'\033[94m[API] 请求方法: {method}\033[0m')
            print(f'\033[94m[API] 请求参数: {_redact_params(params)}\033[0m')
            print(f'\033[94m[API] 请求头: {_redact_headers(headers)}\033[0m')

        try:
            # 根据方法选择 GET 或 POST
            if method.upper() == 'POST':
                response = await asyncio.to_thread(
                    _api_post,
                    url,
                    data=params,
                    headers=headers,
                    timeout=(10, 30),
                )
            else:
                response = await asyncio.to_thread(
                    _api_get,
                    url,
                    params=params,
                    headers=headers,
                    timeout=(10, 30),
                )
        except requests.RequestException as e:
            if self.debug_mode:
                print(f'\033[91m[API] 网络请求异常: {e}\033[0m')
            return {
                'status_code': -1,
                'status_msg': '网络请求失败',
                'message': f'网络请求失败: {e}',
            }, False
        if self.debug_mode:
            print(f'\033[94m[API] 响应状态码: {response.status_code}\033[0m')
            print(f'\033[94m[API] 响应内容长度: {len(response.text)}, 前500字符: {response.text[:500]}\033[0m')

        response_content_type = response.headers.get('Content-Type', '').lower()
        response_url = getattr(response, 'url', '') or ''
        looks_like_verify = (
            response.status_code in (401, 403)
            or 'passport' in response_url.lower()
            or 'login' in response_url.lower()
            or ('text/html' in response_content_type and len(response.content) > 0)
        )

        if looks_like_verify:
            if self.debug_mode:
                print(f'\033[93m[API] 检测到验证/登录页响应，提示用户手动完成验证\033[0m')
            if response.status_code == 401:
                return self._build_login_required_error({
                    'status_code': response.status_code,
                    'status_msg': '用户未登录',
                }), False
            return self._build_verify_hint(uri, params, response)

        if response.status_code != 200 or len(response.content) == 0:
            if self.debug_mode:
                print(
                    f"\033[91m[API] 普通请求失败: status={response.status_code}, empty={len(response.content) == 0}\033[0m"
                )
            failure_payload = {
                'status_code': response.status_code,
                'status_msg': '请求失败',
                'message': '请求失败，请检查 Cookie 或稍后重试',
            }
            if self._looks_like_login_or_verify_error(uri, failure_payload):
                verify_hint, _ = self._build_verify_hint(uri, params, response)
                verify_hint.update(failure_payload)
                return verify_hint, False
            return failure_payload, False
            
        try:
            json_response = response.json()
        except Exception:
            try:
                text = response.text.lstrip()
                starts = [idx for idx in (text.find('{'), text.find('[')) if idx >= 0]
                if not starts:
                    raise ValueError('no json object found')
                decoder = json.JSONDecoder()
                json_response, _ = decoder.raw_decode(text[min(starts):])
            except Exception as e:
                if self.debug_mode:
                    print(f'\033[91m[API] JSON解析失败: {e}\033[0m')
                return {}, False
        except Exception as e:
            if self.debug_mode:
                print(f'\033[91m[API] JSON解析失败: {e}\033[0m')
            return {}, False

        # 检测验证码拦截 - 只有当user_list也为空时才认为需要验证
        nil_info = json_response.get('search_nil_info', {})
        user_list = json_response.get('user_list', [])
        if nil_info.get('search_nil_type') == 'verify_check' and len(user_list) == 0:
            if self.debug_mode:
                print(f'\033[91m[API] 触发滑块验证！返回验证标记由上层处理...\033[0m')

            # 返回验证标记和搜索验证URL，由上层打开浏览器让用户完成验证
            json_response['_need_verify'] = True
            keyword = params.get('keyword', '')
            if keyword:
                json_response['_verify_url'] = f"https://www.douyin.com/jingxuan/search/{urllib.parse.quote(str(keyword))}?type=user"
            return json_response, False

        # 检测视频详情接口返回空数据（可能是视频不存在或 API 限流）
        if uri and 'aweme/detail' in uri and json_response.get('aweme_detail') is None:
            filter_detail = json_response.get('filter_detail', {})
            filter_reason = filter_detail.get('filter_reason', 'unknown')
            if self.debug_mode:
                print(f'\033[91m[API] 视频详情接口返回空数据：filter_reason={filter_reason}\033[0m')
            return json_response, False

        if json_response.get('status_code', 0) != 0:
            if self.debug_mode:
                print(f'\033[91m[API] API返回错误: status_code={json_response.get("status_code")}, msg={json_response.get("status_msg", "")}\033[0m')
            if self._looks_like_logged_out_error(json_response):
                return self._build_login_required_error(json_response), False
            if self._looks_like_login_or_verify_error(uri, json_response):
                verify_hint, _ = self._build_verify_hint(uri, params, response)
                api_message = self._extract_api_message(json_response)
                verify_hint.update({
                    'status_code': json_response.get('status_code'),
                    'status_msg': json_response.get('status_msg', ''),
                    'message': f'{api_message}，请完成验证或重新获取 Cookie 后重试',
                })
                return verify_hint, False
            return json_response, False

        return json_response, True

    async def signed_form_action_request(
        self,
        uri: str,
        body_params: dict,
        headers: dict,
        host: str = None,
        query_overrides: dict | None = None,
    ) -> tuple[dict, bool]:
        """POST 动作接口：公共参数放 query 并签名，动作参数放 form body。"""
        base_host = host or self.host
        url = f'{base_host}{uri}'
        query_params = dict(self.common_params)
        if (
            'aweme/v1/web/commit/item/digg' in uri
            or 'aweme/v1/web/aweme/collect' in uri
            or 'aweme/v1/web/comment/digg' in uri
            or 'aweme/v1/web/commit/follow/user' in uri
        ):
            query_params.update({
                'update_version_code': '170400',
                'version_code': '170400',
                'version_name': '17.4.0',
                'browser_name': 'Chrome',
                'browser_version': '148.0.0.0',
                'engine_version': '148.0.0.0',
                'device_memory': '16',
            })
            if 'aweme/v1/web/commit/item/digg' in uri:
                uid_hash = self._relation_uid_hash()
                if uid_hash:
                    query_params['uid'] = uid_hash
        if query_overrides:
            query_params.update({str(key): str(value) for key, value in query_overrides.items()})
        merged_headers = dict(self.common_headers)
        merged_headers.update(headers or {})
        merged_headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        })
        merged_headers.update(self._relation_ticket_guard_headers(uri))
        is_relation_action = (
            'aweme/v1/web/commit/item/digg' in uri
            or 'aweme/v1/web/aweme/collect' in uri
            or 'aweme/v1/web/comment/digg' in uri
            or 'aweme/v1/web/commit/follow/user' in uri
        )
        dtrait = self._relation_dtrait()
        if is_relation_action and not dtrait:
            return {
                'status_code': -1,
                'status_msg': 'RELATION_DTRAIT_MISSING',
                'message': '点赞安全参数未采集完整，请重新登录 Cookie 后重试',
                '_security_blocked': True,
            }, False
        if dtrait:
            merged_headers['x-tt-session-dtrait'] = dtrait
        headers = merged_headers
        query_params = await self._deal_params(query_params, headers)
        query_params.update({
            'browser_name': 'Chrome',
            'browser_version': '148.0.0.0',
            'engine_version': '148.0.0.0',
            'device_memory': '16',
        })
        headers['x-secsdk-csrf-token'] = 'DOWNGRADE'
        # www.douyin.com → www-hj.douyin.com 是同站跨源，浏览器发送 same-site
        headers['sec-fetch-site'] = 'same-site'

        query = urllib.parse.urlencode(query_params)
        try:
            query_params['a_bogus'] = douyin_sign.sign_detail(query, headers["User-Agent"])
        except Exception as e:
            if self.debug_mode:
                print(f"\033[91m[API] 生成动作接口 a_bogus 失败: {e}\033[0m")
            return {
                'status_code': -1,
                'status_msg': '签名生成失败',
                'message': f'签名生成失败: {e}',
            }, False

        relation_uid = str(query_params.get('uid') or '')
        try:
            from src.config.config import Config
            signer_present = isinstance(Config.RELATION_SIGNER, dict)
        except Exception:
            signer_present = False
        logger.debug(
            'Douyin relation action request: path=%s query_keys=%s uid_present=%s uid_prefix=%s body_keys=%s signer_present=%s ticket_guard_cookie=%s ticket_guard_header=%s csrf_present=%s dtrait_present=%s',
            uri,
            ','.join(sorted(query_params.keys())),
            bool(relation_uid),
            relation_uid[:8],
            ','.join(sorted(body_params.keys())),
            signer_present,
            'bd_ticket_guard_client_data' in (self.cookie or ''),
            'bd-ticket-guard-client-data' in headers,
            'x-secsdk-csrf-token' in headers,
            'x-tt-session-dtrait' in headers,
        )

        if self.debug_mode:
            print(f'\033[94m[API] 动作请求URL: {url}\033[0m')
            print(f'\033[94m[API] 动作请求Query: {_redact_params(query_params)}\033[0m')
            print(f'\033[94m[API] 动作请求Body: {body_params}\033[0m')
            print(f'\033[94m[API] 动作请求头: {_redact_headers(headers)}\033[0m')

        try:
            response = await asyncio.to_thread(
                _api_post,
                url,
                params=query_params,
                data=body_params,
                headers=headers,
                timeout=(10, 30),
            )
        except requests.RequestException as e:
            return {
                'status_code': -1,
                'status_msg': '网络请求失败',
                'message': f'网络请求失败: {e}',
            }, False

        if response.status_code != 200 or len(response.content) == 0:
            logger.warning(
                'Douyin relation action rejected before JSON: path=%s http_status=%s content_length=%s headers=%s',
                uri,
                response.status_code,
                len(response.content or b''),
                {
                    'bd-ticket-guard-result': response.headers.get('bd-ticket-guard-result') or '',
                    'bd_passport_security_gateway': response.headers.get('bd_passport_security_gateway') or '',
                },
            )
            ticket_guard_result = response.headers.get('bd-ticket-guard-result') or ''
            passport_security_gateway = response.headers.get('bd_passport_security_gateway') or ''
            if response.status_code == 403 and (ticket_guard_result or passport_security_gateway == '1'):
                return {
                    'status_code': response.status_code,
                    'status_msg': 'SECURITY_GATEWAY_BLOCKED',
                    'message': (
                        f'抖音安全校验拒绝了本次操作（HTTP 403'
                        f'{", TicketGuard " + ticket_guard_result if ticket_guard_result else ""}），'
                        '当前 Cookie 仍会保留，请稍后重试，或先在抖音网页/客户端完成一次同类操作。'
                    ),
                    '_security_blocked': True,
                }, False
            return {
                'status_code': response.status_code,
                'status_msg': '请求失败',
                'message': '请求失败，请检查 Cookie 或稍后重试',
            }, False

        try:
            json_response = response.json()
        except Exception as e:
            return {
                'status_code': -1,
                'status_msg': 'JSON解析失败',
                'message': f'JSON解析失败: {e}',
            }, False

        logger.debug(
            'Douyin relation action response: path=%s status_code=%s status_msg=%s',
            uri,
            json_response.get('status_code', 0),
            json_response.get('status_msg') or json_response.get('message') or '',
        )

        if json_response.get('status_code', 0) != 0:
            status_code = json_response.get('status_code')
            api_message = self._extract_api_message(json_response)
            if status_code == 8 or '未登录' in api_message:
                return {
                    'status_code': status_code,
                    'status_msg': json_response.get('status_msg', ''),
                    'message': (
                        f'抖音动作接口未接受当前网页登录凭据（{api_message}），'
                        '当前 Cookie 仍会保留。请稍后重试，或先在抖音网页/客户端完成一次同类操作。'
                    ),
                    '_security_blocked': True,
                }, False
            if self._looks_like_logged_out_error(json_response):
                return self._build_login_required_error(json_response), False
            if self._looks_like_login_or_verify_error(uri, json_response):
                verify_hint, _ = self._build_verify_hint(uri, query_params, response)
                verify_hint.update({
                    'status_code': json_response.get('status_code'),
                    'status_msg': json_response.get('status_msg', ''),
                    'message': f'{api_message}，请完成验证或重新获取 Cookie 后重试',
                })
                return verify_hint, False
            return json_response, False

        return json_response, True

    # ---------- IM 薄代理（委托给 im_client.IMClient） ----------

    def _im_common_headers(self, path: str) -> dict:
        return self.im._im_common_headers(path)

    async def _request_im(self, uri: str, endpoint_params: dict | None = None, body_params: dict | None = None, method: str = 'GET') -> tuple[dict, bool]:
        return await self.im._request_im(uri, endpoint_params, body_params, method)

    async def get_im_spotlight_relation_sec_user_ids(self, limit: int = 500, include_all_users: bool = False) -> tuple[list[str], bool, dict]:
        return await self.im.get_im_spotlight_relation_sec_user_ids(limit, include_all_users)

    async def get_following_sec_user_ids(self, user_id: str, sec_uid: str, limit: int = 500, mutual_only: bool = False) -> tuple[list[str], bool, dict]:
        return await self.im.get_following_sec_user_ids(user_id, sec_uid, limit, mutual_only)

    async def get_im_share_friends(self, limit: int = 50) -> tuple[dict, bool]:
        return await self.im.get_im_share_friends(limit)

    async def get_im_user_info(self, sec_user_ids: list[str]) -> tuple[dict, bool]:
        return await self.im.get_im_user_info(sec_user_ids)

    async def get_im_user_active_status(self, sec_user_ids: list[str], conv_ids: list[str] | None = None) -> tuple[dict, bool]:
        return await self.im.get_im_user_active_status(sec_user_ids, conv_ids)

    async def get_im_device_id(self) -> tuple[str, bool, dict]:
        return await self.im.get_im_device_id()

    def _im_proto_signer(self) -> dict | None:
        return self.im._im_proto_signer()

    def _ecdsa_request_sign(self, value: str, private_key: str) -> tuple[str, str | None]:
        return self.im._ecdsa_request_sign(value, private_key)

    def _build_im_request_common_headers(self, signer: dict, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        return self.im._build_im_request_common_headers(signer, extra_headers)

    def _build_im_proto_request(
        self,
        *,
        cmd: int,
        body: bytes,
        request_sign: str,
        signer: dict,
        sdk_version: str = "1.1.3",
        build_number: str = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27",
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        return self.im._build_im_proto_request(
            cmd=cmd, body=body, request_sign=request_sign, signer=signer,
            sdk_version=sdk_version, build_number=build_number, extra_headers=extra_headers,
        )

    def _build_im_pc_proto_request(
        self,
        *,
        cmd: int,
        body: bytes,
        signer: dict,
        request_sign: str = '',
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        return self.im._build_im_pc_proto_request(
            cmd=cmd, body=body, signer=signer, request_sign=request_sign, extra_headers=extra_headers,
        )

    @staticmethod
    def _media_uri_from_url(url: str) -> str:
        return IMClient._media_uri_from_url(url)

    async def get_im_identity_security_token(self) -> tuple[dict, bool]:
        return await self.im.get_im_identity_security_token()

    async def _post_im_proto(self, url: str, payload: bytes, with_signed_query: bool = False) -> tuple[dict, bool]:
        return await self.im._post_im_proto(url, payload, with_signed_query)

    async def create_im_conversation(self, to_user_id: str | int) -> tuple[dict, bool]:
        return await self.im.create_im_conversation(to_user_id)

    async def send_im_text_message(self, to_user_id: str | int, content: str) -> tuple[dict, bool]:
        return await self.im.send_im_text_message(to_user_id, content)

    async def send_im_video_share_message(self, to_user_id: str | int, video: dict) -> tuple[dict, bool]:
        return await self.im.send_im_video_share_message(to_user_id, video)

    @staticmethod
    def _aws_quote(value) -> str:
        return IMClient._aws_quote(value)

    @classmethod
    def _aws_canonical_query(cls, params: dict) -> str:
        return IMClient._aws_canonical_query(params)

    @staticmethod
    def _aws_signing_key(secret_access_key: str, date_stamp: str, region: str = 'cn-north-1', service: str = 'vod') -> bytes:
        return IMClient._aws_signing_key(secret_access_key, date_stamp, region, service)

    def _aws_vod_auth_headers(
        self,
        method: str,
        query_params: dict,
        access_key_id: str,
        secret_access_key: str,
        session_token: str,
        payload_hash: str,
        extra_signed_headers: dict | None = None,
    ) -> tuple[str, dict]:
        return self.im._aws_vod_auth_headers(
            method, query_params, access_key_id, secret_access_key,
            session_token, payload_hash, extra_signed_headers,
        )

    async def _get_im_image_upload_config(self) -> tuple[dict, bool]:
        return await self.im._get_im_image_upload_config()

    async def _apply_im_image_upload(self, config: dict, file_size: int) -> tuple[dict, bool]:
        return await self.im._apply_im_image_upload(config, file_size)

    async def _upload_im_image_bytes(
        self,
        upload_address: dict,
        image_bytes: bytes,
        crc32_hex: str,
    ) -> tuple[dict, bool]:
        return await self.im._upload_im_image_bytes(upload_address, image_bytes, crc32_hex)

    async def _commit_im_image_upload(self, config: dict, session_key: str) -> tuple[dict, bool]:
        return await self.im._commit_im_image_upload(config, session_key)

    async def send_im_image_message(
        self,
        to_user_id: str | int,
        image_data_url: str,
        width: int = 0,
        height: int = 0,
        file_name: str = '',
        mime_type: str = '',
    ) -> tuple[dict, bool]:
        return await self.im.send_im_image_message(
            to_user_id, image_data_url, width, height, file_name, mime_type,
        )

    async def _send_im_content_message(
        self,
        to_user_id: str | int,
        msg_content: str,
        message_type: int = 7,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[dict, bool]:
        return await self.im._send_im_content_message(to_user_id, msg_content, message_type, extra_headers)

    @staticmethod
    def _normalize_im_messages(messages: list[dict]) -> list[dict]:
        return IMClient._normalize_im_messages(messages)

    async def _get_im_recent_user_messages(self, cursor: int = 0) -> tuple[dict, bool]:
        return await self.im._get_im_recent_user_messages(cursor)

    async def get_im_history_messages(
        self,
        cursor: int = 0,
        to_user_id: str | int | None = None,
        conversation_id: str | None = None,
        conversation_short_id: int | None = None,
        conversation_type: int = 1,
    ) -> tuple[dict, bool]:
        return await self.im.get_im_history_messages(
            cursor, to_user_id, conversation_id, conversation_short_id, conversation_type,
        )

    async def get_current_user(self, strict_profile: bool = False) -> tuple[dict, bool]:
        """获取当前登录用户，用于强校验 Cookie 是否仍被抖音服务端认可。"""
        resp, success = await self.common_request(
            '/aweme/v1/web/user/profile/self/',
            {},
            {'Referer': 'https://www.douyin.com/'},
            skip_sign=True,
        )

        if not success:
            if strict_profile:
                return resp, False
            logger.warning(
                'Douyin profile/self current user lookup failed, falling back to query/user: %s',
                resp.get('message') if isinstance(resp, dict) else resp,
            )
            return await self._get_current_user_from_query_user()

        user = resp.get('user') if isinstance(resp, dict) else None
        if not isinstance(user, dict) or not user:
            if strict_profile:
                return {
                    '_need_login': True,
                    'message': '登录态校验失败：抖音未返回当前用户，请重新登录获取 Cookie',
                }, False
            logger.warning('Douyin profile/self returned no user, falling back to query/user')
            return await self._get_current_user_from_query_user()

        return user, True

    async def _get_current_user_from_query_user(self) -> tuple[dict, bool]:
        resp, success = await self.common_request(
            '/aweme/v1/web/query/user',
            {'publish_video_strategy_type': '2'},
            {'Referer': 'https://www.douyin.com/discover'},
        )
        if not success:
            return resp, False
        uid = str(resp.get('user_uid') or resp.get('uid') or resp.get('id') or '').strip()
        if not uid:
            return {
                '_need_login': True,
                'message': '登录态校验失败：抖音未返回当前用户，请重新登录获取 Cookie',
                'raw': resp,
            }, False
        return {
            'uid': uid,
            'sec_uid': str(resp.get('sec_user_id') or resp.get('sec_uid') or '').strip(),
            'nickname': str(resp.get('nickname') or '抖音用户').strip() or '抖音用户',
            'avatar_thumb': {},
            'avatar_medium': {},
            'avatar_larger': {},
        }, True

    async def get_recommended_feed(self, count: int = 20, cursor: int = 0, feed_type: str = 'featured') -> tuple[dict, bool]:
        """获取推荐视频流
        
        Args:
            count: 获取数量
            cursor: 分页游标
            feed_type: featured=精选，recommended=推荐
            
        Returns:
            tuple[dict, bool]: (响应数据, 是否成功)
        """
        feed_type = str(feed_type or 'featured').strip().lower()
        if feed_type in ('recommend', 'tab', 'home', 'feed'):
            feed_type = 'recommended'
        if feed_type not in ('featured', 'recommended'):
            feed_type = 'featured'

        if self.debug_mode:
            print(f"\033[94m[API] 获取推荐视频流: type={feed_type}, count={count}, cursor={cursor}\033[0m")

        if feed_type == 'recommended':
            return await self.get_home_recommended_feed(count=count, cursor=cursor)
        
        # 准备请求参数 - 使用真实浏览器捕获的参数
        params = {
            'module_id': '3003101',  # 推荐模块ID
            'count': str(count),
            'pull_type': '0',  # 刷新类型
            'refresh_index': '1',  # 刷新索引
            'refer_type': '10',  # 引用类型
            'filterGids': '',
            'presented_ids': '',
            'refer_id': '',
            'tag_id': '',
            'use_lite_type': '2',
            'Seo-Flag': '0',
            'pre_log_id': '',
            'pre_item_ids': '',
            'pre_room_ids': '',
            'pre_item_from': 'sati',
            'xigua_user': '0',
            'awemePcRecRawData': '{"is_xigua_user":0,"danmaku_switch_status":0,"is_client":false}',
        }
        
        # 自定义请求头
        headers = {
            "Referer": "https://www.douyin.com/?recommend=1"
        }
        
        # 使用 POST 请求 - 重要！
        # 推荐接口需要 POST 请求，不是 GET
        resp = {}
        success = False
        for skip_sign in (False, True):
            try:
                resp, success = await self.common_request(
                    '/aweme/v2/web/module/feed/',
                    dict(params),
                    dict(headers),
                    skip_sign=skip_sign,
                    method='POST'  # 使用 POST 方法
                )
            except Exception as error:
                if self.debug_mode:
                    print(f"\033[91m[API] 推荐接口请求异常(skip_sign={skip_sign}): {error}\033[0m")
                resp, success = {'message': str(error)}, False

            if success or (isinstance(resp, dict) and resp.get('_need_verify')):
                break

        if success and resp.get('aweme_list'):
            aweme_count = len(resp.get('aweme_list', []))
            if self.debug_mode:
                print(f"\033[92m[API] 获取推荐视频成功: {aweme_count} 个\033[0m")

            # 检查是否有视频没有播放地址
            valid_count = 0
            for aweme in resp.get('aweme_list', []):
                video_data = aweme.get('video', {})
                play_addr = video_data.get('play_addr', {})
                if isinstance(play_addr, dict):
                    url_list = play_addr.get('url_list', [])
                    if url_list and url_list[0]:
                        valid_count += 1

            if self.debug_mode and valid_count < aweme_count:
                print(f"\033[93m[API] 有效视频: {valid_count}/{aweme_count}\033[0m")

            return resp, True

        if self.debug_mode:
            print(f"\033[91m[API] 获取推荐视频失败\033[0m")
            if resp:
                print(f"\033[91m[API] 响应: {resp}\033[0m")

        return resp, False

    async def get_home_recommended_feed(self, count: int = 20, cursor: int = 0) -> tuple[dict, bool]:
        """获取首页「推荐」视频流。"""
        refresh_index = max(1, int(cursor or 0) + 1)
        params = {
            'filterGids': '',
            'tag_id': '',
            'live_insert_type': '',
            'count': str(count),
            'refresh_index': str(refresh_index),
            'video_type_select': '1',
            'aweme_pc_rec_raw_data': json.dumps({
                'is_client': False,
                'ff_danmaku_status': 1,
                'danmaku_switch_status': 0,
                'is_dash_user': 1,
                'related_recommend': 1,
                'is_xigua_user': 0,
            }, ensure_ascii=False, separators=(',', ':')),
            'globalwid': '',
            'pull_type': '0' if cursor <= 0 else '2',
            'min_window': '0',
            'free_right': '0',
            'view_count': str(max(0, int(cursor or 0))),
            'plug_block': '0',
            'ug_source': '',
            'creative_id': '',
            'webcast_sdk_version': '170400',
            'webcast_version_code': '170400',
        }
        headers = {
            'Referer': 'https://www.douyin.com/?recommend=1',
        }

        resp = {}
        success = False
        for skip_sign in (False, True):
            try:
                resp, success = await self.common_request(
                    '/aweme/v1/web/tab/feed/',
                    dict(params),
                    dict(headers),
                    skip_sign=skip_sign,
                    method='GET',
                )
            except Exception as error:
                if self.debug_mode:
                    print(f"\033[91m[API] 首页推荐接口请求异常(skip_sign={skip_sign}): {error}\033[0m")
                resp, success = {'message': str(error)}, False

            if success or (isinstance(resp, dict) and resp.get('_need_verify')):
                break

        if success and resp.get('aweme_list'):
            resp = dict(resp)
            resp['cursor'] = refresh_index
            resp['aweme_list'] = await self._hydrate_home_recommended_aweme_details(resp.get('aweme_list') or [])
            return resp, True

        if self.debug_mode:
            print("\033[91m[API] 获取首页推荐视频失败\033[0m")
            if resp:
                print(f"\033[91m[API] 响应: {resp}\033[0m")

        return resp, False

    async def _hydrate_home_recommended_aweme_details(self, aweme_list: list) -> list:
        """用详情接口刷新首页推荐的播放地址。

        tab/feed 返回的 web-prime 直链在本地代理里容易 403；detail 接口会返回
        带播放 token 的 web 地址，播放器和下载器都更稳定。
        """
        if not aweme_list:
            return aweme_list

        semaphore = asyncio.Semaphore(4)

        async def fetch_detail(aweme: dict):
            aweme_id = str((aweme or {}).get('aweme_id') or '').strip()
            if not aweme_id:
                return None

            params = {
                'aweme_id': aweme_id,
                'aid': '1128',
                'version_name': '23.5.0',
                'device_platform': 'webapp',
                'os': 'windows',
            }

            async with semaphore:
                for skip_sign in (True, False):
                    try:
                        detail_resp, detail_success = await self.common_request(
                            '/aweme/v1/web/aweme/detail/',
                            dict(params),
                            {},
                            skip_sign=skip_sign,
                            method='GET',
                        )
                    except Exception as error:
                        if self.debug_mode:
                            print(f"\033[93m[API] 推荐详情水合异常: aweme_id={aweme_id}, skip_sign={skip_sign}, error={error}\033[0m")
                        continue

                    if isinstance(detail_resp, dict) and (detail_resp.get('_need_verify') or detail_resp.get('_need_login')):
                        return None
                    detail_aweme = (detail_resp or {}).get('aweme_detail') if detail_success else None
                    if isinstance(detail_aweme, dict) and detail_aweme.get('video'):
                        if not detail_aweme.get('aweme_id'):
                            detail_aweme['aweme_id'] = aweme_id
                        return detail_aweme

            return None

        hydrated = await asyncio.gather(
            *(fetch_detail(aweme) for aweme in aweme_list if isinstance(aweme, dict)),
            return_exceptions=True,
        )
        result = []
        detail_index = 0
        replaced_count = 0
        for aweme in aweme_list:
            if not isinstance(aweme, dict):
                result.append(aweme)
                continue
            detail = hydrated[detail_index] if detail_index < len(hydrated) else None
            detail_index += 1
            if isinstance(detail, dict):
                result.append(detail)
                replaced_count += 1
            else:
                result.append(aweme)

        if self.debug_mode and replaced_count:
            print(f"\033[92m[API] 推荐详情水合完成: {replaced_count}/{len(result)}\033[0m")
        return result

    async def set_comment_liked(self, aweme_id: str, comment_id: str, liked: bool, level: int = 1) -> tuple[dict, bool]:
        return await self.comment.set_comment_liked(aweme_id, comment_id, liked, level)

    async def publish_comment(
        self,
        aweme_id: str,
        text: str,
        reply_id: str = '',
        reply_to_reply_id: str = '',
    ) -> tuple[dict, bool]:
        return await self.comment.publish_comment(aweme_id, text, reply_id, reply_to_reply_id)

    async def get_comments(self, aweme_id: str, count: int = 20, cursor: int = 0) -> tuple[dict, bool]:
        return await self.comment.get_comments(aweme_id, count, cursor)

    async def get_comment_replies(self, aweme_id: str, comment_id: str, count: int = 6, cursor: int = 0) -> tuple[dict, bool]:
        return await self.comment.get_comment_replies(aweme_id, comment_id, count, cursor)

    async def get_temp_cookie(self) -> dict:
        return await temp_cookie.get_temp_cookie(self.common_headers, self.debug_mode)

    async def _get_temp_cookie_http(self) -> str:
        return await temp_cookie.get_temp_cookie_http(self.common_headers, self.debug_mode)

    @staticmethod
    def get_browser_cookies() -> dict:
        return temp_cookie.get_browser_cookies()
