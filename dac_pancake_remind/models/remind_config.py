import json

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.exceptions import ValidationError

# Luồng Pancake cũ (CRM_DAC + mục hội thoại của Kết nối Pancake): chỉ ẩn menu và tắt cron, không gỡ, không xóa dữ liệu.
LEGACY_MENUS = ('CRM_DAC.menu_pancake_message_dashboard_main', 'CRM_DAC.menu_page_fm_sync_root',
                'dac_pancake_connector.menu_pancake_connector_chat', 'dac_pancake_connector.menu_pancake_connector_bulk')
LEGACY_CRONS = ('CRM_DAC.cron_pancake_full_sync', 'CRM_DAC.cron_pancake_quick_sync', 'CRM_DAC.cron_pancake_smart_message_sync',
                'CRM_DAC.cron_pancake_deep_sync_continuous', 'CRM_DAC.cron_pancake_full_message_sync_job_runner',
                'CRM_DAC.cron_pancake_message_sync_log_cleanup')


class PancakeRemindConfig(models.Model):
    """Cấu hình (một dòng duy nhất) nhắc khách Pancake chưa được trả lời."""
    _name = 'dac.pancake.remind.config'
    _description = 'Cấu hình nhắc khách Pancake'

    name = fields.Char(default='Nhắc khách Pancake', required=True)
    active = fields.Boolean(string='Bật nhắc', default=False,
        help='Tắt: không quét và không nhắc. Khi bật, chỉ xét tin nhắn đến SAU thời điểm bật (không nhắc lại khách cũ).')
    activated_at = fields.Datetime(string='Bật lúc', readonly=True,
        help='Chỉ xét tin của khách nhắn sau mốc này.')
    wait_minutes = fields.Integer(string='Khách mới: chờ trả lời tối đa (phút làm việc)', default=15, required=True)
    repeat_minutes = fields.Integer(string='Khách mới: nhắc lại mỗi (phút làm việc)', default=10, required=True,
        help='Khách mới vẫn chưa được trả lời (kể cả khi đã có người thả cảm xúc lên tin nhắc) thì cứ sau khoảng này lại nhắc, '
             'bằng cách tag tên và trả lời vào ảnh nhắc lần đầu, cho tới khi có nhân viên trả lời trên Pancake.')
    old_wait_minutes = fields.Integer(string='Khách cũ: chờ trả lời tối đa (phút làm việc)', default=30, required=True,
        help='Khách cũ (đã từng được nhân viên trả lời hoặc đã có đơn): quá hạn này thì AI đọc tin một lần, cần trả lời thì nhắc đúng một lần.')
    max_reminders = fields.Integer(string='(Không dùng nữa)', default=2, required=True)
    ai_check = fields.Boolean(string='AI đọc tin trước khi nhắc', default=True,
        help='Trước lần nhắc đầu, Gemini (Trợ lý Zalo → Gemini) đọc đoạn chat để bỏ qua khách không cần trả lời nữa: khách chỉ "ok em", '
             'cảm ơn, gửi biểu tượng sau tin của sale, tin rác/hệ thống. Gemini chưa bật hoặc lỗi thì dùng luật từ khóa.')
    only_new_customers = fields.Boolean(string='Chỉ nhắc khách mới', default=False,
        help='Bật: bỏ qua khách cũ. Khách mới = hội thoại chưa từng có nhân viên trả lời và khách chưa có đơn hàng nào trong Odoo.')
    backfill_from = fields.Date(string='Tính thống kê từ ngày',
        help='Dùng cho nút "Tính lại thống kê": quét lại lead và hội thoại Pancake từ ngày này để lập bảng thống kê khách mới.')
    live_check = fields.Boolean(string='Tải lại tin của khách ngay trước khi nhắc', default=True,
        help='Bật: trước khi nhắc, lấy lại tin mới nhất của đúng hội thoại đó để không nhắc nhầm khi nhân viên vừa trả lời.')
    page_ids = fields.Many2many('dac.pancake.page', 'dac_pancake_remind_config_dpage_rel', 'config_id', 'page_id',
        string='Chỉ áp dụng cho page',
        help='Page Pancake dùng cho nhắc khách chưa trả lời, nhắc tổng, thống kê khách mới và kiểm tra trùng số lead. '
             'Để trống = mọi page đã thêm ở mục Page Pancake.')
    effective_page_ids = fields.Many2many('dac.pancake.page', string='Page đang áp dụng', compute='_compute_effective_pages')
    legacy_state = fields.Text(string='Trạng thái luồng cũ trước khi tắt', readonly=True, copy=False,
        help='Ghi lại menu/cron của luồng Pancake cũ (CRM_DAC) đang bật trước khi tắt, để bật lại đúng như cũ.')
    legacy_disabled = fields.Boolean(string='Đã tắt luồng Pancake cũ', compute='_compute_legacy_disabled')
    wait_count = fields.Integer(string='Khách đang chờ', compute='_compute_counts')
    reminded_24h = fields.Integer(string='Đã nhắc 24 giờ qua', compute='_compute_counts')

    @api.depends('page_ids')
    def _compute_effective_pages(self):
        for config in self:
            config.effective_page_ids = self.env['dac.pancake.remind.wait']._pages(config)

    @api.constrains('wait_minutes', 'repeat_minutes', 'old_wait_minutes')
    def _check_numbers(self):
        for config in self:
            if min(config.wait_minutes, config.repeat_minutes, config.old_wait_minutes) < 1:
                raise ValidationError('Số phút phải ≥ 1.')

    def _compute_counts(self):
        Wait = self.env['dac.pancake.remind.wait']
        waiting = Wait.search_count([('state', '=', 'waiting')])
        recent = Wait.search_count([('last_reminded_at', '>=', fields.Datetime.subtract(fields.Datetime.now(), hours=24))])
        for config in self:
            config.wait_count = waiting
            config.reminded_24h = recent

    @api.model
    def _get(self):
        # sudo(): cron/luồng nền không có user; chỉ đọc cấu hình của chính addon này
        return self.sudo().search([], limit=1) or self.sudo().create({})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('active'):
                vals.setdefault('activated_at', fields.Datetime.now())
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('active'):
            for config in self.filtered(lambda c: not c.active):
                super(PancakeRemindConfig, config).write({'activated_at': fields.Datetime.now()})
        return super().write(vals)

    def action_backfill_contacts(self):
        """Lập (hoặc bổ sung) bảng thống kê khách mới từ dữ liệu có sẵn từ ngày `backfill_from`. An toàn khi bấm nhiều lần."""
        self.ensure_one()
        start = fields.Datetime.to_datetime(self.backfill_from) if self.backfill_from else fields.Datetime.subtract(fields.Datetime.now(), days=30)
        created = self.env['dac.new.contact']._backfill(self, start)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Đã tính lại thống kê', 'message': 'Thêm %s lượt khách mới vào bảng thống kê.' % created, 'type': 'success'}}

    def action_open_contacts(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.new_contact_action')

    def action_open_waits(self):
        return {'type': 'ir.actions.act_window', 'name': 'Khách Pancake đang chờ trả lời',
                'res_model': 'dac.pancake.remind.wait', 'view_mode': 'list,form'}

    def action_open_pages(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.pancake_page_action')

    # ── Luồng Pancake cũ ─────────────────────────────────────────────────────

    @api.depends('legacy_state')
    def _compute_legacy_disabled(self):
        for config in self:
            config.legacy_disabled = bool(config.legacy_state)

    def _legacy_records(self):
        records = []
        for xmlid in LEGACY_MENUS + LEGACY_CRONS:
            record = self.env.ref(xmlid, raise_if_not_found=False)
            if record:
                records.append((xmlid, record.sudo()))
        return records

    def _check_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise UserError('Chỉ quản trị viên được tắt/bật luồng Pancake cũ.')

    def action_disable_legacy(self):
        """Ẩn menu và tắt cron của luồng Pancake cũ; ghi lại cái nào đang bật để bật lại đúng như cũ. Không xóa dữ liệu."""
        self.ensure_one()
        self._check_admin()
        if self.legacy_state:
            return True
        # sudo(): menu/cron là bản ghi hệ thống; người bấm đã được kiểm tra là quản trị viên
        was_active = [xmlid for xmlid, record in self._legacy_records() if record.active]
        for xmlid, record in self._legacy_records():
            if record.active:
                record.active = False
        self.sudo().legacy_state = json.dumps(was_active or ['-'])
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_enable_legacy(self):
        self.ensure_one()
        self._check_admin()
        try:
            wanted = set(json.loads(self.legacy_state or '[]'))
        except ValueError:
            wanted = set()
        for xmlid, record in self._legacy_records():
            if xmlid in wanted:
                record.active = True
        self.sudo().legacy_state = False
        return {'type': 'ir.actions.client', 'tag': 'reload'}
