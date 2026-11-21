import json

from odoo import api, fields, models
from odoo.exceptions import UserError

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
    remind_real_channels = fields.Boolean(string='Gửi nhắc vào nhóm thật', default=False,
        help='Tắt (mặc định, đang chạy thử): mọi tin nhắc khách, tin gom, lịch hẹn, nhắc việc từ hôm qua — kể cả của khách thật — '
             'CHỈ gửi vào hội thoại Zalo đánh dấu "Thử nghiệm". Chỉ bật khi đã duyệt gửi vào nhóm kinh doanh thật.')
    last_digest_at = fields.Datetime(string='Gửi tin gom gần nhất', readonly=True)
    review_date = fields.Date(string='Đọc lại cho ngày', compute='_compute_followup_state')
    review_done = fields.Boolean(string='Đọc lại xong', compute='_compute_followup_state')
    morning_sent_date = fields.Date(string='Đã gửi nhắc việc ngày', compute='_compute_followup_state')
    ai_check = fields.Boolean(string='AI đọc tin trước khi nhắc', default=True,
        help='Trước lần nhắc đầu, AI (Cài đặt → Kết nối AI dùng chung) đọc đoạn chat: bỏ qua khách không cần trả lời nữa '
             '(chỉ "ok em", cảm ơn, biểu tượng sau tin của sale, tin rác), đánh giá mức gấp và tóm tắt khách cần gì. '
             'Tắt hoặc AI lỗi thì dùng luật từ khóa.')
    stats_real_channels = fields.Boolean(string='Gửi thống kê vào nhóm thật', default=False,
        help='Tắt (mặc định): thống kê khách liên hệ lúc 8:00 / 11:30 / 17:00 chỉ gửi vào hội thoại Zalo đánh dấu "Thử nghiệm". '
             'Chỉ bật khi đã duyệt để gửi vào nhóm kinh doanh thật.')
    backfill_from = fields.Date(string='Tính thống kê từ ngày',
        help='Dùng cho nút "Tính lại thống kê": quét lại lead và hội thoại Pancake từ ngày này để lập bảng thống kê khách mới.')
    live_check = fields.Boolean(string='Tải lại tin của khách ngay trước khi nhắc', default=True,
        help='Bật: trước khi nhắc, lấy lại tin mới nhất của đúng hội thoại đó để không nhắc nhầm khi nhân viên vừa trả lời.')
    greeting_texts = fields.Text(string='Câu chào tự động',
        help='Mỗi dòng một câu (đầu câu là đủ). Tin của page bắt đầu bằng các câu này — cùng các câu chỉ chào hỏi như "Dạ em chào chị ạ", '
             '"Cảm ơn anh đã liên hệ" — KHÔNG tính là nhân viên đã trả lời khách.')
    chatbot_wait = fields.Boolean(string='Chờ chatbot bàn giao', default=False,
        help='Bật khi đã có chatbot AI chào/hỏi thông tin khách trên Pancake: trong lúc chatbot đang trò chuyện thì chưa nhắc vào nhóm; '
             'chỉ nhắc khi chatbot bàn giao (tin có câu bàn giao bên dưới) hoặc chatbot im lặng 10 phút sau tin khách.')
    handover_texts = fields.Text(string='Câu chatbot bàn giao cho nhân viên',
        help='Mỗi dòng một cụm, vd "em chuyển nhân viên hỗ trợ", "nhân viên sẽ liên hệ". Tin chatbot có cụm này = bàn giao → nhắc ngay.')
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

    def _compute_followup_state(self):
        state = self.env['dac.pancake.followup.state']._get()
        for config in self:
            config.review_date, config.review_done, config.morning_sent_date = state.review_date, state.review_done, state.morning_sent_date

    @api.depends('page_ids')
    def _compute_effective_pages(self):
        for config in self:
            config.effective_page_ids = self.env['dac.pancake.remind.wait']._pages(config)

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

    def action_open_staff(self):
        self.env['dac.pancake.staff'].action_refresh()
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.pancake_staff_action')

    def action_open_scenarios(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.scenario_action')

    def action_open_events(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.event_action')

    def action_open_appointments(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_pancake_remind.appointment_action')

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
