"""IM 会话创建与消息发送逻辑。"""
import json
import logging
import time
import urllib.parse
import uuid

from src.api import douyin_im_proto
from src.api.im_formatters import first_url

logger = logging.getLogger('api.im.messages')


class IMMessages:
    """IM 会话与消息发送服务。"""

    def __init__(self, client):
        self._client = client

    @staticmethod
    def media_uri_from_url(url: str) -> str:
        text = str(url or '').strip()
        if not text:
            return ''
        try:
            parsed = urllib.parse.urlparse(text)
            path = urllib.parse.unquote(parsed.path or '').lstrip('/')
        except Exception:
            path = text.split('?', 1)[0].lstrip('/')
        if not path:
            return ''
        if path.startswith('aweme/'):
            path = path[len('aweme/'):]
        if path.startswith('img/'):
            path = path[len('img/'):]
        path = path.split('~', 1)[0]
        for suffix in ('.webp', '.jpeg', '.jpg', '.png'):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        return path

    async def create_conversation(self, to_user_id: str | int) -> tuple[dict, bool]:
        signer = self._client._im_proto_signer()
        if not signer:
            return {'message': '私信安全参数未采集完整，请在设置中重新登录 Cookie 后重试'}, False

        current_user, current_success = await self._client._api.get_current_user()
        if not current_success:
            return current_user, False

        try:
            to_uid = int(str(to_user_id).strip())
            my_uid = int(str(current_user.get('uid') or '').strip())
        except Exception:
            return {'message': '缺少可用的数字 uid，无法创建私信会话'}, False
        if not to_uid or not my_uid:
            return {'message': '缺少可用的数字 uid，无法创建私信会话'}, False

        sign_data = f'avatar_url=&idempotent_id=&name=&participants={to_uid},{my_uid}'
        request_sign, sign_error = self._client._ecdsa_request_sign(sign_data, str(signer.get('private_key') or ''))
        if sign_error:
            return {'message': sign_error}, False
        body = douyin_im_proto.build_create_conversation_body(to_uid, my_uid)
        payload = self._client._build_im_proto_request(
            cmd=609,
            body=body,
            request_sign=request_sign,
            signer=signer,
        )
        response, success = await self._client._post_im_proto('https://imapi.douyin.com/v2/conversation/create', payload)
        if not success:
            return response, False
        conversation = douyin_im_proto.first_conversation(response)
        if not conversation:
            return {'message': '创建会话成功但未返回会话信息', 'raw': response}, False
        return {
            'conversation_id': conversation.conversation_id,
            'conversation_short_id': conversation.conversation_short_id,
            'conversation_type': conversation.conversation_type,
            'ticket': conversation.ticket,
            'raw': response,
        }, True

    async def send_text_message(self, to_user_id: str | int, content: str) -> tuple[dict, bool]:
        message = str(content or '').strip()
        if not message:
            return {'message': '消息内容不能为空'}, False
        msg_content = json.dumps({
            'mention_users': [],
            'aweType': 700,
            'richTextInfos': [],
            'text': message,
        }, ensure_ascii=False, separators=(',', ':'))
        return await self.send_content_message(to_user_id, msg_content, message_type=7)

    async def send_video_share_message(self, to_user_id: str | int, video: dict) -> tuple[dict, bool]:
        if not isinstance(video, dict):
            return {'message': '缺少视频信息，无法分享'}, False
        aweme_id = str(video.get('aweme_id') or video.get('itemId') or '').strip()
        if not aweme_id:
            return {'message': '缺少作品 ID，无法分享'}, False
        author = video.get('author') if isinstance(video.get('author'), dict) else {}
        video_data = video.get('video') if isinstance(video.get('video'), dict) else {}
        cover = (
            video.get('cover_url')
            or video.get('cover')
            or video_data.get('cover')
            or video_data.get('origin_cover')
            or video_data.get('dynamic_cover')
        )
        cover_url = first_url(cover)
        author_avatar = first_url(
            author.get('avatar_thumb')
            or author.get('avatar_medium')
            or author.get('avatar_larger')
        )
        cover_uri = self.media_uri_from_url(cover_url)
        author_avatar_uri = self.media_uri_from_url(author_avatar)
        content = {
            'aweType': 800,
            'content_title': str(video.get('desc') or aweme_id),
            'cover_height': int(video_data.get('height') or video.get('height') or 0),
            'cover_width': int(video_data.get('width') or video.get('width') or 0),
            'itemId': aweme_id,
            'cover_url': {
                'url_list': [cover_url] if cover_url else [],
                'uri': cover_uri,
            },
            'content_thumb': {
                'url_list': [author_avatar] if author_avatar else [],
                'uri': author_avatar_uri,
            },
            'uid': str(author.get('uid') or video.get('uid') or ''),
        }
        security, security_success = await self._client.get_im_identity_security_token()
        if not security_success:
            return security, False
        extra_headers = {
            'identity_security_token': json.dumps(
                {'token': security['identity_security_token']},
                ensure_ascii=False,
                separators=(',', ':'),
            ),
            'identity_security_device_id': security['device_id'],
            'identity_security_aid': '6383',
        }
        msg_content = json.dumps(content, ensure_ascii=False, separators=(',', ':'))
        return await self.send_content_message(
            to_user_id,
            msg_content,
            message_type=8,
            extra_headers=extra_headers,
        )

    async def send_content_message(
        self,
        to_user_id: str | int,
        msg_content: str,
        message_type: int = 7,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[dict, bool]:
        conversation, success = await self.create_conversation(to_user_id)
        if not success:
            return conversation, False

        signer = self._client._im_proto_signer()
        if not signer:
            return {'message': '私信安全参数未采集完整，请在设置中重新登录 Cookie 后重试'}, False

        client_message_id = str(uuid.uuid4())
        sign_data = (
            f'content={msg_content}'
            f'&conversation_id={conversation["conversation_id"]}'
            f'&conversation_short_id={conversation["conversation_short_id"]}'
        )
        request_sign, sign_error = self._client._ecdsa_request_sign(sign_data, str(signer.get('private_key') or ''))
        if sign_error:
            return {'message': sign_error}, False
        body = douyin_im_proto.build_send_message_body(
            conversation_id=conversation['conversation_id'],
            conversation_short_id=int(conversation['conversation_short_id']),
            ticket=conversation['ticket'],
            content=msg_content,
            client_message_id=client_message_id,
            now_ms=int(time.time() * 1000),
            message_type=message_type,
        )
        payload = self._client._build_im_pc_proto_request(
            cmd=100,
            body=body,
            request_sign=request_sign,
            signer=signer,
            extra_headers=extra_headers,
        )
        response, send_success = await self._client._post_im_proto(
            'https://imapi.douyin.com/v1/message/send',
            payload,
            with_signed_query=True,
        )
        if not send_success:
            return response, False
        sent_message = douyin_im_proto.sent_message(response)
        if not sent_message:
            logger.info('Douyin IM send returned OK without inline message ack: %s', response)
            return {
                'message': '发送请求已提交，等待私信通道确认',
                'client_message_id': client_message_id,
                'pending_ack': True,
                'conversation': conversation,
                'raw': response,
            }, True
        return {
            'message': '发送成功',
            'client_message_id': client_message_id,
            'message_id': sent_message.server_message_id,
            'conversation_id': sent_message.conversation_id,
            'conversation_short_id': sent_message.conversation_short_id,
            'conversation': conversation,
            'raw': response,
        }, True
