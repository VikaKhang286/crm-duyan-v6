"""Thiết lập chung (một dòng) và cửa sổ "Chạy phân tích" nhanh."""
import re
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .chat_analysis import READ_MODES

DEFAULT_GUIDANCE = """- Giờ làm việc: thứ 2 – thứ 7, 8:00–12:00 và 13:30–17:30 (nhân viên nghỉ trưa 12:00–13:30); Chủ nhật nghỉ.
- Khách nhắn qua Zalo: KHÔNG xin số điện thoại/Zalo (đã liên lạc được trên Zalo). Khách nhắn Facebook: có thể xin, không bắt buộc.
- Chatbot chỉ gửi được tin chữ: chưa gửi ảnh mẫu, file hay gọi điện được.
- Không hứa mốc giờ cố định (vd "trong 30 phút", "trước 11 giờ"); chỉ dùng mốc làm việc kế tiếp do hệ thống điền.
- Bộ quy tắc càng gọn càng tốt: mỗi ý một quy tắc, không trùng, không mâu thuẫn.
- Khách không được biết đang nhắn với AI/chatbot: không tự giới thiệu "em là trợ lý…", không nhắc AI/bot; chào hỏi tự nhiên như nhân viên."""
UNSURE_GUIDANCE = ('- Không nói "chưa chắc" về việc bên em có làm hay không: Duy An thiết kế, in ấn mọi chất liệu, thi công bảng hiệu, '
                   'mặt dựng alu và thi công trọn gói cửa hàng (vd VinFast). Khách đưa ít thông tin thì chào rồi hỏi thêm một câu cụ thể.')
IDENTITY_GUIDANCE = '- Khách không được biết đang nhắn với AI/chatbot: không tự giới thiệu "em là trợ lý…", không nhắc AI/bot; chào hỏi tự nhiên như nhân viên.'

# Lịch nghỉ lễ 2027 đã công bố (Thông báo 11483/TB-BNV ngày 9/10/2026 của Bộ Nội vụ; Bộ luật Lao động sửa đổi thông qua 21/11/2026,
# hiệu lực 1/3/2027 thêm 27/7 và 24/11). Tết Nguyên đán theo lịch công chức: doanh nghiệp chọn 5 ngày và báo trước 30 ngày → sửa theo DAC.
DEFAULT_HOLIDAYS = """01/01/2027 Tết Dương lịch
04/02/2027-10/02/2027 Tết Nguyên đán Đinh Mùi (lịch công chức; sửa theo lịch nghỉ của Duy An)
16/04/2027 Giỗ Tổ Hùng Vương
30/04/2027-01/05/2027 Ngày Chiến thắng 30/4 và Quốc tế Lao động 1/5
03/05/2027 Nghỉ bù 1/5 (rơi vào thứ 7)
27/07/2027 Ngày Thương binh, liệt sĩ
02/09/2027-03/09/2027 Quốc khánh
24/11/2027 Ngày Văn hóa Việt Nam"""
HOLIDAY_LINE = re.compile(r'^\s*(\d{1,2}/\d{1,2}/\d{4})(?:\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4}))?\s*(.*)$')

DEPTHS = [
    ('quick', 'Nhanh: AI đọc 60 hội thoại'),
    ('standard', 'Chuẩn: AI đọc 150 hội thoại'),
    ('full', 'Nhiều: AI đọc tối đa 1.500 hội thoại'),
    ('all', 'Tất cả hội thoại chưa đọc (không giới hạn)'),
    ('stats', 'Chỉ thống kê số liệu, không dùng AI'),
]
DEPTH_LIMITS = {'quick': 60, 'standard': 150, 'full': 1500, 'all': 100000, 'stats': 0}
PERIODS = [('7', '7 ngày qua'), ('30', '30 ngày qua'), ('90', '90 ngày qua'), ('180', '180 ngày qua'),
           ('all', 'Toàn bộ lịch sử (từ tin cũ nhất)'), ('custom', 'Tự chọn ngày')]
MINUTES_PER_BATCH = 1.5        # đo thực tế với Claude Haiku có ảnh
TOKENS_PER_BATCH = (28000, 10000)  # token vào / ra trung bình mỗi lô (đo thực tế 20/11/2026, có ảnh, qua 9router)


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
        help='Chỉ để ước tính chi phí trong màn hình Huấn luyện & kiểm thử. Mặc định: giá Claude Haiku 5.5 của Anthropic.')
    price_out = fields.Float(string='Giá token ra (USD / 1 triệu)', default=0.50, digits=(8, 3))
    usd_rate = fields.Float(string='Tỉ giá USD → VNĐ', default=26000, digits=(10, 0))
    agent_guidance = fields.Text(string='Định hướng của quản lý cho Agent', default=lambda self: DEFAULT_GUIDANCE,
        help='Agent tối ưu quy tắc và Agent viết system prompt đọc phần này, ưu tiên cao nhất khi quy tắc mâu thuẫn. '
             'Ghi các quyết định của quản lý: giờ làm, có xin SĐT không, chatbot làm được gì.')
    greeting_active = fields.Boolean(string='Bật chào khách mới', default=False,
        help='Bot soạn lời chào cho khách mới nhắn Pancake và gửi người duyệt qua Zalo; chỉ gửi khách khi người duyệt trả lời "duyệt".')
    greeting_activated_at = fields.Datetime(string='Bật lúc', readonly=True,
        help='Chỉ chào khách nhắn sau mốc này (không chào tồn đọng cũ).')
    greeting_approver_channel_id = fields.Many2one('dac.zalo.channel', string='Người duyệt (Zalo)', ondelete='set null',
        domain="[('thread_type', '=', 'user')]", help='Chat riêng Zalo của người duyệt lời chào (Trợ lý Zalo → Hội thoại).')
    greeting_handoff_channel_id = fields.Many2one('dac.zalo.channel', string='Nhóm báo nhân viên vào tiếp', ondelete='set null',
        domain="[('thread_type', '=', 'group')]",
        help='Khi bot cần người thật (không chắc, khách cần báo giá/chốt/cọc…), bot báo ngay vào nhóm này và tag người phụ trách hội thoại.')
    holidays_text = fields.Text(string='Ngày nghỉ lễ', default=lambda self: DEFAULT_HOLIDAYS,
        help='Mỗi dòng một ngày hoặc một khoảng: "dd/mm/yyyy Tên" hoặc "dd/mm/yyyy-dd/mm/yyyy Tên". Chatbot coi các ngày này là ngoài giờ: '
             'vẫn hỏi thông tin rồi hẹn mốc làm việc kế tiếp (đã bỏ qua ngày lễ).')
    last_weekly_run_id = fields.Many2one('dac.chat.analysis', string='Đợt tự động gần nhất', readonly=True, ondelete='set null')
    ai_ready = fields.Boolean(string='Có kết nối AI dùng được', compute='_compute_ai_ready')

    _sql_constraints = [
        ('images_check', 'CHECK(images_per_conversation BETWEEN 0 AND 10)', 'Số ảnh mỗi hội thoại từ 0 đến 10.'),
        ('positive_check', 'CHECK(days_back > 0 AND batch_size > 0 AND conversation_limit >= 0)',
         'Số ngày và số hội thoại mỗi lần gọi phải lớn hơn 0.'),
    ]

    def write(self, vals):
        if vals.get('greeting_active') and not all(self.mapped('greeting_active')):
            vals = dict(vals, greeting_activated_at=fields.Datetime.now())
        return super().write(vals)

    def _holidays(self):
        """{date: tên ngày lễ} từ ô Ngày nghỉ lễ (dòng sai định dạng thì bỏ qua)."""
        out = {}
        for line in (self.holidays_text or '').splitlines():
            match = HOLIDAY_LINE.match(line)
            if not match:
                continue
            try:
                start = datetime.strptime(match.group(1), '%d/%m/%Y').date()
                end = datetime.strptime(match.group(2), '%d/%m/%Y').date() if match.group(2) else start
            except ValueError:
                continue
            for offset in range(min((end - start).days, 60) + 1):
                out[start + timedelta(days=offset)] = (match.group(3) or 'Nghỉ lễ').strip()
        return out

    def action_open_greetings(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_greeting_action')

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


class _Rollback(Exception):
    """Dùng để huỷ savepoint sau khi đếm thử."""


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
    summary_scope = fields.Selection([('all_time', 'Mọi hội thoại đã gán nhãn (gộp cả các đợt trước)'),
                                      ('this_analysis', 'Chỉ hội thoại đọc trong đợt này')],
                                     string='Agent tổng hợp trên', default='all_time', required=True)
    estimate = fields.Char(string='Ước tính', compute='_compute_estimate')
    counted = fields.Boolean(readonly=True)
    count_text = fields.Text(string='Kết quả đếm', readonly=True)

    @api.depends('depth', 'period', 'date_from', 'date_to')
    def _compute_estimate(self):
        config = self._config()
        for wizard in self:
            limit = DEPTH_LIMITS[wizard.depth]
            if not limit:
                wizard.estimate = 'Khoảng 1 phút. Không gọi AI.'
            elif wizard.depth == 'all':
                wizard.estimate = 'Không giới hạn số hội thoại: bấm "Đếm trước" để biết số hội thoại chưa đọc, thời gian và token.'
            else:
                batches = -(-limit // max(config.batch_size, 1))
                minutes = round(batches * MINUTES_PER_BATCH) + 3
                wizard.estimate = ('Tối đa %s hội thoại ≈ %s lô AI, khoảng %s phút nếu đủ hội thoại (ít hơn nếu kỳ có ít hội thoại). '
                                   'Theo dõi tiến độ ngay trên trang.') % ('{:,}'.format(limit).replace(',', '.'), batches, minutes)

    def _vals(self):
        date_from, date_to = self._dates()
        label = dict(PERIODS)[self.period] if self.period != 'custom' else '%s–%s' % (date_from.strftime('%d/%m'), date_to.strftime('%d/%m'))
        return self._config()._analysis_vals(
            date_from, date_to, ai_model_id=self.ai_model_id.id, ai_conversation_limit=DEPTH_LIMITS[self.depth],
            customer_scope=self.customer_scope, only_unread=self.only_unread, read_images=self.read_images,
            summary_scope=self.summary_scope,
            name='Phân tích %s (%s)' % (label.lower(), fields.Date.context_today(self).strftime('%d/%m/%Y')))

    def action_count(self):
        """Đếm trước (chạy thử phần thống kê rồi huỷ, không lưu gì): số hội thoại chưa đọc, số lô, thời gian, token, chi phí."""
        self.ensure_one()
        vals = self._vals()
        result = {}
        try:
            with self.env.cr.savepoint():
                analysis = self.env['dac.chat.analysis'].create(dict(vals, state='queued'))
                analysis._compute_stats()
                result = {'candidates': analysis.candidate_count, 'conversations': analysis.conversation_count,
                          'batches': analysis.ai_batches_total or len(__import__('json').loads(analysis.batches_json or '[]')),
                          'picked': len(__import__('json').loads(analysis.sample_json or '[]'))}
                raise _Rollback()
        except _Rollback:
            pass
        config = self._config()
        batches = result.get('batches', 0)
        tokens_in, tokens_out = batches * TOKENS_PER_BATCH[0], batches * TOKENS_PER_BATCH[1]
        usd = (tokens_in * config.price_in + tokens_out * config.price_out) / 1e6
        hours = batches * MINUTES_PER_BATCH / 60
        fmt = lambda n: '{:,}'.format(int(n)).replace(',', '.')
        self.write({'counted': True, 'count_text': (
            '%s hội thoại có tin trong kỳ · %s hội thoại thỏa điều kiện chưa đọc · AI sẽ đọc %s hội thoại trong %s lô.\n'
            'Thời gian ước tính: khoảng %s (chạy nền, theo dõi trên trang; bấm "Dừng" bất cứ lúc nào, lần sau chạy lại sẽ tiếp tục '
            'từ hội thoại chưa đọc).\nToken ước tính: ~%s vào / ~%s ra ≈ %sđ theo giá trong Thiết lập (qua 9router gói thuê bao '
            'thì trừ vào hạn mức).') % (
            fmt(result.get('conversations', 0)), fmt(result.get('candidates', 0)), fmt(result.get('picked', 0)), fmt(batches),
            ('%.1f giờ' % hours) if hours >= 1 else ('%d phút' % max(1, round(hours * 60))), fmt(tokens_in), fmt(tokens_out),
            fmt(usd * config.usd_rate))})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'name': 'Chạy phân tích mới'}

    def _dates(self):
        today = fields.Date.context_today(self)
        if self.period == 'all':
            self.env.flush_all()
            self.env.cr.execute('SELECT MIN(inserted_at) FROM dac_pancake_message')
            first = self.env.cr.fetchone()[0]
            return (first.date() if first else today - timedelta(days=365)), today
        if self.period == 'custom':
            if not self.date_from or not self.date_to or self.date_from > self.date_to:
                raise UserError('Chọn "Từ ngày" trước hoặc bằng "Đến ngày".')
            return self.date_from, self.date_to
        return today - timedelta(days=int(self.period)), today

    def action_start(self):
        self.ensure_one()
        analysis = self.env['dac.chat.analysis'].create(self._vals())
        analysis.action_run()
        return {'type': 'ir.actions.act_window', 'res_model': 'dac.chat.analysis', 'res_id': analysis.id, 'view_mode': 'form',
                'target': 'current'}

