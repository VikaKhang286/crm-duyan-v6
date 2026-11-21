"""Thiết lập chung (một dòng) và cửa sổ "Chạy phân tích" nhanh."""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .chat_analysis import READ_MODES

DEPTHS = [
    ('quick', 'Nhanh: AI đọc 60 hội thoại'),
    ('standard', 'Chuẩn: AI đọc 150 hội thoại'),
    ('full', 'Đầy đủ: AI đọc mọi hội thoại thỏa điều kiện (tối đa 1.500)'),
    ('stats', 'Chỉ thống kê số liệu, không dùng AI'),
]
DEPTH_LIMITS = {'quick': 60, 'standard': 150, 'full': 1500, 'stats': 0}
PERIODS = [('7', '7 ngày qua'), ('30', '30 ngày qua'), ('90', '90 ngày qua'), ('180', '180 ngày qua'), ('custom', 'Tự chọn ngày')]
MINUTES_PER_BATCH = 1.5  # đo thực tế với Claude Haiku có ảnh


class ChatConfig(models.Model):
    """Thiết lập mặc định cho phân tích chat (dùng khi tạo đợt mới, đợt tự động hằng tuần)."""
    _name = 'dac.chat.config'
    _description = 'Thiết lập phân tích chat & chatbot'

    name = fields.Char(default='Thiết lập phân tích chat', required=True)
    ai_model_id = fields.Many2one('dac.ai.model', string='Model AI mặc định', ondelete='set null',
        domain="[('connection_id.active', '=', True)]",
        help='Model đọc hội thoại, Agent điều phối và viết system prompt. Trống = thứ tự kết nối chung.')
    ai_fallback = fields.Boolean(string='Model lỗi thì dùng thứ tự chung', default=True)
    days_back = fields.Integer(string='Số ngày gần nhất', default=90)
    conversation_limit = fields.Integer(string='Số hội thoại tối đa cho AI đọc', default=150)
    read_mode = fields.Selection(READ_MODES, string='Chọn hội thoại', default='mix', required=True)
    customer_scope = fields.Selection([('new', 'Chỉ khách mới'), ('all', 'Khách mới và khách cũ')], string='Loại khách',
                                      default='new', required=True)
    only_unread = fields.Boolean(string='Chỉ đọc hội thoại chưa đọc', default=True)
    min_messages = fields.Integer(string='Bỏ hội thoại ít hơn (tin)', default=4)
    read_images = fields.Boolean(string='AI xem ảnh', default=True)
    images_per_conversation = fields.Integer(string='Số ảnh tối đa mỗi hội thoại', default=10)
    batch_size = fields.Integer(string='Số hội thoại mỗi lần gọi AI', default=12)
    auto_weekly = fields.Boolean(string='Tự chạy hằng tuần', default=False,
        help='Mỗi thứ 2 lúc 6:00 tự tạo một đợt cho 7 ngày qua theo thiết lập này, chỉ đọc hội thoại chưa đọc. '
             'Bản system prompt mới luôn là bản nháp, cần người kích hoạt.')
    price_in = fields.Float(string='Giá token vào (USD / 1 triệu)', default=0.10, digits=(8, 3),
        help='Chỉ để ước tính chi phí trong màn hình Demo chatbot. Mặc định: giá Claude Haiku 5.5 của Anthropic.')
    price_out = fields.Float(string='Giá token ra (USD / 1 triệu)', default=0.50, digits=(8, 3))
    usd_rate = fields.Float(string='Tỉ giá USD → VNĐ', default=26000, digits=(10, 0))
    last_weekly_run_id = fields.Many2one('dac.chat.analysis', string='Đợt tự động gần nhất', readonly=True, ondelete='set null')
    ai_ready = fields.Boolean(string='Có kết nối AI dùng được', compute='_compute_ai_ready')

    _sql_constraints = [
        ('images_check', 'CHECK(images_per_conversation BETWEEN 0 AND 10)', 'Số ảnh mỗi hội thoại từ 0 đến 10.'),
        ('positive_check', 'CHECK(days_back > 0 AND batch_size > 0 AND conversation_limit >= 0)',
         'Số ngày và số hội thoại mỗi lần gọi phải lớn hơn 0.'),
    ]

    def _compute_ai_ready(self):
        ready = self.env['dac.ai.connection']._ready() or self.env['dac.chat.task']._any_model()
        for config in self:
            config.ai_ready = ready or bool(config.ai_model_id)

    @api.model
    def _get(self):
        # sudo(): một dòng thiết lập dùng chung, đọc được trong cron và khi tạo đợt
        return self.sudo().search([], limit=1) or self.sudo().create({})

    @api.model
    def action_open(self):
        return {'type': 'ir.actions.act_window', 'name': 'Thiết lập phân tích chat', 'res_model': self._name, 'res_id': self._get().id,
                'view_mode': 'form', 'target': 'current'}

    def action_open_tasks(self):
        """Mở màn hình chọn model và thinking cho từng tác vụ AI."""
        return self.env['dac.chat.task'].action_open()

    def action_save(self):
        """Nút "Lưu" (Odoo tự lưu trước khi gọi nút) rồi quay lại trang Tổng quan."""
        return self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_dashboard_action')

    def _analysis_vals(self, date_from, date_to, **overrides):
        """Giá trị tạo một đợt phân tích theo thiết lập này."""
        self.ensure_one()
        vals = {
            'date_from': date_from, 'date_to': date_to, 'ai_model_id': self.ai_model_id.id, 'ai_fallback': self.ai_fallback,
            'ai_conversation_limit': self.conversation_limit, 'read_mode': self.read_mode, 'customer_scope': self.customer_scope,
            'only_unread': self.only_unread, 'min_messages': self.min_messages, 'read_images': self.read_images,
            'images_per_conversation': self.images_per_conversation, 'ai_batch_size': self.batch_size,
        }
        vals.update(overrides)
        return vals


class ChatRunWizard(models.TransientModel):
    """Chạy phân tích nhanh từ trang Tổng quan: chọn kỳ + mức đọc, xem ước tính, bấm Chạy."""
    _name = 'dac.chat.run.wizard'
    _description = 'Chạy phân tích chat'

    def _config(self):
        return self.env['dac.chat.config']._get()

    period = fields.Selection(PERIODS, string='Kỳ phân tích', default='90', required=True)
    date_from = fields.Date(string='Từ ngày')
    date_to = fields.Date(string='Đến ngày')
    depth = fields.Selection(DEPTHS, string='Mức đọc', default='standard', required=True)
    ai_model_id = fields.Many2one('dac.ai.model', string='Model AI', domain="[('connection_id.active', '=', True)]",
                                  default=lambda self: self._config().ai_model_id)
    customer_scope = fields.Selection([('new', 'Chỉ khách mới'), ('all', 'Khách mới và khách cũ')], string='Loại khách',
                                      default=lambda self: self._config().customer_scope, required=True)
    only_unread = fields.Boolean(string='Chỉ đọc hội thoại chưa đọc', default=lambda self: self._config().only_unread)
    read_images = fields.Boolean(string='AI xem ảnh', default=lambda self: self._config().read_images)
    estimate = fields.Char(string='Ước tính', compute='_compute_estimate')

    @api.depends('depth', 'period', 'date_from', 'date_to')
    def _compute_estimate(self):
        config = self._config()
        for wizard in self:
            limit = DEPTH_LIMITS[wizard.depth]
            if not limit:
                wizard.estimate = 'Khoảng 1 phút. Không gọi AI.'
                continue
            batches = -(-limit // max(config.batch_size, 1))
            minutes = round(batches * MINUTES_PER_BATCH) + 3
            wizard.estimate = ('Tối đa %s hội thoại ≈ %s lô AI, khoảng %s phút nếu đủ hội thoại (ít hơn nếu kỳ có ít hội thoại). '
                               'Theo dõi tiến độ ngay trên trang.') % ('{:,}'.format(limit).replace(',', '.'), batches, minutes)

    def _dates(self):
        today = fields.Date.context_today(self)
        if self.period == 'custom':
            if not self.date_from or not self.date_to or self.date_from > self.date_to:
                raise UserError('Chọn "Từ ngày" trước hoặc bằng "Đến ngày".')
            return self.date_from, self.date_to
        return today - timedelta(days=int(self.period)), today

    def action_start(self):
        self.ensure_one()
        date_from, date_to = self._dates()
        label = dict(PERIODS)[self.period] if self.period != 'custom' else '%s–%s' % (date_from.strftime('%d/%m'), date_to.strftime('%d/%m'))
        vals = self._config()._analysis_vals(
            date_from, date_to, ai_model_id=self.ai_model_id.id, ai_conversation_limit=DEPTH_LIMITS[self.depth],
            customer_scope=self.customer_scope, only_unread=self.only_unread, read_images=self.read_images,
            name='Phân tích %s (%s)' % (label.lower(), fields.Date.context_today(self).strftime('%d/%m/%Y')))
        analysis = self.env['dac.chat.analysis'].create(vals)
        analysis.action_run()
        return {'type': 'ir.actions.act_window', 'res_model': 'dac.chat.analysis', 'res_id': analysis.id, 'view_mode': 'form',
                'target': 'current'}
