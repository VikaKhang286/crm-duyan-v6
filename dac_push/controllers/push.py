"""POST /dac_push/v1/events — nhận gói dữ liệu từ plugin WordPress "Push DAC Odoo".

Xác thực: X-DAC-Timestamp (unix giây, lệch tối đa 300s) + X-DAC-Signature =
hex(HMAC-SHA256(secret, "<timestamp>.<raw_body>")). Chưa bật hoặc chưa có secret → 503 (không bao giờ mở).
Body: {"event_id", "event_type", "source", "occurred_at", "data": {...}}
"""
import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
MAX_BODY = 64 * 1024
MAX_SKEW = 300


def _resp(status, body):
    return request.make_response(json.dumps(body, ensure_ascii=False),
        headers=[('Content-Type', 'application/json'), ('Cache-Control', 'no-store')], status=status)


class PushController(http.Controller):

    # readonly=False: route ghi DB; Odoo 18 mở cursor chỉ-đọc cho auth='none' theo mặc định
    @http.route('/dac_push/v1/events', type='http', auth='none', methods=['POST'], csrf=False,
                readonly=False, save_session=False)
    def events(self, **kwargs):
        # su: route auth='none' của webhook (không có user); mọi quyền dựa trên chữ ký HMAC kiểm tra bên dưới
        env = request.env(su=True)
        config = env['dac.push.config']._config()
        secret = (config.secret or '').strip()
        if not config.active or not secret:
            return _resp(503, {'ok': False, 'error': 'not_configured'})

        raw = request.httprequest.get_data() or b''
        if len(raw) > MAX_BODY:
            return _resp(413, {'ok': False, 'error': 'body_too_large'})
        headers = request.httprequest.headers
        ts, sig = headers.get('X-DAC-Timestamp', ''), headers.get('X-DAC-Signature', '')
        try:
            if abs(time.time() - int(ts)) > MAX_SKEW:
                return _resp(401, {'ok': False, 'error': 'stale_timestamp'})
        except ValueError:
            return _resp(401, {'ok': False, 'error': 'bad_timestamp'})
        expected = hmac.new(secret.encode(), ts.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            _logger.warning('dac_push: chữ ký sai từ %s', request.httprequest.remote_addr)
            return _resp(401, {'ok': False, 'error': 'bad_signature'})

        try:
            envelope = json.loads(raw.decode('utf-8'))
            if not isinstance(envelope, dict):
                raise ValueError
        except ValueError:
            return _resp(400, {'ok': False, 'error': 'invalid_json'})
        if not envelope.get('event_id') or not envelope.get('event_type'):
            return _resp(400, {'ok': False, 'error': 'missing_event'})

        event, created = env['dac.push.event']._receive(envelope)
        return _resp(200, {'ok': True, 'duplicate': not created, 'state': event.state})
