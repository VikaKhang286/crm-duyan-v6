import hashlib
import json
import re
import time

import requests

from odoo import api, fields, models, _


class PancakeApiLog(models.Model):
    _name = 'dac.pancake.api.log'
    _description = 'Lịch sử API Pancake'
    _order = 'id desc'
    _rec_name = 'operation'

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    order_id = fields.Many2one('sale.order', readonly=True, ondelete='set null', index=True)
    order_reference = fields.Char(string='Mã đơn CRM', readonly=True)
    operation = fields.Char(string='Thao tác', required=True, readonly=True)
    method = fields.Char(string='Phương thức', readonly=True)
    endpoint = fields.Char(string='Địa chỉ API (không có khóa)', readonly=True)
    state = fields.Selection([('started', 'Đang gọi'), ('success', 'Pancake trả thành công'),
        ('rejected', 'Pancake từ chối'), ('uncertain', 'Cần đối soát'), ('network_error', 'Lỗi kết nối')],
        default='started', required=True, readonly=True, index=True)
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    completed_at = fields.Datetime(readonly=True)
    duration_ms = fields.Integer(readonly=True)
    http_status = fields.Integer(string='HTTP', readonly=True)
    error_code = fields.Char(string='Mã lỗi', readonly=True)
    summary = fields.Text(string='Kết quả', readonly=True)
    request_digest = fields.Char(string='SHA-256 nội dung gửi', readonly=True, groups='base.group_system')

    def _start(self, company, operation, method, path, payload=None, order=None, chat=False):
        allowed = method in ('GET', 'POST', 'PUT') and re.fullmatch(r'shops(?:/[0-9]+(?:/orders(?:/[0-9]+)?)?)?', path)
        if chat:
            allowed = method == 'GET' and path == 'pages'
        if not allowed:
            raise ValueError('Unsupported Pancake endpoint')
        root = 'https://pages.fm/api/v1/' if chat else 'https://pos.pages.fm/api/v1/'
        return self.sudo().create({'company_id': company.id, 'order_id': order.id if order else False,
            'order_reference': order.name if order else False, 'operation': operation,
            'method': method, 'endpoint': root + path,
            'request_digest': hashlib.sha256(json.dumps(payload or {}, sort_keys=True).encode()).hexdigest(),
            'summary': _('Chờ Pancake phản hồi; kết quả này không chứng minh Meta nhận chuyển đổi.')})

    def _request(self, company, operation, method, path, payload=None, params=None, order=None, chat=False, log=None):
        company = company.sudo()
        # Always validate the target, even when reusing a precommitted log.
        expected_root = 'https://pages.fm/api/v1/' if chat else 'https://pos.pages.fm/api/v1/'
        if log:
            if log.company_id != company or log.method != method or log.endpoint != expected_root + path:
                raise ValueError('Pancake log target mismatch')
        else:
            log = self._start(company, operation, method, path, payload, order, chat)
        key = (self.env['ir.config_parameter'].sudo().get_param('page_fm.access_token') or
            self.env['ir.config_parameter'].sudo().get_param('pages_fm_main_access_token')) if chat else company.pancake_pos_api_key
        arguments = dict(params or {})
        arguments['access_token' if chat else 'api_key'] = (key or '').strip()
        started = time.monotonic()
        try:
            response = requests.request(method, log.endpoint, params=arguments, json=payload if method != 'GET' else None,
                timeout=(3.05, 12), allow_redirects=False)
            try:
                data = response.json()
            except ValueError:
                data = {}
            data = data if isinstance(data, dict) else {}
            status = response.status_code
            ok = 200 <= status < 300 and data.get('success') is not False and not data.get('error')
            # Read checks must positively prove the expected object at the caller.
            uncertain = method != 'GET' and (status >= 500 or (200 <= status < 300 and not data))
            state = 'uncertain' if uncertain else 'success' if ok else 'rejected'
            code = data.get('error_code')
            code = str(code) if isinstance(code, (int, float)) else ''
            message = _('Pancake trả thành công. Chưa xác nhận chuyển đổi/doanh thu trong Meta.') if ok else _('Pancake chưa xác nhận yêu cầu. HTTP %(http)s, mã lỗi %(code)s.') % {'http': status, 'code': code or '—'}
        except requests.RequestException as exc:
            data, status, ok, code = {}, 0, False, ''
            uncertain = method != 'GET' and not isinstance(exc, requests.ConnectTimeout)
            state = 'uncertain' if uncertain else 'network_error'
            message = _('Không có phản hồi chắc chắn. Đối soát đơn POS trước khi thử lại.')
        log.sudo().write({'state': state, 'http_status': status, 'error_code': code,
            'summary': message, 'completed_at': fields.Datetime.now(),
            'duration_ms': int((time.monotonic() - started) * 1000)})
        # Raw responses and exception strings can contain keys/customer data:
        # they are returned to internal callers only, never stored or displayed.
        return {'ok': ok and not uncertain, 'data': data, 'status': status, 'state': state, 'log': log}
