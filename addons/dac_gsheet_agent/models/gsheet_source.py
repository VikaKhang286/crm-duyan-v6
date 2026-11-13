import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .gsheet_client import GSheetClient, GSheetError, load_service_account, parse_gid, parse_spreadsheet_id

_logger = logging.getLogger(__name__)

PARAM = 'dac_gsheet_agent.'

ORDER_STATES = [
    ('quotation', 'Báo giá'),
    ('deposit', 'Đặt cọc'),
    ('production', 'Sản xuất'),
    ('installation', 'Thi công - lắp đặt'),
    ('delivery', 'Giao hàng'),
    ('payment', 'Thu tiền'),
    ('completed', 'Hoàn thành'),
    ('cancel', 'Hủy'),
]

# Các trường đơn xe đẩy có thể map với cột sheet.
# writable: agent được đề xuất ghi trường này vào Odoo.
FIELD_SPECS = {
    'order_number': {'label': 'Số đặt hàng', 'type': 'char', 'writable': True},
    'partner_name': {'label': 'Tên khách hàng', 'type': 'char', 'writable': False},
    'partner_phone': {'label': 'SĐT khách hàng', 'type': 'phone', 'writable': False},
    'date': {'label': 'Ngày đơn hàng', 'type': 'date', 'writable': True},
    'production_deadline': {'label': 'Deadline sản xuất', 'type': 'date', 'writable': True},
    'order_state_custom': {'label': 'Trạng thái', 'type': 'state', 'writable': True},
    'amount_total': {'label': 'Tổng tiền', 'type': 'amount', 'writable': False},
    'user_id': {'label': 'Sale phụ trách', 'type': 'user', 'writable': False},
    'cart_design_link': {'label': 'Link file thiết kế', 'type': 'char', 'writable': True},
    'ignore': {'label': '(Chỉ để AI đọc — không so khớp)', 'type': 'ignore', 'writable': False},
}
FIELD_SELECTION = [(k, v['label']) for k, v in FIELD_SPECS.items()]


# ----------------------------------------------------------------------
# Chuẩn hoá giá trị để so khớp
# ----------------------------------------------------------------------
def strip_accents(text):
    text = unicodedata.normalize('NFD', str(text or ''))
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return text.replace('đ', 'd').replace('Đ', 'D')


def norm_text(value):
    return re.sub(r'\s+', ' ', strip_accents(value)).strip().casefold()


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    text = re.sub(r'\s+\d{1,2}:\d{2}(:\d{2})?$', '', text)  # bỏ phần giờ
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d.%m.%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value):
    if value in (None, '', False):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).lower().replace('đ', '').replace('vnd', '').strip()
    text = re.sub(r'[^\d,.\-]', '', text)
    if not text:
        return None
    # Kiểu VN: 1.250.000 hoặc 1,250,000 → bỏ phân cách hàng nghìn
    if re.fullmatch(r'-?\d{1,3}([.,]\d{3})+', text):
        text = re.sub(r'[.,]', '', text)
    else:
        text = text.replace(',', '.')
    try:
        return int(round(float(text)))
    except ValueError:
        return None


class DacGsheetSource(models.Model):
    _name = 'dac.gsheet.source'
    _description = 'File Google Sheet kết nối'
    _order = 'sequence, id'

    name = fields.Char(string='Tên gợi nhớ', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    url = fields.Char(string='Link Google Sheet', required=True)
    spreadsheet_id = fields.Char(compute='_compute_spreadsheet_id', store=True, index=True)
    spreadsheet_title = fields.Char(string='Tên file trên Google', readonly=True)
    worksheet_name = fields.Char(
        string='Tên tab (sheet)',
        help='Bỏ trống: dùng tab trong link (gid) hoặc tab đầu tiên.')
    header_row = fields.Integer(string='Dòng tiêu đề', default=1, required=True)
    access_mode = fields.Selection([
        ('read', 'Chỉ xem'),
        ('write', 'Chỉnh sửa (AI được ghi)'),
    ], string='Quyền của AI', default='read', required=True,
        help='Chỉ xem: AI chỉ đọc. Chỉnh sửa: AI được đề xuất/ghi vào sheet. '
             'Nhớ chia sẻ file cho email service account đúng quyền tương ứng.')
    sheet_write_needs_approval = fields.Boolean(
        string='Ghi sheet cần người duyệt', default=True,
        help='Tắt: AI ghi trực tiếp vào sheet (vẫn được log).')
    key_header = fields.Char(
        string='Cột khoá (Số đặt hàng)',
        help='Tên cột trên sheet dùng để khớp với "Số đặt hàng" của đơn xe đẩy.')
    column_ids = fields.One2many('dac.gsheet.column.map', 'source_id', string='Ánh xạ cột', copy=True)
    state_map_ids = fields.One2many('dac.gsheet.state.map', 'source_id', string='Ánh xạ trạng thái', copy=True)
    ai_notes = fields.Text(
        string='Mô tả cho AI',
        help='Giải thích file này dùng để làm gì, ai cập nhật, quy ước tô màu, v.v.')
    track_from_date = fields.Date(
        string='Chỉ đối chiếu đơn từ ngày',
        help='Đơn xe đẩy tạo trước ngày này không bị báo "thiếu trên sheet".')

    auto_check = fields.Boolean(string='Tự động kiểm tra', default=False)
    check_interval_minutes = fields.Integer(string='Chu kỳ (phút)', default=60)
    auto_ai_review = fields.Boolean(
        string='AI tự phân tích sau mỗi lần kiểm tra',
        help='Khi có chênh lệch mới, AI xem xét và tạo đề xuất chờ người phụ trách duyệt.')
    responsible_user_id = fields.Many2one(
        'res.users', string='Người phụ trách duyệt', default=lambda self: self.env.user)
    last_check_at = fields.Datetime(string='Lần kiểm tra gần nhất', readonly=True)
    last_status = fields.Selection([
        ('never', 'Chưa chạy'), ('ok', 'Thành công'), ('error', 'Lỗi'),
    ], default='never', readonly=True, string='Trạng thái')
    last_error = fields.Text(readonly=True, string='Lỗi gần nhất')
    headers_json = fields.Text(readonly=True)
    header_preview = fields.Char(compute='_compute_header_preview', string='Các cột đọc được')
    open_discrepancy_count = fields.Integer(compute='_compute_open_discrepancy_count')
    service_account_email = fields.Char(compute='_compute_service_account_email')

    @api.depends('url')
    def _compute_spreadsheet_id(self):
        for rec in self:
            rec.spreadsheet_id = parse_spreadsheet_id(rec.url)

    @api.depends('headers_json')
    def _compute_header_preview(self):
        for rec in self:
            rec.header_preview = ', '.join(json.loads(rec.headers_json or '[]'))

    def _compute_open_discrepancy_count(self):
        groups = self.env['dac.gsheet.discrepancy']._read_group(
            [('source_id', 'in', self.ids), ('state', '=', 'open')], ['source_id'], ['__count'])
        counts = {src.id: count for src, count in groups}
        for rec in self:
            rec.open_discrepancy_count = counts.get(rec.id, 0)

    def _compute_service_account_email(self):
        raw = self._get_param('service_account_json')
        try:
            email = json.loads(raw or '{}').get('client_email', '')
        except ValueError:
            email = ''
        for rec in self:
            rec.service_account_email = email or '(chưa cấu hình service account)'

    @api.constrains('url')
    def _check_url(self):
        for rec in self:
            if not parse_spreadsheet_id(rec.url):
                raise ValidationError(_('Link Google Sheet không hợp lệ: %s') % rec.url)

    @api.constrains('header_row', 'check_interval_minutes')
    def _check_numbers(self):
        for rec in self:
            if rec.header_row < 1:
                raise ValidationError(_('Dòng tiêu đề phải ≥ 1.'))
            if rec.check_interval_minutes < 5:
                raise ValidationError(_('Chu kỳ kiểm tra tối thiểu 5 phút.'))

    # ------------------------------------------------------------------
    # Cấu hình chung (config parameters)
    # ------------------------------------------------------------------
    @api.model
    def _get_param(self, key, default=''):
        # sudo(): đọc cấu hình hệ thống (secret) — người dùng thường không có quyền ir.config_parameter.
        return self.env['ir.config_parameter'].sudo().get_param(PARAM + key, default)

    @api.model
    def get_llm_config(self):
        return {
            'provider': self._get_param('llm_provider', 'anthropic'),
            'base_url': self._get_param('llm_base_url'),
            'api_key': self._get_param('llm_api_key'),
            'model': self._get_param('llm_model', 'claude-opus-5-5'),
            'effort': self._get_param('llm_effort', 'medium'),
            'use_fallbacks': self._get_param('llm_use_fallbacks', 'True') not in ('False', '0', ''),
        }

    # ------------------------------------------------------------------
    # Truy cập Google Sheet
    # ------------------------------------------------------------------
    def _client(self, writable=False):
        self.ensure_one()
        info = load_service_account(self._get_param('service_account_json'))
        if writable and self.access_mode != 'write':
            raise GSheetError('File "%s" đang ở chế độ CHỈ XEM.' % self.name)
        return GSheetClient(info, self.spreadsheet_id, writable=writable)

    def _resolve_worksheet(self, client):
        self.ensure_one()
        if self.worksheet_name:
            return self.worksheet_name
        meta = client.get_metadata()
        gid = parse_gid(self.url)
        sheets = meta['sheets']
        if not sheets:
            raise GSheetError('File không có tab nào.')
        chosen = next((s for s in sheets if s['sheet_id'] == gid), sheets[0])
        return chosen['title']

    def fetch_table(self):
        """Đọc sheet → dict(headers, rows=[{row, values{header: value}}], worksheet)."""
        self.ensure_one()
        client = self._client()
        worksheet = self._resolve_worksheet(client)
        values = client.get_values(worksheet)
        header_idx = self.header_row - 1
        if len(values) <= header_idx:
            return {'headers': [], 'rows': [], 'worksheet': worksheet}
        headers = [str(h).strip() for h in values[header_idx]]
        # đặt tên cho cột không tiêu đề để vẫn đọc được
        headers = [h or 'Cột %s' % (i + 1) for i, h in enumerate(headers)]
        rows = []
        for offset, raw in enumerate(values[header_idx + 1:]):
            if not any(str(c).strip() for c in raw):
                continue
            row_values = {headers[i]: raw[i] if i < len(raw) else '' for i in range(len(headers))}
            rows.append({'row': self.header_row + 1 + offset, 'values': row_values})
        return {'headers': headers, 'rows': rows, 'worksheet': worksheet}

    def write_cells(self, row_number, values_by_header):
        """Ghi giá trị theo tên cột vào một dòng (chỉ khi quyền 'write')."""
        self.ensure_one()
        client = self._client(writable=True)
        worksheet = self._resolve_worksheet(client)
        headers = json.loads(self.headers_json or '[]')
        if not headers:
            headers = self.fetch_table()['headers']
        cells = []
        for header, value in values_by_header.items():
            if header not in headers:
                raise GSheetError('Không có cột "%s" trong sheet.' % header)
            if int(row_number) <= self.header_row:
                raise GSheetError('Không được ghi đè dòng tiêu đề.')
            cells.append((int(row_number), headers.index(header), value))
        client.batch_update_cells(worksheet, cells)

    def append_row(self, values_by_header):
        self.ensure_one()
        client = self._client(writable=True)
        worksheet = self._resolve_worksheet(client)
        headers = json.loads(self.headers_json or '[]') or self.fetch_table()['headers']
        unknown = set(values_by_header) - set(headers)
        if unknown:
            raise GSheetError('Không có cột: %s' % ', '.join(sorted(unknown)))
        client.append_row(worksheet, [values_by_header.get(h, '') for h in headers])

    # ------------------------------------------------------------------
    # Nút trên form
    # ------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        try:
            client = self._client()
            meta = client.get_metadata()
            table = self.fetch_table()
        except GSheetError as exc:
            self.write({'last_status': 'error', 'last_error': str(exc)})
            raise UserError(str(exc)) from exc
        vals = {
            'spreadsheet_title': meta['title'],
            'headers_json': json.dumps(table['headers'], ensure_ascii=False),
            'last_error': False,
        }
        if not self.worksheet_name:
            vals['worksheet_name'] = table['worksheet']
        self.write(vals)
        self._autofill_column_map(table['headers'])
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Kết nối thành công'),
            'message': _('%(title)s / %(tab)s: %(cols)s cột, %(rows)s dòng dữ liệu') % {
                'title': meta['title'], 'tab': table['worksheet'],
                'cols': len(table['headers']), 'rows': len(table['rows'])},
            'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
        }}

    def _autofill_column_map(self, headers):
        """Gợi ý ánh xạ cột theo tên quen thuộc (chỉ khi chưa có ánh xạ)."""
        self.ensure_one()
        if self.column_ids:
            return
        hints = [
            ('order_number', ('so dat hang', 'so dh', 'ma don', 'so phieu', 'ma dh', 'so don')),
            ('partner_phone', ('sdt', 'so dien thoai', 'dien thoai', 'phone')),
            ('partner_name', ('ten khach', 'khach hang', 'ho ten', 'ten kh')),
            ('production_deadline', ('deadline', 'ngay giao', 'han giao', 'ngay hoan thanh')),
            ('date', ('ngay dat', 'ngay don', 'ngay tao', 'ngay')),
            ('order_state_custom', ('trang thai', 'tinh trang', 'tien do')),
            ('amount_total', ('tong tien', 'thanh tien', 'gia tri', 'tong')),
            ('user_id', ('sale', 'nhan vien', 'nguoi ban')),
            ('cart_design_link', ('link thiet ke', 'file thiet ke')),
        ]
        used, lines = set(), []
        for header in headers:
            key = norm_text(header)
            for field_key, words in hints:
                if field_key in used:
                    continue
                if any(w in key for w in words):
                    used.add(field_key)
                    lines.append((0, 0, {'header': header, 'odoo_field': field_key}))
                    if field_key == 'order_number' and not self.key_header:
                        self.key_header = header
                    break
        if lines:
            self.write({'column_ids': lines})

    def action_run_check(self):
        self.ensure_one()
        run = self.env['dac.gsheet.check.run'].run_for_source(self, trigger='manual')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'dac.gsheet.check.run',
            'res_id': run.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_discrepancies(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Chênh lệch — %s') % self.name,
            'res_model': 'dac.gsheet.discrepancy',
            'view_mode': 'list,form',
            'domain': [('source_id', '=', self.id)],
            'context': {'search_default_open': 1},
        }

    def action_open_chat(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'dac_gsheet_agent.chat',
            'name': _('Trợ lý AI Xe đẩy'),
            'context': {'default_source_ids': self.ids},
        }

    # ------------------------------------------------------------------
    # Ánh xạ / chuẩn hoá
    # ------------------------------------------------------------------
    def get_field_columns(self):
        """{odoo_field: header} cho các cột được so khớp."""
        self.ensure_one()
        return {c.odoo_field: c.header for c in self.column_ids if c.odoo_field != 'ignore' and c.compare}

    def map_state(self, sheet_value):
        self.ensure_one()
        key = norm_text(sheet_value)
        if not key:
            return None
        for line in self.state_map_ids:
            if norm_text(line.sheet_value) == key:
                return line.state
        for code, label in ORDER_STATES:
            if norm_text(label) == key or code == key:
                return code
        return None

    def state_to_sheet(self, state_code):
        self.ensure_one()
        line = self.state_map_ids.filtered(lambda l: l.state == state_code)[:1]
        return line.sheet_value if line else dict(ORDER_STATES).get(state_code, state_code)

    # ------------------------------------------------------------------
    @api.model
    def _cron_auto_check(self):
        """Cron: kiểm tra các nguồn đến hạn. KHÔNG raise — lỗi ghi vào last_error."""
        now = fields.Datetime.now()
        for source in self.search([('auto_check', '=', True)]):
            due = not source.last_check_at or \
                source.last_check_at + timedelta(minutes=source.check_interval_minutes) <= now
            if not due:
                continue
            try:
                with self.env.cr.savepoint():
                    run = self.env['dac.gsheet.check.run'].run_for_source(source, trigger='cron')
                if source.auto_ai_review and run.state == 'ok' and run.new_discrepancy_count:
                    with self.env.cr.savepoint():
                        self.env['dac.gsheet.agent.session'].start_auto_review(run)
            except Exception as exc:  # cron không được raise
                _logger.exception('Auto check source %s failed', source.id)
                source.write({'last_status': 'error', 'last_error': str(exc), 'last_check_at': now})
            self.env.cr.commit()  # mỗi nguồn một transaction để lỗi nguồn này không rollback nguồn khác


class DacGsheetColumnMap(models.Model):
    _name = 'dac.gsheet.column.map'
    _description = 'Ánh xạ cột Google Sheet ↔ trường đơn hàng'
    _order = 'sequence, id'

    source_id = fields.Many2one('dac.gsheet.source', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    header = fields.Char(string='Tên cột trên sheet', required=True)
    odoo_field = fields.Selection(FIELD_SELECTION, string='Trường trên Odoo', required=True)
    compare = fields.Boolean(string='So khớp', default=True,
                             help='Tắt: AI vẫn đọc cột này nhưng kiểm tra tự động bỏ qua.')

    _sql_constraints = [
        ('uniq_header', 'unique(source_id, header)', 'Mỗi cột chỉ được ánh xạ một lần.'),
    ]


class DacGsheetStateMap(models.Model):
    _name = 'dac.gsheet.state.map'
    _description = 'Ánh xạ giá trị trạng thái trên sheet'

    source_id = fields.Many2one('dac.gsheet.source', required=True, ondelete='cascade', index=True)
    sheet_value = fields.Char(string='Giá trị trên sheet', required=True)
    state = fields.Selection(ORDER_STATES, string='Trạng thái Odoo', required=True)
