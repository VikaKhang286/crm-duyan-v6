import hmac

from odoo import http
from odoo.http import request
from odoo.exceptions import UserError


class ZaloBridgeController(http.Controller):
    @http.route('/dac_zalo/bridge/events', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def events(self):
        # This machine endpoint must read its private shared secret before authenticating the bridge.
        config = request.env['dac.zalo.config'].sudo()._config()
        supplied = request.httprequest.headers.get('Authorization', '')
        expected = 'Bearer ' + (config.bridge_token or '')
        if not config.active or not config.bridge_token or not hmac.compare_digest(supplied.encode(), expected.encode()):
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        if len(request.httprequest.get_data()) > 1024 * 1024:
            return request.make_json_response({'error': 'payload_too_large'}, status=413)
        try:
            body = request.httprequest.get_json()
            events = body['events']
            account = body['account_id']
            if not isinstance(account, str) or not account or not isinstance(events, list) or len(events) > 100:
                raise ValueError('Invalid payload')
            for event in events:
                if not isinstance(event, dict) or event.get('direction') not in ('in', 'out'):
                    raise ValueError('Invalid event')
                for name in ('thread_id', 'message_id', 'text'):
                    if not isinstance(event.get(name), str) or not event[name] or len(event[name]) > (12000 if name == 'text' else 200):
                        raise ValueError('Invalid event field')
                if not isinstance(event.get('historical', False), bool):
                    raise ValueError('Invalid historical flag')
                if event.get('historical') and (not isinstance(event.get('timestamp'), (int, float)) or event['timestamp'] <= 0):
                    raise ValueError('Invalid historical timestamp')
            # Authenticated bridge can ingest only validated DMs; employee identity is resolved by one-time binding.
            request.env['dac.zalo.message'].sudo()._ingest(account, events)
        except (ValueError, KeyError, TypeError):
            return request.make_json_response({'error': 'invalid_payload'}, status=400)
        except UserError:
            # Do not acknowledge until the account is confirmed or the concurrent batch has finished.
            return request.make_json_response({'error': 'not_ready'}, status=409)
        return request.make_json_response({'accepted': True})

    @http.route('/dac_zalo/bridge/group_events', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def group_events(self):
        # Cùng cách xác thực với /events: bí mật dùng chung của bridge. Chỉ nhận sự kiện liên quan tới tin trợ lý đã gửi.
        config = request.env['dac.zalo.config'].sudo()._config()
        supplied = request.httprequest.headers.get('Authorization', '')
        expected = 'Bearer ' + (config.bridge_token or '')
        if not config.active or not config.bridge_token or not hmac.compare_digest(supplied.encode(), expected.encode()):
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        if len(request.httprequest.get_data()) > 256 * 1024:
            return request.make_json_response({'error': 'payload_too_large'}, status=413)
        try:
            body = request.httprequest.get_json()
            account = body['account_id']
            events = body['events']
            if not isinstance(account, str) or not account or not isinstance(events, list) or len(events) > 100:
                raise ValueError('Invalid payload')
            reactions, replies = [], []
            for item in events:
                if not isinstance(item, dict):
                    raise ValueError('Invalid event')
                text = lambda name, limit: isinstance(item.get(name), str) and 0 < len(item[name]) <= limit
                if item.get('kind') == 'reaction':
                    ids = item.get('message_ids')
                    if (not isinstance(ids, list) or not 0 < len(ids) <= 20 or not all(isinstance(i, str) and 0 < len(i) <= 40 for i in ids)
                            or not (text('thread_id', 200) and text('reactor_uid', 200) and text('icon', 200))):
                        raise ValueError('Invalid reaction')
                    reactions.append(item)
                elif item.get('kind') == 'reply':
                    if not (text('thread_id', 200) and text('sender_uid', 200) and text('message_id', 40)
                            and text('quoted_message_id', 40) and text('text', 500)):
                        raise ValueError('Invalid reply')
                    replies.append(item)
                else:
                    raise ValueError('Unknown event kind')
            # sudo(): bridge là máy, không có user; chỉ ghi nhận sự kiện lên tin trợ lý đã gửi
            request.env['dac.zalo.channel.reaction'].sudo()._ingest(account, reactions)
            request.env['dac.zalo.channel.message'].sudo()._ingest_replies(account, replies)
        except (ValueError, KeyError, TypeError):
            return request.make_json_response({'error': 'invalid_payload'}, status=400)
        return request.make_json_response({'accepted': True})
