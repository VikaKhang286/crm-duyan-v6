"""So khớp tất định giữa Google Sheet và đơn hàng xe đẩy.

Phần này không dùng AI: rẻ, nhanh, kết quả lặp lại được. AI chỉ được gọi
sau đó để giải thích / đề xuất xử lý những chênh lệch mà luật cứng không
tự quyết được (khớp mờ tên khách, trạng thái chưa ánh xạ...).
"""
import hashlib
import json
import logging

from odoo import _, api, fields, models

from .gsheet_client import GSheetError
from .gsheet_source import FIELD_SPECS, ORDER_STATES, norm_text, parse_amount, parse_date
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn

_logger = logging.getLogger(__name__)

KINDS = [
    ('missing_in_odoo', 'Có trên sheet, chưa có trên Odoo'),
    ('missing_in_sheet', 'Có trên Odoo, chưa có trên sheet'),
    ('field_mismatch', 'Lệch dữ liệu'),
    ('duplicate_key', 'Trùng số đơn trên sheet'),
    ('unmapped_state', 'Trạng thái trên sheet chưa ánh xạ'),
]


def order_field_value(order, field_key):
    """Giá trị hiển thị của đơn cho một field_key trong FIELD_SPECS."""
    if field_key == 'partner_name':
        return order.partner_id.name or ''
    if field_key == 'partner_phone':
        return order.partner_id.mobile or order.partner_id.phone or ''
    if field_key == 'user_id':
        return order.user_id.name or ''
    if field_key == 'date':
        return order.date and fields.Datetime.context_timestamp(order, order.date).date() or False
    if field_key == 'amount_total':
        return order.amount_total
    return order[field_key] or ''


def values_equal(source, field_key, sheet_value, odoo_value):
    """True nếu giống nhau; None nếu không thể so (ô sheet trống / không parse được)."""
    ftype = FIELD_SPECS[field_key]['type']
    if sheet_value in (None, ''):
        return None
    if ftype == 'phone':
        return normalize_phone_vn(sheet_value) == normalize_phone_vn(odoo_value)
    if ftype == 'date':
        parsed = parse_date(sheet_value)
        if parsed is None:
            return None
        return parsed == (odoo_value or None)
    if ftype == 'amount':
        parsed = parse_amount(sheet_value)
        if parsed is None:
            return None
        return parsed == int(round(odoo_value or 0))
    if ftype == 'state':
        mapped = source.map_state(sheet_value)
        if mapped is None:
            return None
        return mapped == odoo_value
    if ftype == 'user':
        a, b = norm_text(sheet_value), norm_text(odoo_value)
        return bool(a and b and (a in b or b in a))
    return norm_text(sheet_value) == norm_text(odoo_value)


def display(field_key, value):
    if value in (None, False, ''):
        return ''
    if FIELD_SPECS.get(field_key, {}).get('type') == 'state':
        return dict(ORDER_STATES).get(value, value)
    if FIELD_SPECS.get(field_key, {}).get('type') == 'date' and hasattr(value, 'strftime'):
        return value.strftime('%d/%m/%Y')
    if FIELD_SPECS.get(field_key, {}).get('type') == 'amount':
        return '{:,.0f}'.format(value).replace(',', '.')
    return str(value)


class DacGsheetCheckRun(models.Model):
    _name = 'dac.gsheet.check.run'
    _description = 'Lần kiểm tra Google Sheet'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    source_id = fields.Many2one('dac.gsheet.source', required=True, ondelete='cascade', index=True)
    trigger = fields.Selection([
        ('manual', 'Bấm tay'), ('cron', 'Tự động'), ('agent', 'AI Agent'),
    ], default='manual')
    state = fields.Selection([('ok', 'Thành công'), ('error', 'Lỗi')], default='ok')
    error_message = fields.Text()
    row_count = fields.Integer(string='Số dòng sheet')
    order_count = fields.Integer(string='Số đơn xe đẩy đối chiếu')
    matched_count = fields.Integer(string='Khớp số đơn')
    discrepancy_count = fields.Integer(string='Số chênh lệch')
    new_discrepancy_count = fields.Integer(string='Chênh lệch mới')
    summary = fields.Text(string='Tóm tắt')
    discrepancy_ids = fields.One2many('dac.gsheet.discrepancy', 'run_id', string='Chênh lệch')

    @api.depends('source_id', 'create_date')
    def _compute_name(self):
        for rec in self:
            stamp = rec.create_date and fields.Datetime.context_timestamp(rec, rec.create_date).strftime('%d/%m %H:%M')
            rec.name = '%s — %s' % (rec.source_id.name or '', stamp or '')

    # ------------------------------------------------------------------
    @api.model
    def run_for_source(self, source, trigger='manual'):
        source.ensure_one()
        try:
            table = source.fetch_table()
        except GSheetError as exc:
            source.write({'last_status': 'error', 'last_error': str(exc), 'last_check_at': fields.Datetime.now()})
            return self.create({'source_id': source.id, 'trigger': trigger, 'state': 'error', 'error_message': str(exc)})
        source.write({'headers_json': json.dumps(table['headers'], ensure_ascii=False)})
        return self._compare(source, table, trigger)

    @api.model
    def _cart_orders(self, source):
        domain = [('order_type', '=', 'cart'), ('order_state_custom', '!=', 'cancel')]
        if source.track_from_date:
            domain.append(('create_date', '>=', fields.Datetime.to_datetime(source.track_from_date)))
        return self.env['sale.order'].search(domain)

    @api.model
    def _compare(self, source, table, trigger):
        Disc = self.env['dac.gsheet.discrepancy']
        columns = source.get_field_columns()
        key_header = source.key_header or columns.get('order_number')
        findings = []

        if not key_header or key_header not in table['headers']:
            msg = _('Chưa chọn "Cột khoá (Số đặt hàng)" hợp lệ — mở thiết lập file để chọn.')
            source.write({'last_status': 'error', 'last_error': msg, 'last_check_at': fields.Datetime.now()})
            return self.create({'source_id': source.id, 'trigger': trigger, 'state': 'error',
                                'error_message': msg, 'row_count': len(table['rows'])})

        rows_by_key = {}
        for row in table['rows']:
            key = norm_text(row['values'].get(key_header, ''))
            if not key:
                continue
            rows_by_key.setdefault(key, []).append(row)

        # Bắt cả đơn đã hủy để không báo "thiếu trên Odoo" cho đơn đã hủy.
        all_cart = self.env['sale.order'].search([('order_type', '=', 'cart'), ('order_number', '!=', False)])
        orders_by_key = {}
        for order in all_cart:
            orders_by_key.setdefault(norm_text(order.order_number), order)
        tracked = self._cart_orders(source).filtered('order_number')

        matched = 0
        for key, rows in rows_by_key.items():
            if len(rows) > 1:
                findings.append({
                    'kind': 'duplicate_key', 'sheet_key': rows[0]['values'].get(key_header),
                    'row_number': rows[0]['row'],
                    'sheet_value': ', '.join('dòng %s' % r['row'] for r in rows),
                })
            row = rows[0]
            order = orders_by_key.get(key)
            if not order:
                findings.append({
                    'kind': 'missing_in_odoo', 'sheet_key': row['values'].get(key_header),
                    'row_number': row['row'],
                    'sheet_value': json.dumps(row['values'], ensure_ascii=False)[:2000],
                })
                continue
            matched += 1
            for field_key, header in columns.items():
                if field_key == 'order_number' or header not in row['values']:
                    continue
                sheet_value = row['values'].get(header)
                odoo_value = order_field_value(order, field_key)
                equal = values_equal(source, field_key, sheet_value, odoo_value)
                if equal is None and FIELD_SPECS[field_key]['type'] == 'state' and str(sheet_value).strip():
                    findings.append({
                        'kind': 'unmapped_state', 'sheet_key': row['values'].get(key_header),
                        'row_number': row['row'], 'order_id': order.id, 'field_key': field_key,
                        'sheet_value': sheet_value, 'odoo_value': display(field_key, odoo_value),
                    })
                elif equal is False:
                    findings.append({
                        'kind': 'field_mismatch', 'sheet_key': row['values'].get(key_header),
                        'row_number': row['row'], 'order_id': order.id, 'field_key': field_key,
                        'sheet_value': sheet_value, 'odoo_value': display(field_key, odoo_value),
                    })

        for order in tracked:
            if norm_text(order.order_number) not in rows_by_key:
                findings.append({
                    'kind': 'missing_in_sheet', 'sheet_key': order.order_number, 'order_id': order.id,
                    'odoo_value': '%s — %s' % (order.partner_id.name or '', display('order_state_custom', order.order_state_custom)),
                })

        # Bỏ qua những chênh lệch người dùng đã đánh dấu "Bỏ qua" trước đó.
        ignored = set(Disc.search([('source_id', '=', source.id), ('state', '=', 'ignored')]).mapped('signature'))
        previous_open = Disc.search([('source_id', '=', source.id), ('state', '=', 'open')])
        previous_sigs = set(previous_open.mapped('signature'))
        vals_list, new_count = [], 0
        for item in findings:
            item['signature'] = Disc.make_signature(source.id, item)
            if item['signature'] in ignored:
                continue
            if item['signature'] not in previous_sigs:
                new_count += 1
            item['field_label'] = FIELD_SPECS.get(item.get('field_key'), {}).get('label', '')
            item['source_id'] = source.id
            vals_list.append(item)
        # Các chênh lệch mở của lần trước được thay bằng kết quả mới.
        previous_open.write({'state': 'superseded'})

        run = self.create({
            'source_id': source.id, 'trigger': trigger, 'state': 'ok',
            'row_count': len(table['rows']), 'order_count': len(tracked),
            'matched_count': matched, 'discrepancy_count': len(vals_list),
            'new_discrepancy_count': new_count,
        })
        for vals in vals_list:
            vals['run_id'] = run.id
        Disc.create(vals_list)
        run.summary = run._build_summary()
        source.write({'last_status': 'ok', 'last_error': False, 'last_check_at': fields.Datetime.now()})
        return run

    def _build_summary(self):
        self.ensure_one()
        counts = {}
        for d in self.discrepancy_ids:
            counts[d.kind] = counts.get(d.kind, 0) + 1
        labels = dict(KINDS)
        lines = [_('%(rows)s dòng sheet, %(orders)s đơn xe đẩy, khớp số đơn %(matched)s.') % {
            'rows': self.row_count, 'orders': self.order_count, 'matched': self.matched_count}]
        if not counts:
            lines.append(_('Không có chênh lệch.'))
        for kind, count in counts.items():
            lines.append('• %s: %s' % (labels[kind], count))
        if self.new_discrepancy_count:
            lines.append(_('Mới so với lần trước: %s') % self.new_discrepancy_count)
        return '\n'.join(lines)

    def as_agent_payload(self, limit=60):
        self.ensure_one()
        return {
            'run_id': self.id, 'state': self.state, 'error': self.error_message or None,
            'summary': self.summary,
            'discrepancies': [d.as_agent_dict() for d in self.discrepancy_ids[:limit]],
            'truncated': max(0, len(self.discrepancy_ids) - limit),
        }


class DacGsheetDiscrepancy(models.Model):
    _name = 'dac.gsheet.discrepancy'
    _description = 'Chênh lệch giữa Google Sheet và Odoo'
    _order = 'id desc'

    run_id = fields.Many2one('dac.gsheet.check.run', ondelete='cascade', index=True)
    source_id = fields.Many2one('dac.gsheet.source', required=True, ondelete='cascade', index=True)
    kind = fields.Selection(KINDS, required=True, string='Loại')
    state = fields.Selection([
        ('open', 'Cần xử lý'), ('resolved', 'Đã xử lý'), ('ignored', 'Bỏ qua'), ('superseded', 'Cũ'),
    ], default='open', index=True, string='Tình trạng')
    sheet_key = fields.Char(string='Số đơn')
    row_number = fields.Integer(string='Dòng sheet')
    order_id = fields.Many2one('sale.order', string='Đơn hàng', ondelete='set null', index=True)
    field_key = fields.Char()
    field_label = fields.Char(string='Trường')
    sheet_value = fields.Text(string='Giá trị trên sheet')
    odoo_value = fields.Text(string='Giá trị trên Odoo')
    signature = fields.Char(index=True)
    note = fields.Char(string='Ghi chú')

    @api.model
    def make_signature(self, source_id, item):
        raw = '|'.join(str(item.get(k) or '') for k in ('kind', 'sheet_key', 'order_id', 'field_key', 'sheet_value', 'odoo_value'))
        return hashlib.sha1(('%s|%s' % (source_id, raw)).encode()).hexdigest()

    def action_ignore(self):
        self.write({'state': 'ignored'})

    def action_resolve(self):
        self.write({'state': 'resolved'})

    def as_agent_dict(self):
        self.ensure_one()
        return {
            'id': self.id, 'kind': self.kind, 'order_number': self.sheet_key,
            'sheet_row': self.row_number or None, 'order_id': self.order_id.id or None,
            'field': self.field_key or None, 'sheet_value': self.sheet_value or None,
            'odoo_value': self.odoo_value or None,
        }
