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
