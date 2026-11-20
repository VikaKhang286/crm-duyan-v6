"""Kịch bản nhắc: mỗi loại tin nhắc (khách mới, nhắc lại, tin gom, khẩn, lịch hẹn, nhắc việc 8:00, thống kê) là một dòng
quản trị viên bật/tắt, chỉnh thời gian và sửa mẫu tin. Mỗi tin đã gửi được ghi vào Nhật ký nhắc."""
import re
import string

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Giá trị mẫu để xem trước và kiểm tra mẫu tin
SAMPLE = {
    'icon': '⚠️', 'muc_do': '⚠️ CHÚ Ý', 'loai_khach': 'Khách mới', 'cho': '12 phút', 'ten_khach': 'Nguyễn Hoa',
    'tom_tat': 'Hỏi giá in 500 tờ rơi A5, cần chiều nay', 'link': 'https://pancake.vn/…?c_id=…', 'lan': 2, 'toi_da': 4,
    'so_khach': 3, 'nhan_vien': ' (Tiểu Băng)', 'gio_hen': '15:00', 'viec': 'Gửi báo giá bạt 180×80', 'phieu': 'crm.duyan.vn/hen/…',
    'qua_han': 'Quá hẹn 31 phút', 'ngay': '19/11',
}
PLACEHOLDERS = {
    'icon': 'icon mức gấp (🚨 ⚠️ 🔔)', 'muc_do': 'mức gấp có chữ (🚨 KHẨN / ⚠️ CHÚ Ý / 🔔)', 'loai_khach': 'Khách mới / Đang trao đổi / Khách cũ',
    'cho': 'khách đã chờ (giờ làm việc)', 'ten_khach': 'tên khách', 'tom_tat': 'AI tóm tắt khách cần gì', 'link': 'link hội thoại Pancake',
    'lan': 'lần nhắc thứ mấy', 'toi_da': 'số lần nhắc tối đa', 'so_khach': 'số khách trong tin gom', 'nhan_vien': '" (tên nhân viên)" nếu chưa ghép Zalo',
    'gio_hen': 'giờ hẹn', 'viec': 'việc đã hẹn', 'phieu': 'link phiếu hẹn', 'qua_han': '"Quá hẹn 31 phút" / "Đã quá hẹn"', 'ngay': 'ngày (dd/mm)',
}
FIELD_RE = re.compile(r'^[a-z_]+$')


class _Missing(dict):
    def __missing__(self, key):
        return '{%s}' % key


class PancakeRemindScenario(models.Model):
    _name = 'dac.pancake.remind.scenario'
    _description = 'Kịch bản nhắc khách Pancake'
    _order = 'sequence, id'
    _sql_constraints = [('code_unique', 'unique(code)', 'Mỗi kịch bản chỉ có một dòng.')]

    code = fields.Char(string='Mã', required=True, readonly=True)
    name = fields.Char(string='Kịch bản', required=True)
    sequence = fields.Integer(default=10)
    group = fields.Selection([('wait', 'Khách chờ'), ('appointment', 'Lịch hẹn'), ('report', 'Báo cáo')],
        string='Nhóm', required=True, default='wait')
    enabled = fields.Boolean(string='Bật', default=True)
    description = fields.Text(string='Khi nào gửi / gửi gì', readonly=True)
    wait_minutes = fields.Integer(string='Thời gian (phút)', aggregator=None)
    repeat_minutes = fields.Integer(string='Lặp lại mỗi (phút)', aggregator=None)
    max_count = fields.Integer(string='Tối đa (lần)', aggregator=None)
    wait_label = fields.Char(readonly=True)
    repeat_label = fields.Char(readonly=True)
    max_label = fields.Char(readonly=True)
    template = fields.Text(string='Mẫu tin', help='Tag tên người nhận luôn được thêm ở đầu tin. Dùng các biến trong ngoặc nhọn, vd {ten_khach}.')
    line_template = fields.Text(string='Mẫu mỗi dòng', help='Mỗi khách / mỗi việc một dòng (tin gom, nhắc việc 8:00).')
    default_template = fields.Text(readonly=True)
    default_line_template = fields.Text(readonly=True)
    placeholders = fields.Char(string='Biến dùng được', readonly=True, help='Các biến (trong ngoặc nhọn) dùng được trong mẫu tin của kịch bản này.')
    placeholder_help = fields.Text(string='Ý nghĩa các biến', compute='_compute_placeholder_help')
    preview = fields.Text(string='Xem trước', compute='_compute_preview')
    event_ids = fields.One2many('dac.pancake.remind.event', 'scenario_id', string='Nhật ký')
    sent_count = fields.Integer(string='Đã gửi', compute='_compute_stats')
    sent_7d = fields.Integer(string='7 ngày qua', compute='_compute_stats')
    last_sent_at = fields.Datetime(string='Gửi gần nhất', compute='_compute_stats')

    @api.depends('placeholders')
    def _compute_placeholder_help(self):
        for scenario in self:
            names = [n.strip() for n in (scenario.placeholders or '').split(',') if n.strip()]
            scenario.placeholder_help = '\n'.join('{%s} — %s' % (n, PLACEHOLDERS.get(n, '')) for n in names) or False

    @api.depends('template', 'line_template')
    def _compute_preview(self):
        for scenario in self:
            parts = []
            if scenario.template:
                parts.append('@Tbăng ' + scenario._render_template(scenario.template, SAMPLE))
            if scenario.line_template:
                parts.append(scenario._render_template(scenario.line_template, SAMPLE))
                parts.append(scenario._render_template(scenario.line_template, dict(SAMPLE, ten_khach='Long Nguyễn', icon='🔔', cho='45 phút')))
            scenario.preview = '\n'.join(parts) or False

    def _compute_stats(self):
        Event = self.env['dac.pancake.remind.event']
        since = fields.Datetime.subtract(fields.Datetime.now(), days=7)
        total = dict(Event._read_group([('scenario_id', 'in', self.ids)], ['scenario_id'], ['__count']))
        recent = dict(Event._read_group([('scenario_id', 'in', self.ids), ('sent_at', '>=', since)], ['scenario_id'], ['__count']))
        last = dict(Event._read_group([('scenario_id', 'in', self.ids)], ['scenario_id'], ['sent_at:max']))
        for scenario in self:
            scenario.sent_count = total.get(scenario, 0)
            scenario.sent_7d = recent.get(scenario, 0)
            scenario.last_sent_at = last.get(scenario, False)

    @api.constrains('template', 'line_template', 'wait_minutes', 'repeat_minutes', 'max_count')
    def _check_values(self):
        for scenario in self:
            allowed = {n.strip() for n in (scenario.placeholders or '').split(',') if n.strip()}
            for text in (scenario.template, scenario.line_template):
                if not text:
                    continue
                try:
                    names = {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None}
                except ValueError:
                    raise ValidationError(_('Mẫu tin "%s" có dấu ngoặc nhọn chưa đóng/mở đúng.') % scenario.name) from None
                wrong = sorted(n for n in names if not FIELD_RE.match(n) or n not in allowed)
                if wrong:
                    raise ValidationError(_('Mẫu tin "%s" dùng biến không có: %s. Biến dùng được: %s')
                                          % (scenario.name, ', '.join('{%s}' % n for n in wrong), scenario.placeholders or '(không có)'))
            for field in ('wait_minutes', 'repeat_minutes', 'max_count'):
                label = getattr(scenario, field.replace('minutes', 'label').replace('max_count', 'max_label'), None)
                if label and scenario[field] < 1:
                    raise ValidationError(_('"%s" của kịch bản "%s" phải ≥ 1.') % (label, scenario.name))

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group('base.group_system') and set(vals) - {'enabled'}:
            raise UserError(_('Chỉ quản trị viên được sửa thời gian và mẫu tin; quản lý chỉ bật/tắt kịch bản.'))
        return super().write(vals)

    def action_save_scenario(self):
        """Nút "Lưu" trên thanh đầu form (Odoo tự lưu trước khi gọi nút)."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.name, 'message': _('Đã lưu.'), 'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_reset_template(self):
        for scenario in self:
            scenario.write({'template': scenario.default_template, 'line_template': scenario.default_line_template})

    def action_open_events(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Nhật ký: %s') % self.name, 'res_model': 'dac.pancake.remind.event',
                'view_mode': 'list,form', 'domain': [('scenario_id', '=', self.id)]}

    # ── Dùng trong code ──────────────────────────────────────────────────────

    @api.model
    def _get(self, code):
        # sudo(): cron không có user; chỉ đọc kịch bản của chính addon này
        scenario = self.sudo().search([('code', '=', code)], limit=1)
        if not scenario:
            raise UserError(_('Thiếu kịch bản nhắc "%s" (cập nhật lại addon).') % code)
        return scenario

    @api.model
    def _on(self, code):
        return self._get(code).enabled

    def _render_template(self, template, values):
        try:
            return template.format_map(_Missing(values))
        except (ValueError, IndexError, KeyError, AttributeError):
            return template

    def _render(self, values, line=False):
        """Mẫu tin (hoặc mẫu dòng) với `values`; mẫu trống thì dùng mẫu mặc định."""
        self.ensure_one()
        template = (self.line_template or self.default_line_template) if line else (self.template or self.default_template)
        text = self._render_template(template or '', values)
        return '\n'.join(part for part in text.split('\n') if part.strip())  # bỏ dòng trống (vd chưa có tóm tắt)

    def _log(self, messages, conversation=None, wait=None, appointment=None, level=False, is_test=False, count=1):
        """Ghi Nhật ký nhắc cho các tin vừa xếp hàng gửi."""
        self.ensure_one()
        if not messages:
            return
        # sudo(): cron ghi nhật ký
        self.env['dac.pancake.remind.event'].sudo().create({
            'scenario_id': self.id, 'sent_at': fields.Datetime.now(), 'conversation_id': conversation.id if conversation else False,
            'wait_id': wait.id if wait else False, 'appointment_id': appointment.id if appointment else False, 'level': level or False,
            'is_test': bool(is_test), 'customer_count': count, 'text': messages[:1].text, 'message_ids': [(6, 0, messages.ids)],
            'channel_names': ', '.join(sorted(set(messages.mapped('channel_id.name')))),
        })


class PancakeRemindEvent(models.Model):
    """Một lần nhắc đã gửi (theo kịch bản)."""
    _name = 'dac.pancake.remind.event'
    _description = 'Nhật ký nhắc khách Pancake'
    _order = 'sent_at desc, id desc'
    _rec_name = 'scenario_id'

    scenario_id = fields.Many2one('dac.pancake.remind.scenario', string='Kịch bản', required=True, ondelete='cascade', index=True)
    sent_at = fields.Datetime(string='Lúc', required=True, index=True)
    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại', ondelete='set null', index=True)
    customer_name = fields.Char(string='Khách', related='conversation_id.customer_name')
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    wait_id = fields.Many2one('dac.pancake.remind.wait', string='Khách chờ', ondelete='set null')
    appointment_id = fields.Many2one('dac.pancake.appointment', string='Phiếu hẹn', ondelete='set null')
    level = fields.Selection([('urgent', '🚨 Khẩn'), ('high', '⚠️ Chú ý'), ('normal', '🔔 Thường'), ('none', '⚪ Không cần trả lời')],
        string='Mức độ')
    customer_count = fields.Integer(string='Số khách / việc', default=1)
    text = fields.Text(string='Nội dung')
    channel_names = fields.Char(string='Gửi vào')
    message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_pancake_remind_event_message_rel', 'event_id', 'message_id',
        string='Tin Zalo')
    is_test = fields.Boolean(string='Dữ liệu thử nghiệm')
