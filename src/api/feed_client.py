"""推荐视频流接口逻辑。

从 DouyinAPI 中拆出的推荐流接口：精选 feed、首页推荐 feed，以及用
详情接口刷新首页推荐播放地址的水合逻辑。FeedClient 持有 DouyinAPI 实例
引用，共享 cookie、headers、common_request 等状态。原方法保留为薄代理，
确保外部调用兼容。
"""

import asyncio
import json


class FeedClient:
    """推荐视频流接口服务。"""

    def __init__(self, api):
        self._api = api

    @property
    def debug_mode(self) -> bool:
        return self._api.debug_mode

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
                resp, success = await self._api.common_request(
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
                resp, success = await self._api.common_request(
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
                        detail_resp, detail_success = await self._api.common_request(
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
