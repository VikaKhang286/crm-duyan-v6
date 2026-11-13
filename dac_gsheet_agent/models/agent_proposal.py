"""Đề xuất thay đổi do AI tạo — người dùng bấm Duyệt mới ghi vào Odoo/Sheet.

Duyệt chạy dưới quyền chính người bấm (không sudo) nên record rule / ACL /
validation trong sale.order.write() vẫn áp dụng đầy đủ.
"""
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn

from .gsheet_check import display, order_field_value
from .gsheet_client import GSheetError
from .gsheet_source import FIELD_SPECS, ORDER_STATES, parse_date

_logger = logging.getLogger(__name__)

KINDS = [
    ('order_update', 'Cập nhật đơn Odoo'),
    ('order_create', 'Tạo đơn xe đẩy trên Odoo'),
    ('sheet_update', 'Sửa ô trên Google Sheet'),
    ('sheet_append', 'Thêm dòng vào Google Sheet'),
]
ORDER_CREATE_FIELDS = ('partner_name', 'partner_phone', 'order_number', 'date', 'production_deadline', 'note')


def normalize_order_changes(changes):
    """Kiểm tra & chuyển giá trị agent gửi sang giá trị ghi được. Raise ValueError."""
    vals = {}
    for key, value in (changes or {}).items():
        spec = FIELD_SPECS.get(key)
        if not spec or not spec['writable']:
            raise ValueError('Không được phép sửa trường "%s". Được sửa: %s' % (
                key, ', '.join(k for k, s in FIELD_SPECS.items() if s['writable'])))
        if spec['type'] == 'date':
            parsed = parse_date(value)
            if not parsed:
                raise ValueError('Ngày không hợp lệ cho %s: %r (dùng YYYY-MM-DD)' % (key, value))
            vals[key] = parsed
        elif spec['type'] == 'state':
            if value not in dict(ORDER_STATES):
                raise ValueError('Trạng thái không hợp lệ: %r. Hợp lệ: %s' % (value, ', '.join(dict(ORDER_STATES))))
            vals[key] = value
        else:
            vals[key] = str(value or '').strip()
    if not vals:
        raise ValueError('Không có thay đổi nào.')
    return vals


class DacGsheetAgentProposal(models.Model):
    _name = 'dac.gsheet.agent.proposal'
    _description = 'Đề xuất đồng bộ của AI Agent'
    _order = 'id desc'

    session_id = fields.Many2one('dac.gsheet.agent.session', ondelete='cascade', index=True)
    message_id = fields.Many2one('dac.gsheet.agent.message', ondelete='set null')
    user_id = fields.Many2one(related='session_id.user_id', store=True, string='Người phụ trách')
    kind = fields.Selection(KINDS, required=True, string='Loại')
    state = fields.Selection([
        ('pending', 'Chờ duyệt'), ('applied', 'Đã áp dụng'), ('rejected', 'Từ chối'), ('failed', 'Lỗi'),
    ], default='pending', index=True, string='Trạng thái')
    order_id = fields.Many2one('sale.order', string='Đơn hàng', ondelete='cascade')
    source_id = fields.Many2one('dac.gsheet.source', string='File sheet', ondelete='cascade')
    sheet_row = fields.Integer(string='Dòng sheet')
    payload_json = fields.Text(required=True)
    reason = fields.Text(string='Lý do AI đưa ra')
    reject_reason = fields.Char(string='Lý do từ chối')
    error_message = fields.Text(string='Lỗi')
    applied_by = fields.Many2one('res.users', readonly=True)
    applied_at = fields.Datetime(readonly=True)
    summary = fields.Char(compute='_compute_summary', string='Nội dung')

    @api.depends('kind', 'payload_json', 'order_id', 'sheet_row')
    def _compute_summary(self):
        for rec in self:
            lines = rec.get_diff_lines()
            target = rec.order_id.display_name if rec.order_id else (
                'dòng %s' % rec.sheet_row if rec.sheet_row else '')
            rec.summary = '%s %s: %s' % (dict(KINDS)[rec.kind], target, '; '.join(
                '%s → %s' % (l['label'], l['new']) for l in lines))[:250]

    def _payload(self):
        try:
            return json.loads(self.payload_json or '{}')
        except ValueError:
            return {}

    def get_diff_lines(self):
        """[{label, old, new}] để hiển thị trên giao diện."""
        self.ensure_one()
        payload = self._payload()
        lines = []
        if self.kind == 'order_update':
            for key, value in payload.get('changes', {}).items():
                spec = FIELD_SPECS.get(key, {'label': key})
                old = order_field_value(self.order_id, key) if self.order_id else ''
                new = parse_date(value) if spec.get('type') == 'date' else value
                lines.append({'label': spec['label'], 'old': display(key, old), 'new': display(key, new)})
        elif self.kind == 'order_create':
            for key in ORDER_CREATE_FIELDS:
                if payload.get(key):
                    lines.append({'label': FIELD_SPECS.get(key, {'label': 'Ghi chú'})['label'],
                                  'old': '', 'new': str(payload[key])})
        else:
            current = payload.get('current') or {}
            for header, value in payload.get('values', {}).items():
                lines.append({'label': header, 'old': str(current.get(header, '')), 'new': str(value)})
        return lines

    # ------------------------------------------------------------------
    def action_apply(self):
        for rec in self:
            if rec.state != 'pending':
                continue
            try:
                with self.env.cr.savepoint():
                    rec._apply()
                rec.write({'state': 'applied', 'applied_by': self.env.uid,
                           'applied_at': fields.Datetime.now(), 'error_message': False})
            except (UserError, GSheetError, ValueError) as exc:
                rec.write({'state': 'failed', 'error_message': str(exc)})
        return True

    def action_reject(self, reason=None):
        self.filtered(lambda p: p.state in ('pending', 'failed')).write({
            'state': 'rejected', 'reject_reason': reason or False})
        return True

    def action_retry(self):
        self.filtered(lambda p: p.state == 'failed').write({'state': 'pending'})
        return True

    def _apply(self):
        self.ensure_one()
        payload = self._payload()
        if self.kind == 'order_update':
            if not self.order_id:
                raise UserError(_('Đơn hàng không còn tồn tại.'))
            vals = normalize_order_changes(payload.get('changes'))
            if 'date' in vals:
                vals['date'] = fields.Datetime.to_datetime(vals['date'])
            self.order_id.write(vals)
            self._resolve_discrepancies(payload)
        elif self.kind == 'order_create':
            order = self._create_order(payload)
            self.order_id = order
            self._resolve_discrepancies(payload)
        elif self.kind == 'sheet_update':
            self.source_id.write_cells(self.sheet_row, payload.get('values') or {})
            self._resolve_discrepancies(payload)
        elif self.kind == 'sheet_append':
            self.source_id.append_row(payload.get('values') or {})
            self._resolve_discrepancies(payload)

    def _resolve_discrepancies(self, payload):
        ids = [int(i) for i in payload.get('discrepancy_ids') or [] if str(i).isdigit()]
        if ids:
            self.env['dac.gsheet.discrepancy'].browse(ids).exists().filtered(
                lambda d: d.state == 'open').write({'state': 'resolved'})

    def _create_order(self, payload):
        Partner = self.env['res.partner']
        name = (payload.get('partner_name') or '').strip()
        phone = normalize_phone_vn(payload.get('partner_phone'))
        if not name and not phone:
            raise UserError(_('Cần tên hoặc số điện thoại khách hàng.'))
        partner = Partner.browse()
        if phone:
            candidates = Partner.search(['|', ('phone', 'ilike', phone[-9:]), ('mobile', 'ilike', phone[-9:])], limit=20)
            partner = candidates.filtered(
                lambda p: phone in (normalize_phone_vn(p.phone), normalize_phone_vn(p.mobile)))[:1]
        if not partner and name and not phone:
            partner = Partner.search([('name', '=ilike', name)], limit=1)
        if not partner:
            partner = Partner.create({'name': name or phone, 'phone': phone or False})
        order_number = (payload.get('order_number') or '').strip()
        if order_number and self.env['sale.order'].search_count([('order_number', '=', order_number)]):
            raise UserError(_('Số đặt hàng %s đã tồn tại trên Odoo.') % order_number)
        vals = {
            'partner_id': partner.id,
            'order_type': 'cart',
            'fulfillment_method': 'delivery',
            'order_number': order_number or False,
        }
        order_date = parse_date(payload.get('date'))
        if order_date:
            vals['date'] = fields.Datetime.to_datetime(order_date)
        deadline = parse_date(payload.get('production_deadline'))
        if deadline:
            vals['production_deadline'] = deadline
        if payload.get('note'):
            vals['note'] = payload['note']
        return self.env['sale.order'].create(vals)

    def as_ui_dict(self):
        self.ensure_one()
        return {
            'id': self.id, 'kind': self.kind, 'kind_label': dict(KINDS)[self.kind],
            'state': self.state, 'reason': self.reason or '', 'error': self.error_message or '',
            'reject_reason': self.reject_reason or '',
            'message_id': self.message_id.id or False,
            'order': {'id': self.order_id.id, 'name': self.order_id.display_name} if self.order_id else False,
            'source': self.source_id.name or '', 'sheet_row': self.sheet_row or False,
            'lines': self.get_diff_lines(),
        }
