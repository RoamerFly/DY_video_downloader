"""批量下载路由拆分模块。

从 downloads_routes.py 抽离的批量下载相关路由：下载点赞视频、下载点赞作者
作品、通过 aweme_id 下载视频。路由仍注册到同一个 downloads_bp Blueprint，
URL 不变；注入的依赖通过运行时读取 downloads_routes 模块属性获取，
避免循环导入与 setup 时序问题。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from flask import jsonify

from src.web.downloads_routes import downloads_bp


def _deps():
    """延迟读取 downloads_routes 注入的依赖。"""
    from src.web import downloads_routes as dr
    return dr


@downloads_bp.route('/api/download_liked', methods=['POST'])
def download_liked():
    """下载点赞视频"""
    dr = _deps()
    try:
        data = dr._request_json()
        count = dr._coerce_int(data.get('count'), 20, 1, 100)
        if not dr._Config.COOKIE:
            return jsonify({'success': False, 'message': '下载点赞视频需要设置Cookie'}), 400

        user_manager = dr._get_user_manager()
        if not user_manager:
            return jsonify({'success': False, 'message': '请先初始化'}), 400

        # 生成任务ID
        task_id = str(uuid.uuid4())
        dr._task_store.store(task_id, {
            'status': 'running',
            'type': 'liked_videos',
            'start_time': datetime.now()
        })

        # 在全局 Loop 中运行异步下载协程
        async def do_download_liked():
            try:
                dr._socketio.emit('download_started', {
                    'task_id': task_id,
                    'type': 'liked_videos'
                })

                completed = await user_manager.download_liked_videos(count)

                dr._task_store.set_status(task_id, 'completed', end_time=datetime.now())

                dr._socketio.emit('download_completed', {
                    'task_id': task_id,
                    'message': f'点赞视频下载完成，共处理 {completed} 个作品'
                })
            except Exception as e:
                dr._logger.error(f"Download liked error: {e}")
                dr._task_store.set_status(task_id, 'failed')
                dr._socketio.emit('download_failed', {'task_id': task_id, 'message': f'任务出错: {str(e)}'})

        loop = dr._get_or_create_loop()
        asyncio.run_coroutine_threadsafe(do_download_liked(), loop)

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '点赞视频下载任务已开始'
        })

    except Exception as e:
        return jsonify({'success': False, 'message': f'下载失败: {str(e)}'}), 500


@downloads_bp.route('/api/download_liked_authors', methods=['POST'])
def download_liked_authors():
    """下载点赞作者作品"""
    dr = _deps()
    try:
        data = dr._request_json()
        count = dr._coerce_int(data.get('count'), 20, 1, 100)
        selected_sec_uids = data.get('selected_sec_uids') or data.get('sec_uids') or []
        if not dr._Config.COOKIE:
            return jsonify({'success': False, 'message': '下载点赞作者作品需要设置Cookie'}), 400

        user_manager = dr._get_user_manager()
        if not user_manager:
            return jsonify({'success': False, 'message': '请先初始化'}), 400

        # 生成任务ID
        task_id = str(uuid.uuid4())
        dr._task_store.store(task_id, {
            'status': 'running',
            'type': 'liked_authors',
            'start_time': datetime.now()
        })

        # 在全局 Loop 中运行异步下载协程
        async def do_download_liked_authors():
            try:
                dr._socketio.emit('download_started', {
                    'task_id': task_id,
                    'type': 'liked_authors'
                })

                completed = await user_manager.download_liked_authors(count=count, selected_sec_uids=selected_sec_uids)

                dr._task_store.set_status(task_id, 'completed', end_time=datetime.now())

                dr._socketio.emit('download_completed', {
                    'task_id': task_id,
                    'message': f'点赞作者作品下载完成，共处理 {completed} 个作者'
                })
            except Exception as e:
                dr._logger.error(f"Download liked authors error: {e}")
                dr._task_store.set_status(task_id, 'failed')
                dr._socketio.emit('download_failed', {'task_id': task_id, 'message': f'任务出错: {str(e)}'})

        loop = dr._get_or_create_loop()
        asyncio.run_coroutine_threadsafe(do_download_liked_authors(), loop)

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '点赞作者作品下载任务已开始'
        })

    except Exception as e:
        return jsonify({'success': False, 'message': f'下载失败: {str(e)}'}), 500


@downloads_bp.route('/api/download_video', methods=['POST'])
def download_video_by_aweme_id():
    """通过 aweme_id 下载视频"""
    dr = _deps()
    try:
        data = dr._request_json()
        aweme_id = data.get('aweme_id', '').strip()

        if not aweme_id:
            return jsonify({'success': False, 'message': 'aweme_id 参数不能为空'}), 400

        user_manager = dr._get_user_manager()
        if not user_manager:
            return jsonify({'success': False, 'message': '请先初始化'}), 400

        # 使用统一的 API 接口获取视频详情
        detail = dr._run_async(user_manager.get_video_detail(aweme_id))

        if not detail:
            return jsonify({'success': False, 'message': '获取视频详情失败'}), 500

        # 获取媒体信息
        media_type = detail.get('media_type', 'video')
        media_urls = dr._normalize_download_media_urls(detail.get('media_urls', []), media_type)
        video_fallback_urls = user_manager.get_video_download_urls((detail.get('video') or {}))
        if media_type == 'video':
            selected_video_urls = dr._normalize_download_media_urls(
                user_manager._build_video_media_urls(detail.get('video') or {}),
                'video',
            )
            if selected_video_urls:
                media_urls = selected_video_urls

        if not media_urls:
            return jsonify({'success': False, 'message': '无法获取视频下载地址'}), 500

        # 生成文件名
        author_name = detail.get('author', {}).get('nickname', '未知作者')
        name = dr._build_download_name(
            author_name,
            detail.get('desc', ''),
            aweme_id,
            media_type=media_type,
            create_time=detail.get('create_time'),
            default_title_prefix='未知作品',
        )

        # 添加到下载队列
        task_id = str(uuid.uuid4())

        async def do_download():
            try:
                if len(media_urls) == 1 and media_urls[0].get('type') == 'video':
                    success = await asyncio.to_thread(
                        user_manager.downloader.download_video,
                        media_urls[0]['url'],
                        name,
                        aweme_id,
                        asyncio.Event(),
                        dr._socketio,
                        task_id,
                        None,
                        None,
                        False,
                        fallback_urls=video_fallback_urls,
                    )
                else:
                    success = await asyncio.to_thread(
                        user_manager.downloader.download_media_group,
                        media_urls,
                        name,
                        aweme_id,
                        dr._socketio,
                        task_id,
                        asyncio.Event(),
                        None,
                        None,
                        False,
                    )

                if success:
                    dr._socketio.emit('download_complete', {
                        'task_id': task_id,
                        'aweme_id': aweme_id,
                        'message': f'{name} 下载完成'
                    })
                else:
                    dr._socketio.emit('download_error', {
                        'task_id': task_id,
                        'aweme_id': aweme_id,
                        'message': f'{name} 下载失败'
                    })
            except Exception as e:
                dr._logger.error(f"下载视频失败: {e}")
                dr._socketio.emit('download_error', {
                    'task_id': task_id,
                    'aweme_id': aweme_id,
                    'message': f'下载失败: {str(e)}'
                })

        # 在后台线程执行下载
        loop = dr._get_or_create_loop()
        asyncio.run_coroutine_threadsafe(do_download(), loop)

        return jsonify({'success': True, 'task_id': task_id, 'message': '已添加到下载队列'})

    except Exception as e:
        dr._logger.exception(f"下载视频异常: {e}")
        return jsonify({'success': False, 'message': f'下载失败: {str(e)}'}), 500
