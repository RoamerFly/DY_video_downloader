"""评论动作逻辑拆分模块。

将 CommentClient 中评论点赞、发布评论等动作相关方法抽取到独立模块。
通过 CommentActions 类持有 CommentClient 实例引用，共享 cookie、headers、
公共请求方法等状态。CommentClient 保留原方法签名作为薄代理，保持对外
接口不变。
"""
import asyncio
import logging
import random
import urllib.parse

import requests

from src.api import sign as douyin_sign
from src.api.http_client import (
    api_post_stateless as _api_post_stateless,
    get_api_session as _get_api_session,
    sign_spider_a_bogus as _sign_spider_a_bogus,
    splice_params as _splice_params,
)

logger = logging.getLogger('api.comment')


class CommentActions:
    """评论动作服务，封装评论点赞、发布评论等写操作。"""

    def __init__(self, client):
        """
        Args:
            client: CommentClient 实例，用于共享 cookie、headers、公共请求方法等。
        """
        self._client = client

    async def set_comment_liked(self, aweme_id: str, comment_id: str, liked: bool, level: int = 1) -> tuple[dict, bool]:
        """点赞或取消点赞评论。"""
        aweme_id = str(aweme_id or '').strip()
        comment_id = str(comment_id or '').strip()
        if not aweme_id:
            return {'message': '作品ID不能为空'}, False
        if not comment_id:
            return {'message': '评论ID不能为空'}, False

        return await self._client.signed_form_action_request(
            '/aweme/v1/web/comment/digg',
            {},
            {
                'Referer': 'https://www.douyin.com/',
                'Origin': 'https://www.douyin.com',
                'sec-fetch-mode': 'cors',
                'sec-fetch-dest': 'empty',
                'priority': 'u=1, i',
            },
            host='https://www-hj.douyin.com',
            query_overrides={
                'cid': comment_id,
                'aweme_id': aweme_id,
                'digg_type': '1' if liked else '2',
                'channel_id': '0',
                'app_name': 'aweme',
                'item_type': '0',
                'level': str(max(1, int(level or 1))),
                'enter_from': 'discover',
                'previous_page': 'discover',
            },
        )

    async def publish_comment(
        self,
        aweme_id: str,
        text: str,
        reply_id: str = '',
        reply_to_reply_id: str = '',
    ) -> tuple[dict, bool]:
        """发布一级评论或回复评论，按 Douyin_Spider 的 comment_publish 请求形态构造。"""
        aweme_id = str(aweme_id or '').strip()
        text = str(text or '').strip()
        reply_id = str(reply_id or '').strip()
        reply_to_reply_id = str(reply_to_reply_id or '').strip()
        if not aweme_id:
            return {'message': '作品ID不能为空'}, False
        if not text:
            return {'message': '评论内容不能为空'}, False

        current_user, logged_in = await self._client.get_current_user(strict_profile=True)
        if not logged_in:
            return self._client._build_login_required_error(current_user if isinstance(current_user, dict) else None), False

        try:
            _get_api_session().cookies.clear()
        except Exception:
            pass

        uri = '/aweme/v1/web/comment/publish'
        url = f'https://www.douyin.com{uri}'
        referer = f'https://www.douyin.com/discover?modal_id={aweme_id}'
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-ch-ua': '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'priority': 'u=1, i',
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://www.douyin.com',
            'referer': referer,
        }
        query_params = {
            'app_name': 'aweme',
            'enter_from': 'discover',
            'previous_page': 'discover',
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'pc_client_type': '1',
            'update_version_code': '170400',
            'version_code': '170400',
            'version_name': '17.4.0',
            'cookie_enabled': 'true',
            'screen_width': '1707',
            'screen_height': '960',
            'browser_language': 'zh-CN',
            'browser_platform': 'Win32',
            'browser_name': 'Edge',
            'browser_version': '125.0.0.0',
            'browser_online': 'true',
            'engine_name': 'Blink',
            'engine_version': '125.0.0.0',
            'os_name': 'Windows',
            'os_version': '10',
            'cpu_core_num': '32',
            'device_memory': '8',
            'platform': 'PC',
            'downlink': '10',
            'effective_type': '4g',
            'round_trip_time': '100',
        }
        cookie_dict = self._client._cookies_to_dict(self._client.cookie)
        query_params['webid'] = await self._client._get_webid(headers, referer) or self._client._generate_fake_webid()
        query_params['msToken'] = cookie_dict.get('msToken') or self._client._get_ms_token()
        cookie_dict['msToken'] = query_params['msToken']
        cookie_str_with_ms_token = '; '.join([f'{key}={value}' for key, value in cookie_dict.items()])
        headers.update(self._client._spider_ticket_guard_headers(uri))
        csrf_headers = dict(headers)
        csrf_headers['cookie'] = cookie_str_with_ms_token
        csrf_token = await self._client._get_csrf_token(csrf_headers, force_refresh=True)
        if csrf_token:
            headers['x-secsdk-csrf-token'] = csrf_token
        verify_fp = cookie_dict.get('s_v_web_id') or self._client._generate_s_v_web_id()

        body_params = {
            'aweme_id': aweme_id,
            'comment_send_celltime': random.randint(1000, 20000),
            'comment_video_celltime': random.randint(1000, 20000),
        }
        if reply_id:
            body_params['reply_id'] = reply_id
        body_params['text'] = text
        body_params['text_extra'] = []

        query = _splice_params(query_params)
        body_query = _splice_params(body_params)
        try:
            query_params['a_bogus'] = _sign_spider_a_bogus(query, body_query)
        except Exception as e:
            return {
                'status_code': -1,
                'status_msg': '签名生成失败',
                'message': f'Spider 签名生成失败: {e}',
            }, False
        query_params['verifyFp'] = verify_fp
        query_params['fp'] = verify_fp
        logger.info(
            "comment_publish spider request: csrf=%s ticket_guard=%s webid=%s msToken=%s verify_fp=%s",
            bool(headers.get('x-secsdk-csrf-token')),
            bool(headers.get('bd-ticket-guard-client-data')),
            bool(query_params.get('webid')),
            bool(query_params.get('msToken')),
            bool(verify_fp),
        )

        try:
            response = await asyncio.to_thread(
                _api_post_stateless,
                url,
                params=query_params,
                data=body_params,
                headers=headers,
                cookies=cookie_dict,
                timeout=(10, 30),
            )
        except requests.RequestException as e:
            return {
                'status_code': -1,
                'status_msg': '网络请求失败',
                'message': f'网络请求失败: {e}',
            }, False

        ticket_guard_result = response.headers.get('bd-ticket-guard-result') or response.headers.get('Bd-Ticket-Guard-Result') or ''
        logger.info(
            "comment_publish first response: status=%s len=%s ticket_guard_result=%s logid=%s",
            response.status_code,
            len(response.content or b''),
            ticket_guard_result or '',
            response.headers.get('x-tt-logid') or response.headers.get('X-Tt-Logid') or '',
        )
        if response.status_code == 200 and len(response.content or b'') == 0 and ticket_guard_result == '1002':
            cookie_ticket_headers = self._client._ticket_guard_headers_from_cookie()
            if cookie_ticket_headers:
                retry_headers = {
                    key: value
                    for key, value in headers.items()
                    if not key.lower().startswith('bd-ticket-guard-')
                }
                retry_headers.update(cookie_ticket_headers)
                retry_csrf_headers = dict(retry_headers)
                retry_csrf_headers['cookie'] = cookie_str_with_ms_token
                retry_csrf_token = await self._client._get_csrf_token(retry_csrf_headers, force_refresh=True)
                if retry_csrf_token:
                    retry_headers['x-secsdk-csrf-token'] = retry_csrf_token
                logger.info(
                    "comment_publish ticket retry: ticket_guard=%s csrf=%s",
                    bool(retry_headers.get('bd-ticket-guard-client-data')),
                    bool(retry_headers.get('x-secsdk-csrf-token')),
                )
                try:
                    response = await asyncio.to_thread(
                        _api_post_stateless,
                        url,
                        params=query_params,
                        data=body_params,
                        headers=retry_headers,
                        cookies=cookie_dict,
                        timeout=(10, 30),
                    )
                    retry_ticket_guard_result = response.headers.get('bd-ticket-guard-result') or response.headers.get('Bd-Ticket-Guard-Result') or ''
                    logger.info(
                        "comment_publish retry response: status=%s len=%s ticket_guard_result=%s logid=%s",
                        response.status_code,
                        len(response.content or b''),
                        retry_ticket_guard_result or '',
                        response.headers.get('x-tt-logid') or response.headers.get('X-Tt-Logid') or '',
                    )
                except requests.RequestException as e:
                    return {
                        'status_code': -1,
                        'status_msg': '网络请求失败',
                        'message': f'网络请求失败: {e}',
                    }, False

        if response.status_code == 200 and len(response.content or b'') == 0:
            rust_headers = dict(self._client.common_headers)
            rust_headers.update(self._client._relation_ticket_guard_headers(uri))
            rust_headers.update({
                'Referer': f'https://www.douyin.com/video/{aweme_id}',
                'Origin': 'https://www.douyin.com',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-mode': 'cors',
                'sec-fetch-dest': 'empty',
                'priority': 'u=1, i',
                'x-secsdk-csrf-token': 'DOWNGRADE',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
                'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            })
            dtrait = self._client._relation_dtrait()
            if dtrait:
                rust_headers['x-tt-session-dtrait'] = dtrait

            rust_query_params = dict(self._client.common_params)
            for key in ('pc_libra_divert', 'support_h265', 'support_dash', 'disable_rs', 'need_filter_settings', 'list_type'):
                rust_query_params.pop(key, None)
            rust_query_params.update({
                'app_name': 'aweme',
                'enter_from': 'discover',
                'previous_page': 'discover',
                'update_version_code': '170400',
                'version_code': '170400',
                'version_name': '17.4.0',
                'browser_name': 'Chrome',
                'browser_version': '148.0.0.0',
                'engine_version': '148.0.0.0',
                'device_memory': '16',
            })
            rust_query_params = await self._client._deal_params(rust_query_params, rust_headers)
            rust_cookie_dict = dict(cookie_dict)
            rust_cookie_dict['msToken'] = rust_query_params.get('msToken') or rust_cookie_dict.get('msToken') or self._client._get_ms_token()
            rust_query_params['msToken'] = rust_cookie_dict['msToken']
            rust_headers['Cookie'] = '; '.join([f'{key}={value}' for key, value in rust_cookie_dict.items()])
            rust_params_str = urllib.parse.urlencode(rust_query_params)
            try:
                rust_query_params['a_bogus'] = douyin_sign.sign_detail(
                    rust_params_str,
                    rust_headers.get('User-Agent') or rust_headers.get('user-agent') or '',
                )
            except Exception as e:
                logger.warning("comment_publish relation-v2 fallback sign failed: %s", e)
            else:
                rust_body_params = {
                    'aweme_id': aweme_id,
                    'text': text,
                    'text_extra': '[]',
                    'paste_edit_method': 'non_paste',
                    'comment_send_celltime': '3000',
                    'comment_video_celltime': '2000',
                    'one_level_comment_rank': '1',
                }
                if reply_id:
                    rust_body_params['reply_id'] = reply_id
                    rust_body_params['reply_to_reply_id'] = reply_to_reply_id or '0'

                logger.info(
                    "comment_publish relation fallback: ticket_guard=%s dtrait=%s msToken=%s",
                    bool(rust_headers.get('bd-ticket-guard-client-data')),
                    bool(rust_headers.get('x-tt-session-dtrait')),
                    bool(rust_query_params.get('msToken')),
                )
                for relation_attempt in range(3):
                    if relation_attempt > 0:
                        await asyncio.sleep(0.6 * relation_attempt)
                        x_ms_token = response.headers.get('x-ms-token') or response.headers.get('X-Ms-Token') or ''
                        if x_ms_token:
                            rust_cookie_dict['msToken'] = x_ms_token
                            rust_query_params['msToken'] = x_ms_token
                            rust_headers['Cookie'] = '; '.join([f'{key}={value}' for key, value in rust_cookie_dict.items()])
                        rust_query_params.pop('a_bogus', None)
                        rust_body_params['comment_send_celltime'] = str(random.randint(1000, 20000))
                        rust_body_params['comment_video_celltime'] = str(random.randint(1000, 20000))
                        rust_params_str = urllib.parse.urlencode(rust_query_params)
                        try:
                            rust_query_params['a_bogus'] = douyin_sign.sign_detail(
                                rust_params_str,
                                rust_headers.get('User-Agent') or rust_headers.get('user-agent') or '',
                            )
                        except Exception as e:
                            logger.warning("comment_publish relation-v2 fallback retry sign failed: %s", e)
                            break
                    try:
                        response = await asyncio.to_thread(
                            _api_post_stateless,
                            url,
                            params=rust_query_params,
                            data=rust_body_params,
                            headers=rust_headers,
                            cookies=rust_cookie_dict,
                            timeout=(10, 30),
                        )
                        rust_ticket_guard_result = response.headers.get('bd-ticket-guard-result') or response.headers.get('Bd-Ticket-Guard-Result') or ''
                        logger.info(
                            "comment_publish relation-v2 fallback response: attempt=%s status=%s len=%s ticket_guard_result=%s logid=%s",
                            relation_attempt + 1,
                            response.status_code,
                            len(response.content or b''),
                            rust_ticket_guard_result or '',
                            response.headers.get('x-tt-logid') or response.headers.get('X-Tt-Logid') or '',
                        )
                        if response.status_code != 200 or len(response.content or b'') > 0:
                            break
                    except requests.RequestException as e:
                        return {
                            'status_code': -1,
                            'status_msg': '网络请求失败',
                            'message': f'网络请求失败: {e}',
                        }, False

        if response.status_code != 200 or len(response.content or b'') == 0:
            body_preview = ''
            try:
                body_preview = response.text[:1000]
            except Exception:
                body_preview = '<unreadable>'
            logger.warning(
                "comment_publish empty/error response: status=%s headers=%s",
                response.status_code,
                {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in (
                        'content-type',
                        'content-length',
                        'bd-ticket-guard-result',
                        'bd-ticket-guard-server-data',
                        'bd_passport_security_gateway',
                        'passport-security-gateway',
                        'x-tt-logid',
                        'x-tt-verify-passport-decision',
                        'x-ms-token',
                        'x-ware-csrf-token',
                    )
                },
            )
            return {
                'status_code': response.status_code,
                'status_msg': '请求失败',
                'message': '发表评论失败，请检查 Cookie 或稍后重试',
                'body': body_preview,
            }, False

        try:
            json_response = response.json()
        except Exception as e:
            return {
                'status_code': -1,
                'status_msg': 'JSON解析失败',
                'message': f'JSON解析失败: {e}',
            }, False

        if json_response.get('status_code', 0) != 0:
            if self._client._looks_like_logged_out_error(json_response):
                return self._client._build_login_required_error(json_response), False
            if self._client._looks_like_login_or_verify_error(uri, json_response):
                verify_hint, _ = self._client._build_verify_hint(uri, query_params, response)
                api_message = self._client._extract_api_message(json_response)
                verify_hint.update({
                    'status_code': json_response.get('status_code'),
                    'status_msg': json_response.get('status_msg', ''),
                    'message': f'{api_message}，请完成验证或重新获取 Cookie 后重试',
                })
                return verify_hint, False
            return json_response, False

        return json_response, True
