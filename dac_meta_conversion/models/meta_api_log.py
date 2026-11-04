import hashlib
import json
import re
import time

import requests

from odoo import api, fields, models, _

OPERATIONS = [
    ('purchase', 'Gửi Purchase'),
    ('dataset_check', 'Kiểm tra Dataset / token'),
    ('page_dataset_check', 'Kiểm tra liên kết Page – Dataset'),
]
LOG_STATES = [
    ('started', 'Đang gọi API'), ('success', 'Thành công'),
    ('rejected', 'Meta từ chối'), ('network_error', 'Lỗi kết nối'),
    ('uncertain', 'Chưa rõ kết quả'),
]


def safe_payload(value, secrets=(), in_user_data=False):
    """Keep useful diagnostics while excluding credentials and customer IDs."""
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            if any(word in key.lower() for word in ('token', 'secret', 'authorization', 'password')):
                result[key] = '[đã che]'
            else:
                result[key] = safe_payload(item, secrets, in_user_data or key == 'user_data')
        return result
    if isinstance(value, list):
        return [safe_payload(item, secrets, in_user_data) for item in value[:100]]
    if isinstance(value, str):
        if in_user_data:
            return '[đã che định danh]'
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            value = value.replace(str(secret), '[đã che]')
        value = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [đã che]', value)
        value = re.sub(r'\bEAA[A-Za-z0-9_-]{10,}\b', '[đã che token]', value)
        return value[:4000]
    if in_user_data and value is not None:
        return '[đã che định danh]'
    return value


class MetaApiLog(models.Model):
    _name = 'dac.meta.api.log'
    _description = 'Lịch sử API Chuyển đổi Meta'
    _order = 'id desc'
    _rec_name = 'operation'

    company_id = fields.Many2one('res.company', string='Công ty', required=True, index=True, readonly=True)
    event_id = fields.Many2one('dac.meta.conversion.event', string='Sự kiện chuyển đổi', ondelete='set null', index=True, readonly=True)
    order_id = fields.Many2one('sale.order', string='Đơn hàng', ondelete='set null', index=True, readonly=True)
    order_reference = fields.Char(string='Mã đơn lúc gửi', readonly=True)
    operation = fields.Selection(OPERATIONS, string='Loại API', required=True, index=True, readonly=True)
    attempt_number = fields.Integer(string='Lần gửi', readonly=True)
    method = fields.Char(string='Phương thức', required=True, readonly=True)
    endpoint = fields.Char(string='Địa chỉ API', required=True, readonly=True)
    dataset_id = fields.Char(string='Dataset ID', readonly=True)
    is_test = fields.Boolean(string='Có Test Event Code', readonly=True)
    state = fields.Selection(LOG_STATES, string='Trạng thái', default='started', required=True, index=True, readonly=True)
    started_at = fields.Datetime(string='Bắt đầu', default=fields.Datetime.now, required=True, index=True, readonly=True)
    completed_at = fields.Datetime(string='Kết thúc', readonly=True)
    duration_ms = fields.Integer(string='Thời gian (ms)', readonly=True)
    http_status = fields.Integer(string='HTTP', readonly=True, help='0 nghĩa là chưa nhận được mã HTTP từ Meta.')
    events_received = fields.Integer(string='Sự kiện Meta nhận', readonly=True)
    error_code = fields.Char(string='Mã lỗi Meta', readonly=True)
    error_subcode = fields.Char(string='Mã lỗi phụ', readonly=True)
    trace_id = fields.Char(string='Mã truy vết Meta', readonly=True)
    summary = fields.Text(string='Kết quả', readonly=True)
    request_payload = fields.Json(readonly=True, groups='base.group_system')
    response_payload = fields.Json(readonly=True, groups='base.group_system')
    request_digest = fields.Char(string='SHA-256 nội dung gửi', readonly=True, groups='base.group_system')
    request_preview = fields.Text(string='Nội dung gửi (đã che)', compute='_compute_previews', groups='base.group_system')
    response_preview = fields.Text(string='Phản hồi Meta (đã che)', compute='_compute_previews', groups='base.group_system')

    @api.depends('request_payload', 'response_payload')
    def _compute_previews(self):
        for log in self:
            log.request_preview = json.dumps(log.request_payload or {}, ensure_ascii=False, indent=2)
            log.response_preview = json.dumps(log.response_payload or {}, ensure_ascii=False, indent=2)

    def _start_call(self, company, operation, method, path, payload=None, event=None):
        company = company.sudo()
        if method not in ('GET', 'POST') or not re.fullmatch(r'[0-9]+(?:/events|/dataset)?', path):
            raise ValueError('Unsupported Meta API endpoint')
        endpoint = 'https://graph.facebook.com/%s/%s' % (company.meta_conversion_api_version, path)
        return self.sudo().create({
            'company_id': company.id, 'operation': operation, 'method': method, 'endpoint': endpoint,
            'dataset_id': event.dataset_id if event else company.meta_conversion_dataset_id,
            'event_id': event.id if event else False,
            'order_id': event.order_id.id if event else False,
            'order_reference': event.order_id.name if event else False,
            'attempt_number': event.attempt_count if event else 0,
            'is_test': bool((payload or {}).get('test_event_code')),
            'request_payload': safe_payload(payload or {}, [company.meta_conversion_access_token]),
            'request_digest': hashlib.sha256(json.dumps(payload or {}, sort_keys=True, default=str).encode()).hexdigest(),
            'summary': _('Đang chờ phản hồi từ Meta.'),
        })

    def _call(self, company, operation, method, path, payload=None, event=None, api_log=None):
        company = company.sudo()
        log = api_log or self._start_call(company, operation, method, path, payload, event)
        started = time.monotonic()
        headers = {'Authorization': 'Bearer ' + company.meta_conversion_access_token.strip()}
        try:
            if method == 'POST':
                response = requests.post(log.endpoint, headers=headers, json=payload,
                                         timeout=(3.05, 8), allow_redirects=False)
            else:
                response = requests.get(log.endpoint, headers=headers, params={'fields': 'id'},
                                        timeout=(3.05, 8), allow_redirects=False)
        except requests.RequestException as exc:
            connecting = isinstance(exc, requests.ConnectTimeout)
            log.sudo().write({
                'state': 'network_error' if method == 'GET' or connecting else 'uncertain',
                'completed_at': fields.Datetime.now(), 'duration_ms': int((time.monotonic() - started) * 1000),
                'summary': _('Không thiết lập được kết nối tới Meta.') if connecting else _('Không nhận được phản hồi chắc chắn. Với Purchase, cần đối soát Meta trước khi gửi lại.'),
            })
            return {'transport_error': 'connect_timeout' if connecting else 'connection', 'data': {}, 'http_status': 0, 'log': log}
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        status = response.status_code
        error = data.get('error') if isinstance(data.get('error'), dict) else {}
        accepted = 200 <= status < 300 and not error
        if method == 'POST':
            accepted = accepted and data.get('events_received') == 1
        state = 'success' if accepted else 'rejected'
        if method == 'POST' and (status >= 500 or (200 <= status < 300 and not accepted)):
            state = 'uncertain'
        secrets = [company.meta_conversion_access_token, company.meta_conversion_access_token.strip()]
        for item in (payload or {}).get('data', []):
            for value in item.get('user_data', {}).values():
                secrets.extend(value if isinstance(value, list) else [value])
        safe = safe_payload(data, [str(value) for value in secrets if value])
        error_safe = safe.get('error') if isinstance(safe.get('error'), dict) else {}
        if accepted:
            summary = _('Meta xác nhận đã nhận 1 sự kiện.') if method == 'POST' else _('Meta trả dữ liệu thành công.')
        else:
            summary = _('HTTP %(http)s. %(detail)s') % {
                'http': status, 'detail': error_safe.get('message') or _('Chưa có xác nhận hợp lệ từ Meta.'),
            }
        log.sudo().write({
            'state': state, 'completed_at': fields.Datetime.now(),
            'duration_ms': int((time.monotonic() - started) * 1000), 'http_status': status,
            'events_received': data.get('events_received') if isinstance(data.get('events_received'), int) else 0,
            'error_code': str(error_safe.get('code') or '')[:100],
            'error_subcode': str(error_safe.get('error_subcode') or '')[:100],
            'trace_id': str(error_safe.get('fbtrace_id') or safe.get('fbtrace_id') or '')[:200],
            'summary': summary, 'response_payload': safe,
        })
        return {'data': data, 'http_status': status, 'ok': accepted, 'log': log}
