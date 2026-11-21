"""Đợt phân tích tin nhắn Pancake: thống kê cách nhân viên nhắn, cho model AI được chỉ định đọc hội thoại mẫu và đề xuất quy tắc.

Chạy nền bằng cron (bấm "Chạy phân tích" chỉ xếp hàng): bước 1 thống kê toàn bộ tin trong kỳ (SQL + Python, vài giây),
bước 2 AI đọc từng lô hội thoại mẫu (mỗi lần cron một lô, commit sau mỗi lô để không vượt giới hạn thời gian của worker),
bước 3 AI tổng hợp thành quy tắc đề xuất. Quy tắc chỉ có hiệu lực khi quản lý bấm "Duyệt".
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.addons.dac_pancake_remind.models.remind_wait import classify
from odoo.addons.dac_pancake_remind.models.staff import name_key
from odoo.addons.dac_push.models import working_time

from . import chat_stats as cs
from .ai_agent import AgentLoop, ai_call, fetch_image, vision_call
from .chat_insight import GROUPABLE, LABEL_SCHEMA, SCORE_LABELS, SCORES

_logger = logging.getLogger(__name__)

STATES = [('draft', 'Nháp'), ('queued', 'Đang chờ chạy'), ('ai', 'AI đang xử lý'), ('done', 'Xong'), ('failed', 'Lỗi')]
READ_MODES = [('mix', 'Hỗn hợp: tốt / chưa tốt / ngoài giờ'), ('recent', 'Gần nhất trước'), ('good', 'Ưu tiên hội thoại tốt'),
              ('bad', 'Ưu tiên hội thoại chưa tốt')]
CRON_BUDGET = 45            # giây: quá mốc này thì dừng, commit và hẹn cron chạy tiếp
MAP_TIMEOUT = (10, 90)      # mỗi lô hội thoại (có ảnh)
REDUCE_TIMEOUT = (10, 100)  # bước tổng hợp
COORD_ROUNDS = 8            # số vòng tối đa của Agent điều phối (mỗi vòng một lượt cron)
MAX_IMAGES_PER_BATCH = 30     # ảnh tối đa trong một lần gọi AI (lô nhiều ảnh thì ít hội thoại hơn)
MAX_IMAGES_PER_CONVERSATION = 10
IMAGE_FETCH_BUDGET = 60       # giây tải ảnh tối đa cho một lô
MEDIA_TYPES_TEXT = ('text', 'replied_message', 'styled_message')

AI_SYSTEM = """Bạn là chuyên gia phân tích hội thoại bán hàng của công ty Duy An (DAC): in ấn, bảng hiệu, hộp đèn, POSM, xe đẩy bán hàng,
website & marketing. Khách chủ yếu nhắn qua Zalo cá nhân của shop (qua Pancake). Nhiệm vụ: học cách nhân viên nhắn tin hiệu quả
để viết quy tắc cho một CHATBOT chào khách mới.

Phạm vi của chatbot (quy tắc phải nằm trong phạm vi này):
- ĐƯỢC: chào khách, hỏi nhu cầu, xin thông tin cần thiết (sản phẩm, kích thước, số lượng, chất liệu, file/nội dung, thời gian cần,
  khu vực, tên, SĐT/Zalo), tư vấn sơ bộ chung chung (giải thích loại sản phẩm/chất liệu phổ biến), hẹn nhân viên liên hệ/báo giá.
- KHÔNG ĐƯỢC: đưa con số giá hay báo giá, cam kết thời gian/tiến độ, xác nhận đơn, nhận cọc hay gửi số tài khoản, hứa giảm giá,
  quyết định thay nhân viên. Gặp các việc này thì hẹn nhân viên xử lý.

Yêu cầu khi viết:
- Không chép giá cụ thể, số tài khoản, SĐT, tên khách, tên nhân viên vào quy tắc hay câu mẫu.
- Câu mẫu viết đúng giọng nhân viên DAC (dựa trên dữ liệu: ngắn, xưng "em", có "dạ/ạ", "nha"), không văn mẫu tổng đài.
- Số liệu là tương quan, không phải nhân quả; nhóm ít hơn ~20 hội thoại thì không kết luận mạnh.
- Luôn trả kết quả bằng cách gọi đúng tool được yêu cầu."""

CATEGORY_ENUM = ['greeting', 'info', 'style', 'consult', 'after_hours', 'handoff', 'forbidden']
CATEGORY_HELP = ('greeting=chào hỏi; info=lấy thông tin; style=cách nhắn (số tin mỗi lượt, độ dài, xưng hô, giọng); '
                 'consult=tư vấn sơ bộ; after_hours=ngoài giờ làm việc; handoff=khi nào chuyển nhân viên; forbidden=không được làm')

REDUCE_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string', 'description': '5–10 câu: phong cách nhắn hiện tại và điều làm nên hội thoại tốt.'},
        'rules': {'type': 'array', 'description': '8–25 quy tắc cho chatbot, ưu tiên điều có bằng chứng.', 'items': {
            'type': 'object',
            'properties': {
                'category': {'type': 'string', 'enum': CATEGORY_ENUM, 'description': CATEGORY_HELP},
                'title': {'type': 'string', 'description': 'Tiêu đề ngắn.'},
                'rule': {'type': 'string', 'description': 'Quy tắc cụ thể, làm theo được.'},
                'example': {'type': 'string', 'description': 'Câu mẫu chatbot có thể gửi.'},
                'evidence': {'type': 'string', 'description': 'Bằng chứng: số liệu hoặc nhận xét từ hội thoại.'},
                'priority': {'type': 'string', 'enum': ['must', 'should', 'may']},
            },
            'required': ['category', 'title', 'rule', 'priority'],
        }},
    },
    'required': ['summary', 'rules'],
}

FILTER_PROPS = {key: {'type': 'boolean' if key in ('after_hours', 'is_new') else 'string'} for key in GROUPABLE}
COORD_TOOLS = [
    {'name': 'label_stats', 'description': 'Đếm hội thoại đã gán nhãn theo một nhãn (kèm % chốt, điểm nhân viên TB, thời gian trả lời đầu). '
     'scope=all_time để gộp cả các đợt trước.',
     'parameters': {'type': 'object', 'properties': {'group_by': {'type': 'string', 'enum': list(GROUPABLE)},
                                                     'filters': {'type': 'object', 'properties': FILTER_PROPS},
                                                     'scope': {'type': 'string', 'enum': ['this_analysis', 'all_time']}},
                    'required': ['group_by']}},
    {'name': 'staff_scorecard', 'description': 'Bảng điểm từng nhân viên: số hội thoại, % chốt/mất khách, điểm TB theo tiêu chí, ví dụ làm tốt/cần cải thiện.',
     'parameters': {'type': 'object', 'properties': {'scope': {'type': 'string', 'enum': ['this_analysis', 'all_time']}}}},
    {'name': 'search_conversations', 'description': 'Tìm hội thoại đã gán nhãn theo bộ lọc, trả id + tóm tắt + nhãn.',
     'parameters': {'type': 'object', 'properties': {'filters': {'type': 'object', 'properties': FILTER_PROPS},
                                                     'scope': {'type': 'string', 'enum': ['this_analysis', 'all_time']},
                                                     'limit': {'type': 'integer'}}}},
    {'name': 'read_conversation', 'description': 'Đọc lại nguyên đoạn chat của một hội thoại (SĐT đã che) + dữ kiện từ ảnh.',
     'parameters': {'type': 'object', 'properties': {'conversation_id': {'type': 'integer'}}, 'required': ['conversation_id']}},
    {'name': 'get_stats', 'description': 'Lấy một phần số liệu thống kê của toàn bộ tin trong kỳ.',
     'parameters': {'type': 'object', 'properties': {'section': {'type': 'string', 'enum': [
         'overview', 'good_first_turn', 'effects', 'info_questions', 'openers', 'customer_openers', 'vocabulary', 'hours', 'staff']}},
                    'required': ['section']}},
    {'name': 'list_rules', 'description': 'Xem các quy tắc chat hiện có.',
     'parameters': {'type': 'object', 'properties': {'state': {'type': 'string', 'enum': ['approved', 'proposed', 'all']}}}},
    {'name': 'finish', 'description': 'Trả kết quả cuối cùng (gọi MỘT lần khi đã đủ bằng chứng).',
     'parameters': {'type': 'object', 'properties': {
         'summary': REDUCE_SCHEMA['properties']['summary'],
         'rules': REDUCE_SCHEMA['properties']['rules'],
         'staff_reviews': {'type': 'array', 'description': 'Đánh giá từng nhân viên (dựa trên bảng điểm và hội thoại đã đọc).', 'items': {
             'type': 'object', 'properties': {
                 'staff_name': {'type': 'string'}, 'overall': {'type': 'integer', 'description': '1–5'},
                 'strengths': {'type': 'string'}, 'improvements': {'type': 'string'},
                 'coaching': {'type': 'string', 'description': 'Một lời khuyên cụ thể, làm được ngay.'}},
             'required': ['staff_name', 'overall']}},
         'bot_scope': {'type': 'string', 'description': 'Loại hội thoại chatbot nên tự lo / phải chuyển người ngay (dựa trên nhãn bot_fit, intent, customer_kind).'},
     }, 'required': ['summary', 'rules']}},
]

COORD_TASK = """Bạn là AGENT ĐIỀU PHỐI. Các lô hội thoại đã được AI đọc và GÁN NHÃN (sản phẩm, loại khách, mục đích, kết quả, lý do mất
khách, thông tin đã lấy, dữ kiện từ ảnh, điểm nhân viên). Nhiệm vụ: rút ra kết luận và bộ quy tắc nhắn tin cho chatbot chào khách,
đồng thời đánh giá từng nhân viên sale.

Cách làm: dùng tool để tra cứu và KIỂM CHỨNG trước khi kết luận (vd so % chốt theo sản phẩm, xem lý do mất khách, đọc lại 2–3 hội thoại
mất khách hoặc chốt nhanh để lấy câu mẫu thật). Tối đa khoảng 6 lần tra cứu, rồi gọi finish.
Trong finish: mỗi quy tắc có bằng chứng (số liệu hoặc id hội thoại). Đánh giá nhân viên công bằng, cụ thể, dựa trên dữ liệu; nhân viên
có dưới 5 hội thoại thì ghi rõ "ít dữ liệu". Không lặp lại quy tắc an toàn đã duyệt và không viết quy tắc trái với chúng."""


def _to_utc(day, end=False):
    """Ngày (giờ VN) → datetime naive UTC lúc 00:00 (hoặc 00:00 ngày hôm sau nếu end)."""
    moment = datetime.combine(day + timedelta(days=1) if end else day, datetime.min.time(), tzinfo=cs.TZ)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _cfg(field, fallback=None):
    """Giá trị mặc định lấy từ Thiết lập phân tích chat."""
    def default(self):
        value = self.env['dac.chat.config']._get()[field]
        return value if value not in (None,) else fallback
    return default


def _image_urls(raw):
    """Link ảnh (https) trong tin."""
    urls = []

    def walk(items):
        for item in items or []:
            if isinstance(item, dict):
                url = item.get('url') or item.get('origin_url')
                kind = str(item.get('type') or '')
                if url and str(url).startswith('https://') and kind in ('photo', 'image', 'sticker_photo', ''):
                    if kind or str(url).lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        urls.append(url)
                walk(item.get('attachments'))
    walk(raw.get('attachments') if isinstance(raw, dict) else None)
    return urls[:6]


def _num(value, suffix=''):
    if value is None:
        return '–'
    if isinstance(value, float):
        value = ('%.1f' % value).rstrip('0').rstrip('.')
    return '%s%s' % (value, suffix)


class ChatAnalysis(models.Model):
    _name = 'dac.chat.analysis'
    _description = 'Phân tích tin nhắn khách (Pancake)'
    _order = 'id desc'

    name = fields.Char(string='Tên đợt', required=True, default=lambda self: 'Phân tích chat %s' % fields.Date.context_today(self).strftime('%d/%m/%Y'))
    date_from = fields.Date(string='Từ ngày', required=True, default=lambda self: fields.Date.context_today(self) - timedelta(
        days=self.env['dac.chat.config']._get().days_back or 90))
    date_to = fields.Date(string='Đến ngày', required=True, default=fields.Date.context_today)
    page_ids = fields.Many2many('dac.pancake.page', 'dac_chat_analysis_page_rel', 'analysis_id', 'page_id', string='Page',
        help='Để trống = mọi page Pancake.')
    ai_model_id = fields.Many2one('dac.ai.model', string='Model AI đọc tin', ondelete='set null', default=_cfg('ai_model_id'),
        domain="[('connection_id.active', '=', True)]",
        help='Model dùng để đọc hội thoại và đề xuất quy tắc (danh sách lấy ở Cài đặt → Kết nối AI dùng chung). '
             'Để trống = dùng thứ tự kết nối chung.')
    ai_fallback = fields.Boolean(string='Model chỉ định lỗi thì dùng thứ tự chung', default=_cfg('ai_fallback'))
    ai_conversation_limit = fields.Integer(string='Số hội thoại cho AI đọc', default=_cfg('conversation_limit'),
        help='AI đọc kỹ các hội thoại mẫu (khoảng 60% hội thoại tốt nhất, 25% khách không phản hồi, 15% ngoài giờ). '
             'Thống kê số liệu luôn tính trên TOÀN BỘ tin trong kỳ. 0 = chỉ thống kê, không gọi AI.')
    ai_batch_size = fields.Integer(string='Số hội thoại mỗi lần gọi AI', default=_cfg('batch_size'))
    read_mode = fields.Selection(READ_MODES, string='Chọn hội thoại cho AI đọc', default=_cfg('read_mode'), required=True)
    only_unread = fields.Boolean(string='Chỉ đọc hội thoại chưa đọc', default=_cfg('only_unread'),
        help='Bỏ qua hội thoại AI đã đọc và gán nhãn ở đợt trước, trừ khi có tin mới sau lần đọc đó.')
    customer_scope = fields.Selection([('new', 'Chỉ khách mới (bắt đầu trong kỳ)'), ('all', 'Khách mới và khách cũ')],
        string='Loại khách', default=_cfg('customer_scope'), required=True)
    min_messages = fields.Integer(string='Bỏ hội thoại ít hơn (tin)', default=_cfg('min_messages'),
        help='Hội thoại quá ngắn ít giá trị để học. Thống kê số liệu vẫn tính đủ.')
    read_images = fields.Boolean(string='AI xem ảnh trong hội thoại', default=_cfg('read_images'),
        help='Gửi kèm ảnh khách/nhân viên gửi (ưu tiên ảnh của khách) để AI đọc thêm dữ kiện: mẫu, mặt bằng, kích thước, nội dung… '
             'Cần model đọc được ảnh (Claude, Gemini). Model không đọc được ảnh thì tự đọc chỉ chữ.')
    images_per_conversation = fields.Integer(string='Số ảnh tối đa mỗi hội thoại', default=_cfg('images_per_conversation'),
        help='Tối đa 10. Một lần gọi AI nhận tối đa 30 ảnh: hội thoại nhiều ảnh thì lô đó ít hội thoại hơn.')
    batches_json = fields.Text(readonly=True, copy=False)
    progress_pct = fields.Integer(string='Tiến độ (%)', compute='_compute_progress')
    progress_log = fields.Text(string='Nhật ký chạy', readonly=True, copy=False)
    images_sent = fields.Integer(string='Ảnh đã gửi cho AI', readonly=True, copy=False)
    tokens_in = fields.Integer(string='Token vào (AI)', readonly=True, copy=False,
        help='Tổng token đầu vào nhà cung cấp AI báo về (bước đọc hội thoại + Agent điều phối). Bước gọi qua lõi chung không có số liệu thì không tính.')
    tokens_out = fields.Integer(string='Token ra (AI)', readonly=True, copy=False)
    auto_weekly = fields.Boolean(string='Tự chạy hằng tuần', copy=False,
        help='Mỗi thứ 2 lúc 6:00, tạo một đợt mới giống đợt này cho 7 ngày vừa qua, chỉ đọc hội thoại chưa đọc. '
             'Bản system prompt mới vẫn chỉ là bản nháp, cần người kích hoạt.')
    candidate_count = fields.Integer(string='Hội thoại thỏa điều kiện', readonly=True, copy=False)
    coord_state_json = fields.Text(readonly=True, copy=False)
    coord_failures = fields.Integer(readonly=True, copy=False)
    coord_rounds = fields.Integer(string='Số vòng Agent điều phối', readonly=True, copy=False)
    coord_log = fields.Text(string='Agent điều phối đã tra cứu', readonly=True, copy=False)
    coord_bot_scope = fields.Text(string='Phạm vi chatbot (Agent đề xuất)', readonly=True, copy=False)
    insight_ids = fields.One2many('dac.chat.insight', 'analysis_id', string='Nhãn hội thoại', readonly=True)
    insight_count = fields.Integer(string='Hội thoại đã gán nhãn', compute='_compute_insight_count')
    state = fields.Selection(STATES, string='Trạng thái', default='draft', required=True, readonly=True, copy=False, index=True)
    step_message = fields.Char(string='Tiến độ', readonly=True, copy=False)
    error_message = fields.Text(string='Lỗi', readonly=True, copy=False)
    started_at = fields.Datetime(string='Bắt đầu', readonly=True, copy=False)
    finished_at = fields.Datetime(string='Xong lúc', readonly=True, copy=False)
    report_json = fields.Text(string='Số liệu (JSON)', readonly=True, copy=False)
    report_html = fields.Html(string='Báo cáo', compute='_compute_report_html', sanitize=False)
    conversation_count = fields.Integer(string='Hội thoại có tin trong kỳ', readonly=True, copy=False)
    new_conversation_count = fields.Integer(string='Hội thoại khách mới', readonly=True, copy=False)
    sample_json = fields.Text(readonly=True, copy=False)
    ai_notes_json = fields.Text(readonly=True, copy=False)
    ai_batches_done = fields.Integer(string='Lô AI đã đọc', readonly=True, copy=False)
    ai_batches_total = fields.Integer(string='Tổng số lô AI', readonly=True, copy=False)
    ai_summary = fields.Text(string='AI nhận xét', readonly=True, copy=False)
    ai_reduced = fields.Boolean(readonly=True, copy=False)
    rules_reviewed = fields.Boolean(readonly=True, copy=False)
    rules_review_summary = fields.Text(string='AI rà soát quy tắc', readonly=True, copy=False)
    prompt_id = fields.Many2one('dac.chat.prompt', string='System prompt tạo ra', readonly=True, copy=False, ondelete='set null')
    prompt_ids = fields.One2many('dac.chat.prompt', 'analysis_id', string='Các bộ system prompt', readonly=True)
    ai_used_models = fields.Char(string='Model đã dùng', readonly=True, copy=False)
    staff_line_ids = fields.One2many('dac.chat.analysis.staff', 'analysis_id', string='Theo nhân viên', readonly=True)
    rule_ids = fields.One2many('dac.chat.rule', 'analysis_id', string='Quy tắc đề xuất')
    rule_count = fields.Integer(string='Quy tắc', compute='_compute_rule_count')

    _sql_constraints = [('date_check', 'CHECK(date_from <= date_to)', 'Từ ngày phải trước hoặc bằng Đến ngày.'),
                        ('images_check', 'CHECK(images_per_conversation BETWEEN 0 AND 10)', 'Số ảnh mỗi hội thoại từ 0 đến 10.')]

    @api.depends('state', 'ai_batches_done', 'ai_batches_total', 'coord_rounds', 'ai_reduced', 'rules_reviewed')
    def _compute_progress(self):
        for analysis in self:
            if analysis.state == 'done':
                analysis.progress_pct = 100
            elif analysis.state != 'ai':
                analysis.progress_pct = 0
            elif analysis.ai_reduced:
                analysis.progress_pct = 97 if analysis.rules_reviewed else 94
            elif analysis.ai_batches_done < analysis.ai_batches_total:
                analysis.progress_pct = 5 + int(75 * analysis.ai_batches_done / max(analysis.ai_batches_total, 1))
            else:
                analysis.progress_pct = 80 + int(14 * analysis.coord_rounds / COORD_ROUNDS)

    def _progress(self, message):
        """Giá trị ghi: dòng tiến độ + thêm một dòng có giờ vào nhật ký chạy."""
        stamp = fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), fields.Datetime.now()).strftime('%H:%M:%S')
        log = ((self.progress_log or '') + '\n%s  %s' % (stamp, message)).strip()
        return {'step_message': message, 'progress_log': log[-6000:]}

    def _compute_insight_count(self):
        counts = dict(self.env['dac.chat.insight']._read_group([('analysis_id', 'in', self.ids)], ['analysis_id'], ['__count']))
        for analysis in self:
            analysis.insight_count = counts.get(analysis, 0)

    def _compute_rule_count(self):
        counts = dict(self.env['dac.chat.rule']._read_group([('analysis_id', 'in', self.ids)], ['analysis_id'], ['__count']))
        for analysis in self:
            analysis.rule_count = counts.get(analysis, 0)

    # ── Nút bấm ──────────────────────────────────────────────────────────────
    def action_run(self):
        for analysis in self:
            if analysis.state in ('queued', 'ai'):
                raise UserError('Đợt "%s" đang chạy.' % analysis.name)
            if analysis.ai_batch_size <= 0 or analysis.ai_conversation_limit < 0:
                raise UserError('Số hội thoại cho AI phải ≥ 0 và số hội thoại mỗi lần gọi AI phải > 0.')
        # Chạy lại: bỏ quy tắc đề xuất cũ chưa duyệt của đợt này (quy tắc đã duyệt/bỏ giữ nguyên)
        self.env['dac.chat.rule'].search([('analysis_id', 'in', self.ids), ('state', '=', 'proposed')]).unlink()
        self.staff_line_ids.unlink()
        self.write({'state': 'queued', 'step_message': 'Đang chờ cron chạy…', 'progress_log': False, 'batches_json': False,
                    'error_message': False, 'started_at': fields.Datetime.now(),
                    'finished_at': False, 'report_json': False, 'sample_json': False, 'ai_notes_json': False, 'ai_batches_done': 0,
                    'ai_batches_total': 0, 'ai_summary': False, 'ai_used_models': False, 'ai_reduced': False, 'prompt_id': False,
                    'rules_reviewed': False, 'rules_review_summary': False,
                    'images_sent': 0, 'tokens_in': 0, 'tokens_out': 0, 'candidate_count': 0, 'coord_state_json': False, 'coord_failures': 0, 'coord_rounds': 0,
                    'coord_log': False, 'coord_bot_scope': False})
        # sudo(): chỉ admin có quyền trên ir.cron; quản lý bấm nút chỉ hẹn cron của addon này chạy sớm
        self.env.ref('dac_chat_playbook.cron_process_analysis').sudo()._trigger()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Đã xếp hàng', 'type': 'info',
            'message': 'Hệ thống chạy nền: thống kê xong trong ~1 phút, AI đọc mỗi lô ~1 phút. Tải lại trang để xem tiến độ.',
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_open_rules(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_rule_action')
        action['domain'] = [('analysis_id', '=', self.id)]
        action['context'] = {'search_default_group_category': 1}
        return action

    def action_open_insights(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_insight_action')
        action['domain'] = [('analysis_id', '=', self.id)]
        return action

    def action_open_prompt(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_prompt_action')
        action['domain'] = [('analysis_id', '=', self.id)]
        return action

    def action_rebuild_prompt(self):
        """Sau khi sửa/duyệt quy tắc: cho Agent viết lại một bản system prompt mới từ đợt này (chạy nền)."""
        for analysis in self:
            if analysis.state != 'done' or not analysis.ai_reduced:
                raise UserError('Chỉ viết lại được cho đợt đã chạy xong phần AI.')
        for analysis in self:
            analysis.write(dict(analysis._progress('Viết lại system prompt: Agent rà soát quy tắc rồi viết prompt…'), state='ai', prompt_id=False,
                                finished_at=False, rules_reviewed=False))
        # sudo(): chỉ admin có quyền trên ir.cron; quản lý bấm nút chỉ hẹn cron của addon này chạy sớm
        self.env.ref('dac_chat_playbook.cron_process_analysis').sudo()._trigger()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_reset(self):
        self.write({'state': 'draft', 'step_message': False})

    # ── Cron ─────────────────────────────────────────────────────────────────
    @api.model
    def _cron_process(self):
        """Không raise: lỗi → đợt chuyển 'Lỗi' kèm thông báo."""
        started = time.monotonic()
        # sudo(): cron chạy nền; đọc tin Pancake và gọi kết nối AI dùng chung (key chỉ admin đọc được)
        for analysis in self.sudo().search([('state', 'in', ('queued', 'ai'))], order='id'):
            while analysis.state in ('queued', 'ai') and time.monotonic() - started < CRON_BUDGET:
                try:
                    analysis._process_step()
                    self.env.cr.commit()
                except Exception as error:  # noqa: BLE001 — cron không được raise
                    self.env.cr.rollback()
                    _logger.exception('Phân tích chat %s lỗi', analysis.id)
                    analysis.write(dict(analysis._progress('LỖI: %s' % str(error)[:300]), state='failed', error_message=str(error)[:2000],
                                        finished_at=fields.Datetime.now()))
                    self.env.cr.commit()
            if time.monotonic() - started >= CRON_BUDGET:
                break
        if self.sudo().search_count([('state', 'in', ('queued', 'ai'))]):
            self.env.ref('dac_chat_playbook.cron_process_analysis').sudo()._trigger()

    @api.model
    def _cron_weekly(self):
        """Tạo và chạy đợt tuần mới từ các đợt bật "Tự chạy hằng tuần" (không raise)."""
        today = fields.Date.context_today(self)
        config = self.env['dac.chat.config']._get()
        if config.auto_weekly:
            try:
                run = self.sudo().create(config._analysis_vals(today - timedelta(days=7), today - timedelta(days=1), only_unread=True,
                                                               name='Tự động tuần %s' % today.strftime('%d/%m/%Y')))
                run.action_run()
                config.last_weekly_run_id = run
                self.env.cr.commit()
            except Exception:  # noqa: BLE001 — cron không được raise
                self.env.cr.rollback()
                _logger.exception('Không tạo được đợt phân tích tuần theo thiết lập')
        # sudo(): cron chạy nền
        for template in self.sudo().search([('auto_weekly', '=', True)]):
            try:
                run = template.copy({'name': 'Tự động tuần %s' % today.strftime('%d/%m/%Y'), 'date_from': today - timedelta(days=7),
                                     'date_to': today - timedelta(days=1), 'only_unread': True})
                run.action_run()
                self.env.cr.commit()
            except Exception:  # noqa: BLE001 — cron không được raise
                self.env.cr.rollback()
                _logger.exception('Không tạo được đợt phân tích tuần từ %s', template.id)

    def _process_step(self):
        """Làm một bước: thống kê, hoặc một lô AI, hoặc bước tổng hợp."""
        self.ensure_one()
        if self.state == 'queued':
            self._compute_stats()
        elif self.ai_batches_done < self.ai_batches_total:
            self._ai_read_batch()
        elif not self.ai_reduced:
            self._coordinate()
        elif not self.rules_reviewed:
            self._review_rules()
        else:
            self._build_prompt()

    # ── Bước 1: thống kê ─────────────────────────────────────────────────────
    def _conversation_meta(self, start):
        """{id: dict(id, inserted_at, ordered, customer_name)} của các hội thoại (không nội bộ) có cập nhật trong kỳ."""
        params = [start]
        page_filter = ''
        if self.page_ids:
            page_filter = 'AND c.page_id = ANY(%s)'
            params.append(self.page_ids.ids)
        # Truy vấn SQL thô: ghi hết thay đổi đang chờ xuống DB trước
        self.env.flush_all()
        # Có đơn Odoo có giá: liên hệ của hội thoại có đơn (không hủy) tạo sau khi bắt đầu chat. amount_total không lưu DB → cộng dòng.
        self.env.cr.execute("""
            SELECT c.id, c.inserted_at_fm, c.customer_name,
                   EXISTS (SELECT 1 FROM sale_order so JOIN sale_order_line l ON l.order_id = so.id
                            WHERE c.partner_id IS NOT NULL AND so.partner_id = c.partner_id AND so.state != 'cancel'
                              AND so.create_date >= c.inserted_at_fm - interval '1 day'
                            GROUP BY so.id HAVING SUM(l.price_subtotal) > 0)
              FROM dac_pancake_conversation c
             WHERE NOT COALESCE(c.is_internal, FALSE) AND c.updated_at_fm >= %s """ + page_filter, params)
        return {row[0]: {'id': row[0], 'inserted_at': row[1], 'customer_name': row[2], 'ordered': row[3]} for row in self.env.cr.fetchall()}

    def _iter_messages(self, conversation_ids, start, end):
        """(id hội thoại, [cs.Msg]) theo từng hội thoại; đọc bằng SQL theo lô để chạy được trên ~100 nghìn tin."""
        if not conversation_ids:
            return
        self.env.flush_all()
        self.env.cr.execute("""
            SELECT conversation_id, inserted_at, type_content, content_html, raw_json FROM dac_pancake_message
             WHERE conversation_id = ANY(%s) AND inserted_at >= %s AND inserted_at < %s
             ORDER BY conversation_id, inserted_at, id""", (list(conversation_ids), start, end))
        current, buffer = None, []
        while True:
            rows = self.env.cr.fetchmany(5000)
            if not rows:
                break
            for conversation_id, at, type_content, html, raw_json in rows:
                if conversation_id != current:
                    if current is not None:
                        yield current, buffer
                    current, buffer = conversation_id, []
                msg = self._to_msg(at, type_content, html, raw_json)
                if msg:
                    buffer.append(msg)
        if current is not None:
            yield current, buffer

    @staticmethod
    def _to_msg(at, type_content, html, raw_json):
        # Dùng đúng bộ phân loại của luồng nhắc khách (tin hệ thống, cảm xúc, nhóm Zalo… bị bỏ)
        role = classify(SimpleNamespace(type_content=type_content, raw_json=raw_json, content_html=html))
        if not role:
            return None
        try:
            raw = json.loads(raw_json or '{}')
        except ValueError:
            raw = {}
        sender = raw.get('from') if isinstance(raw.get('from'), dict) else {}
        text = cs.plain(html)
        staff = ' '.join(str(sender.get('admin_name') or '').split()) if role != 'customer' else ''
        media = type_content not in MEDIA_TYPES_TEXT or (bool(raw.get('attachments')) and not text)
        phone = role == 'customer' and bool(raw.get('phone_info'))
        return cs.Msg(at, role, staff, text, media, phone, _image_urls(raw))

    def _compute_stats(self):
        self.ensure_one()
        start, end = _to_utc(self.date_from), _to_utc(self.date_to, end=True)
        meta = self._conversation_meta(start)
        analyzer = cs.Analyzer(start, end, work_days=working_time.WORK_DAYS, shifts=working_time.WORK_SHIFTS)
        for conversation_id, messages in self._iter_messages(meta.keys(), start, end):
            analyzer.add(meta[conversation_id], messages)
        report = analyzer.report()
        samples = self._filter_candidates(report.pop('samples'))
        picked = cs.pick_samples(samples, self.ai_conversation_limit, self.read_mode)
        packed = self._pack_batches(picked)
        batches = len(packed)
        use_ai = bool(batches) and bool(self._task_ai('read').model or self.env['dac.ai.connection']._ready())
        self.write({
            'report_json': json.dumps(report, ensure_ascii=False, default=str),
            'conversation_count': report['overview']['conversations'],
            'new_conversation_count': report['overview']['new_conversations'],
            'sample_json': json.dumps(picked), 'candidate_count': len(samples), 'batches_json': json.dumps(packed),
            'ai_batches_total': batches if use_ai else 0,
            'ai_notes_json': '[]',
            'state': 'ai' if use_ai else 'done',
            **self._progress(('Đã thống kê %s hội thoại (%s khách mới); %s hội thoại thỏa điều kiện, AI sẽ đọc %s hội thoại trong %s lô. '
                              'AI đang đọc lô 1/%s…') % (report['overview']['conversations'], report['overview']['new_conversations'],
                                                         len(samples), len(picked), batches, batches) if use_ai else
                             ('Xong (chỉ thống kê%s).' % (', chưa có kết nối AI dùng được' if batches else
                                                          (', không có hội thoại nào thỏa điều kiện đọc' if self.ai_conversation_limit else '')))),
            'finished_at': False if use_ai else fields.Datetime.now(),
        })
        self._create_staff_lines(report['staff'])
        self.env['dac.chat.rule']._propose_from_stats(self, report)

    def _pack_batches(self, picked):
        """Chia hội thoại thành lô: tối đa `ai_batch_size` hội thoại và (nếu xem ảnh) tối đa 30 ảnh mỗi lô."""
        per_conversation = min(self.images_per_conversation, MAX_IMAGES_PER_CONVERSATION) if self.read_images else 0
        batches, current, images = [], [], 0
        for sample in picked:
            need = min(sample.get('images') or 0, per_conversation)
            if current and (len(current) >= self.ai_batch_size or images + need > MAX_IMAGES_PER_BATCH):
                batches.append(current)
                current, images = [], 0
            current.append(sample)
            images += need
        if current:
            batches.append(current)
        return batches

    def _filter_candidates(self, samples):
        """Lọc hội thoại cho AI đọc: loại khách, số tin tối thiểu, chưa đọc (hoặc có tin mới sau lần đọc trước)."""
        samples = [s for s in samples if s.get('messages', 0) >= max(self.min_messages, 1)]
        if self.customer_scope == 'new':
            samples = [s for s in samples if s.get('new')]
        if self.only_unread and samples:
            read = {i.conversation_id.id: i.messages_until for i in self.env['dac.chat.insight'].search(
                [('conversation_id', 'in', [s['id'] for s in samples])])}
            samples = [s for s in samples if s['id'] not in read or not read[s['id']]
                       or fields.Datetime.to_datetime(s['last_at']) > read[s['id']]]
        return samples

    def _create_staff_lines(self, rows):
        staff_by_key = {s.key: s for s in self.env['dac.pancake.staff'].with_context(active_test=False).search([])}
        fields_map = ('conversations', 'turns', 'messages', 'messages_per_turn', 'turn_1', 'turn_2', 'turn_3', 'turn_4plus', 'chars_avg',
                      'chars_median', 'chars_p90', 'question_pct', 'polite_pct', 'media_pct', 'response_in_hours_median', 'new_first_replies',
                      'new_engaged_pct', 'new_quoted_pct', 'new_deal_pct', 'new_phone_pct')
        self.env['dac.chat.analysis.staff'].create([dict(
            {key: row.get(key) or 0 for key in fields_map}, analysis_id=self.id, name=row['name'],
            pancake_staff_id=(staff_by_key.get(name_key(row['name'])) or self.env['dac.pancake.staff']).id or False,
        ) for row in rows])

    # ── Bước 2–3: AI ─────────────────────────────────────────────────────────
    def _task_ai(self, code):
        """Model + mức thinking của một tác vụ (màn hình "Model & thinking theo tác vụ"); ghi đè của đợt này chỉ dùng khi tác vụ chưa chọn model."""
        return self.env['dac.chat.task']._resolve(code, self.ai_model_id, self.ai_fallback)

    def _ai_json(self, text, schema, name, description, timeout, task='coordinate'):
        """Gọi model được chỉ định (lỗi thì dùng thứ tự chung nếu cho phép) → (dict, nhãn model đã dùng)."""
        ai = self._task_ai(task)
        return ai_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, text, schema, name, description, timeout)

    def _note_model(self, label):
        used = [m for m in (self.ai_used_models or '').split(' · ') if m]
        if label not in used:
            used.append(label)
        return ' · '.join(used)

    def _batch_payload(self, samples):
        """(văn bản các đoạn chat, [(nhãn, base64)], {id hội thoại: số ảnh})."""
        start, end = _to_utc(self.date_from), _to_utc(self.date_to, end=True)
        by_id = {s['id']: s for s in samples}
        parts, images, per_conversation = [], [], {}
        limit_total = MAX_IMAGES_PER_BATCH if self.read_images else 0
        fetch_until = time.monotonic() + IMAGE_FETCH_BUDGET
        for conversation_id, messages in self._iter_messages(by_id.keys(), start, end):
            sample = by_id[conversation_id]
            customer_first = next((m for m in messages if m.role == 'customer'), messages[0] if messages else None)
            if not customer_first:
                continue
            labels = {}
            per_conversation_limit = min(self.images_per_conversation, MAX_IMAGES_PER_CONVERSATION)
            if limit_total and per_conversation_limit > 0:
                # Ưu tiên ảnh của khách (mẫu, mặt bằng, file), rồi tới ảnh nhân viên gửi
                ordered = [u for m in messages if m.role == 'customer' for u in m.images] + \
                          [u for m in messages if m.role == 'staff' for u in m.images]
                for url in dict.fromkeys(ordered):
                    if len(labels) >= per_conversation_limit or len(images) >= limit_total or time.monotonic() > fetch_until:
                        break
                    data = fetch_image(url)
                    if data:
                        label = 'Ảnh %s.%s' % (conversation_id, len(labels) + 1)
                        labels[url] = label
                        images.append(('[%s] (hội thoại %s)' % (label, conversation_id), data))
            per_conversation[conversation_id] = len(labels)
            hours = 'ngoài giờ làm việc' if sample.get('after_hours') else 'trong giờ'
            kind = 'khách mới' if sample.get('new') else 'khách cũ / đã nhắn trước kỳ'
            code = 'có dấu hiệu chốt (STK/cọc)' if sample.get('deal') else ('khách có nhắn lại' if sample.get('engaged') else 'khách không nhắn lại')
            parts.append('### Hội thoại %s — %s; code ghi nhận: %s\n%s' % (
                conversation_id, kind, code,
                cs.transcript(messages, '%s (%s)' % (cs.weekday_label(customer_first.at), hours), max_messages=40, max_chars=2600,
                              image_labels=labels)))
        return '\n\n'.join(parts), images, per_conversation

    def _ai_read_batch(self):
        index = self.ai_batches_done
        packed = json.loads(self.batches_json or '[]')
        if packed:
            batch = packed[index] if index < len(packed) else []
        else:  # đợt tạo trước bản 1.3 (chưa xếp lô theo ảnh)
            samples = json.loads(self.sample_json or '[]')
            batch = samples[index * self.ai_batch_size:(index + 1) * self.ai_batch_size]
        text, images, per_conversation = self._batch_payload(batch)
        notes = json.loads(self.ai_notes_json or '[]')
        sent = tokens_in = tokens_out = 0
        if text:
            prompt = ('Dưới đây là %s đoạn chat giữa khách (KH) và nhân viên (NV) của DAC. "+Xp" = số phút từ tin đầu; "[Ảnh x.y]" = ảnh '
                      'đính kèm bên dưới (đọc ảnh để lấy dữ kiện: sản phẩm, kích thước, chất liệu, nội dung, mặt bằng…); "[ảnh/file]" = ảnh/file '
                      'không gửi kèm; SĐT đã che.\nGÁN NHÃN cho TỪNG hội thoại (dùng đúng conversation_id) và chấm điểm nhân viên chính. '
                      'VIẾT CỰC NGẮN để tiết kiệm token: đúng giới hạn số từ; KHÔNG ghi trường không có thông tin hoặc đang ở giá trị mặc định; '
                      'không chép lại đoạn chat, không giải thích. Ở "patterns", tối đa 5 ý (mỗi ý ≤ 15 từ) về cách nhắn LẶP LẠI đáng học '
                      'hoặc nên tránh.\n\n%s') % (len(batch), text)
            schema = dict(LABEL_SCHEMA, properties=dict(LABEL_SCHEMA['properties'], patterns={
                'type': 'array', 'items': {'type': 'string'}, 'description': 'Tối đa 5 ý, mỗi ý ≤ 15 từ.'}))
            ai = self._task_ai('read')
            data, label, sent, usage = vision_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, prompt, images, schema,
                                                   'label_conversations', 'Gán nhãn từng hội thoại trong lô.', MAP_TIMEOUT)
            tokens_in, tokens_out = usage.get('in', 0), usage.get('out', 0)
            # Lần thử lại chỉ gửi `sent` ảnh đầu: đếm lại số ảnh mỗi hội thoại thực sự đã gửi
            seen = {}
            for image_label, _data in images[:sent]:
                cid = int(image_label.rsplit('hội thoại ', 1)[-1].rstrip(')'))
                seen[cid] = seen.get(cid, 0) + 1
            self.env['dac.chat.insight']._store(self, data.get('items') or [], {s['id']: s for s in batch}, label, seen)
            if data.get('patterns'):
                notes.append([str(p)[:300] for p in data['patterns'][:15]])
            self.ai_used_models = self._note_model(label)
        done = index + 1
        message = 'Xong lô %s/%s: %s hội thoại, %s ảnh%s. ' % (
            done, self.ai_batches_total, len(batch), sent,
            ', %s token vào / %s token ra' % ('{:,}'.format(tokens_in).replace(',', '.'), '{:,}'.format(tokens_out).replace(',', '.'))
            if tokens_out else '') + (
            'AI đang đọc lô %s/%s…' % (done + 1, self.ai_batches_total) if done < self.ai_batches_total else 'Agent điều phối bắt đầu tổng hợp…')
        self.write(dict(self._progress(message), ai_notes_json=json.dumps(notes, ensure_ascii=False), ai_batches_done=done,
                        images_sent=self.images_sent + sent, tokens_in=self.tokens_in + tokens_in, tokens_out=self.tokens_out + tokens_out))

    def _stats_digest(self):
        """Bản số liệu gọn cho AI tổng hợp (bỏ phần dài)."""
        report = json.loads(self.report_json or '{}')
        keep = ('overview', 'good_first_turn', 'effects', 'info_questions', 'markers_per_100')
        digest = {key: report.get(key) for key in keep}
        digest['staff'] = [{k: row.get(k) for k in ('messages_per_turn', 'turn_1', 'turn_2', 'chars_median', 'chars_p90', 'question_pct',
                                                    'polite_pct', 'response_in_hours_median', 'new_first_replies', 'new_engaged_pct',
                                                    'new_deal_pct')} for row in report.get('staff') or []]
        digest['openers'] = [{k: row.get(k) for k in ('text', 'n', 'engaged_pct', 'deal_pct')} for row in (report.get('openers') or [])[:12]]
        digest['top_trigrams'] = (report.get('top_trigrams') or [])[:15]
        return json.dumps(digest, ensure_ascii=False)

    def _guardrail_text(self):
        guardrails = self.env['dac.chat.rule'].search([('state', '=', 'approved'), ('category', 'in', ('forbidden', 'handoff'))])
        return '\n'.join('- %s: %s' % (r.name, r.content) for r in guardrails) or '(chưa có)'

    def _coord_brief(self):
        """Tin mở đầu cho Agent điều phối: số liệu + tổng hợp nhãn + ghi nhận của các lô."""
        Insight = self.env['dac.chat.insight']
        brief = {
            'so_lieu_toan_bo_tin': json.loads(self._stats_digest()),
            'nhan_theo_ket_qua': Insight._tool_label_stats(self, 'outcome'),
            'nhan_theo_san_pham': Insight._tool_label_stats(self, 'product'),
            'ly_do_mat_khach': Insight._tool_label_stats(self, 'lost_reason', {'outcome': 'mat_khach'}),
            'phu_hop_chatbot': Insight._tool_label_stats(self, 'bot_fit'),
        }
        return '%s\n\nDỮ LIỆU BAN ĐẦU (JSON):\n%s\n\nGHI NHẬN CÁC LÔ:\n%s\n\nQUY TẮC AN TOÀN ĐÃ DUYỆT:\n%s' % (
            COORD_TASK, json.dumps(brief, ensure_ascii=False)[:30000], json.dumps(json.loads(self.ai_notes_json or '[]'),
                                                                                    ensure_ascii=False)[:15000], self._guardrail_text())

    def _coord_execute(self, name, args):
        Insight = self.env['dac.chat.insight']
        scope = args.get('scope') or 'this_analysis'
        filters = args.get('filters') if isinstance(args.get('filters'), dict) else {}
        log = '%s(%s)' % (name, json.dumps(args, ensure_ascii=False)[:150])
        self.coord_log = ((self.coord_log or '') + '\n' + log).strip()[-4000:]
        if name == 'label_stats':
            return Insight._tool_label_stats(self, args.get('group_by') or 'outcome', filters, scope)
        if name == 'staff_scorecard':
            return Insight._tool_staff_scorecard(self, scope)
        if name == 'search_conversations':
            return Insight._tool_search(self, filters, scope, args.get('limit') or 8)
        if name == 'read_conversation':
            return self._read_conversation_tool(int(args.get('conversation_id') or 0))
        if name == 'get_stats':
            report = json.loads(self.report_json or '{}')
            section = args.get('section')
            if section == 'vocabulary':
                return {k: report.get(k) for k in ('markers_per_100', 'top_bigrams', 'top_trigrams')}
            if section == 'hours':
                return {k: report.get(k) for k in ('new_by_hour', 'new_by_weekday')}
            return report.get(section, 'Không có phần %s' % section)
        if name == 'list_rules':
            state = args.get('state') or 'approved'
            domain = [] if state == 'all' else [('state', '=', state)]
            return [{'id': r.id, 'category': r.category, 'state': r.state, 'title': r.name, 'rule': r.content}
                    for r in self.env['dac.chat.rule'].search(domain, limit=60)]
        return 'Tool không tồn tại: %s' % name

    def _read_conversation_tool(self, conversation_id):
        conversation = self.env['dac.pancake.conversation'].browse(conversation_id).exists()
        if not conversation or conversation.is_internal:
            return 'Không có hội thoại %s.' % conversation_id
        _start, end = _to_utc(self.date_from), _to_utc(self.date_to, end=True)
        messages = next((msgs for _cid, msgs in self._iter_messages([conversation.id], datetime(2000, 1, 1), end)), [])
        insight = self.env['dac.chat.insight'].search([('conversation_id', '=', conversation.id)], limit=1)
        return {'conversation_id': conversation.id,
                'transcript': cs.transcript(messages, cs.weekday_label(messages[0].at) if messages else '', max_messages=60, max_chars=5000),
                'summary': insight.summary or '', 'image_facts': insight.image_facts or ''}

    def _coordinate(self):
        """Một vòng của Agent điều phối (mỗi lượt cron một vòng). Lỗi AI 2 lần → tổng hợp một lần không tool."""
        ai = self._task_ai('coordinate')
        loop = AgentLoop(ai.env, AI_SYSTEM, COORD_TOOLS, self._coord_execute, 'finish', max_rounds=COORD_ROUNDS, timeout=REDUCE_TIMEOUT)
        try:
            state = json.loads(self.coord_state_json) if self.coord_state_json else loop.start(ai.model, ai.fallback, self._coord_brief())
            state = loop.step(state)
        except UserError as error:
            failures = self.coord_failures + 1
            _logger.warning('Agent điều phối lỗi (%s lần): %s', failures, error)
            if failures < 2:
                self.write(dict(self._progress('Agent điều phối gặp lỗi, thử lại… (%s)' % str(error)[:120]), coord_failures=failures))
                return
            data, label = self._ai_json(self._coord_brief() + '\n\nKhông có tool tra cứu: hãy tổng hợp ngay từ dữ liệu trên.',
                                        COORD_TOOLS[-1]['parameters'], 'finish', 'Trả kết quả cuối cùng.', REDUCE_TIMEOUT)
            return self._finish_coordination(data, label)
        usage = state.pop('last_usage', None) or {}
        self.write({'tokens_in': self.tokens_in + usage.get('in', 0), 'tokens_out': self.tokens_out + usage.get('out', 0)})
        if state.get('done'):
            return self._finish_coordination(state.get('result') or {}, state.get('model_label') or '')
        last = (self.coord_log or '').strip().splitlines()[-1:] or ['']
        self.write(dict(self._progress('Agent điều phối vòng %s/%s: %s' % (state['rounds'], COORD_ROUNDS, last[0][:160])),
                        coord_state_json=json.dumps(state, ensure_ascii=False), coord_rounds=state['rounds']))

    def _finish_coordination(self, data, label):
        self.env['dac.chat.rule']._propose_from_ai(self, data.get('rules') or [])
        self._apply_staff_reviews(data.get('staff_reviews') or [])
        self.write({'ai_summary': (data.get('summary') or '').strip(), 'coord_bot_scope': (data.get('bot_scope') or '').strip() or False,
                    'ai_used_models': self._note_model(label) if label else self.ai_used_models, 'ai_reduced': True,
                    'coord_state_json': False, **self._progress('Agent điều phối xong: %s quy tắc đề xuất, %s nhân viên được đánh giá. '
                                                                 'Agent đang viết system prompt cho chatbot…' % (
                                                                     len(data.get('rules') or []), len(data.get('staff_reviews') or [])))})

    def _apply_staff_reviews(self, reviews):
        """Gắn điểm AI (tính từ nhãn) + nhận xét của Agent vào dòng nhân viên; nhân viên chưa có dòng thì tạo."""
        Line = self.env['dac.chat.analysis.staff']
        lines = {cs.fold(line.name): line for line in self.staff_line_ids}
        card = {cs.fold(row['staff']): row for row in self.env['dac.chat.insight']._tool_staff_scorecard(self)}
        notes = {cs.fold(str(r.get('staff_name') or '')): r for r in reviews if isinstance(r, dict)}
        for key in set(card) | set(notes):
            if not key or key == '?':
                continue
            row, review = card.get(key, {}), notes.get(key, {})
            vals = {'ai_conversations': row.get('conversations', 0), 'ai_deal_pct': row.get('deal_pct', 0), 'ai_lost_pct': row.get('lost_pct', 0)}
            for score in SCORES:
                vals['ai_%s' % score] = row.get(SCORE_LABELS[score]) or 0
            try:
                overall = int(review.get('overall') or 0)
            except (TypeError, ValueError):
                overall = 0
            vals.update({'ai_overall': overall if 1 <= overall <= 5 else 0, 'ai_strengths': review.get('strengths') or False,
                         'ai_improvements': review.get('improvements') or False, 'ai_coaching': review.get('coaching') or False})
            line = lines.get(key)
            if line:
                line.write(vals)
            else:
                Line.create(dict(vals, analysis_id=self.id, name=row.get('staff') or review.get('staff_name')))

    def _review_rules(self):
        """Agent rà soát: gộp quy tắc trùng, bỏ quy tắc thừa trước khi viết system prompt. Lỗi AI thì bỏ qua bước này (không chặn đợt)."""
        try:
            result = self.env['dac.chat.rule']._ai_review(self)
            usage = result.get('usage') or {}
            summary = result['message']
        except UserError as error:
            usage, summary = {}, 'Bỏ qua bước rà soát (AI lỗi: %s).' % str(error)[:200]
        self.write(dict(self._progress('Agent rà soát quy tắc: %s Agent đang viết system prompt…' % summary[:300]),
                        rules_reviewed=True, rules_review_summary=summary,
                        tokens_in=self.tokens_in + usage.get('in', 0), tokens_out=self.tokens_out + usage.get('out', 0)))

    def _build_prompt(self):
        """Bước cuối: Agent tổng hợp số liệu + nhận xét + quy tắc thành một bản system prompt (bản nháp, chờ kích hoạt)."""
        prompts, label = self.env['dac.chat.prompt']._generate(self)
        full = prompts.filtered(lambda p: p.mode == 'consult')[:1] or prompts[:1]
        self.write(dict(self._progress('Xong. Đã có 3 bộ system prompt nháp (%s): Chào hỏi, Hỏi thông tin, Tư vấn.'
                                       % full.name), prompt_id=full.id, ai_used_models=self._note_model(label), state='done',
                        finished_at=fields.Datetime.now()))

    # ── Báo cáo HTML ─────────────────────────────────────────────────────────
    @api.depends('report_json', 'ai_summary', 'coord_bot_scope', 'insight_ids', 'staff_line_ids')
    def _compute_report_html(self):
        for analysis in self:
            analysis.report_html = analysis._render_report() if analysis.report_json else Markup(
                '<p class="text-muted">Chưa có số liệu. Bấm "Chạy phân tích".</p>')

    @staticmethod
    def _html_table(headers, rows, numeric_from=1):
        head = Markup('').join(Markup('<th class="%s">%s</th>') % ('text-end' if i >= numeric_from else '', h) for i, h in enumerate(headers))
        body = Markup('').join(
            Markup('<tr>%s</tr>') % Markup('').join(
                Markup('<td class="%s">%s</td>') % ('text-end' if i >= numeric_from else '', cell) for i, cell in enumerate(row))
            for row in rows)
        return Markup('<div class="table-responsive"><table class="table table-sm table-hover o_dac_chat_table"><thead><tr>%s</tr></thead>'
                      '<tbody>%s</tbody></table></div>') % (head, body)

    def _render_ai_sections(self):
        """Phân loại hội thoại (nhãn AI) + đánh giá nhân viên."""
        if not self.insight_ids:
            return Markup('')
        Insight = self.env['dac.chat.insight']
        h = Markup
        parts = [h('<h3>Phân loại hội thoại (AI đã đọc %s hội thoại, xem %s ảnh)</h3>') % (len(self.insight_ids), self.images_sent)]
        for group_by, title, filters in (('outcome', 'Kết quả', None), ('product', 'Sản phẩm', None), ('intent', 'Mục đích', None),
                                         ('lost_reason', 'Lý do mất khách (chỉ hội thoại mất khách)', {'outcome': 'mat_khach'}),
                                         ('customer_kind', 'Loại khách', None), ('bot_fit', 'Phù hợp chatbot', None)):
            rows = Insight._tool_label_stats(self, group_by, filters)
            if rows:
                parts.append(h('<h5 class="mt-3">%s</h5>') % title)
                parts.append(self._html_table(['Nhóm', 'Hội thoại', 'Tỉ lệ', 'Chốt', 'Điểm NV TB', 'Trả lời đầu TB (phút)'],
                                              [[r['value'], r['conversations'], _num(r['share_pct'], '%'), _num(r['deal_pct'], '%'),
                                                _num(r['staff_score_avg']), _num(r['first_response_avg_min'])] for r in rows]))
        lines = self.staff_line_ids.filtered('ai_conversations')
        if lines:
            parts.append(h('<h3>Đánh giá nhân viên sale</h3><p class="text-muted">Điểm 1–5 do AI chấm trên từng hội thoại đã đọc; '
                           'số liệu thống kê ở bảng "Theo nhân viên" phía dưới. Dùng để kèm cặp, không dùng làm căn cứ duy nhất.</p>'))
            parts.append(self._html_table(
                ['Nhân viên', 'Hội thoại AI chấm', 'Chốt', 'Mất khách'] + [SCORE_LABELS[k] for k in SCORES] + ['Tổng', 'Điểm mạnh', 'Cần cải thiện', 'Lời khuyên'],
                [[l.name, l.ai_conversations, _num(l.ai_deal_pct, '%'), _num(l.ai_lost_pct, '%')] +
                 [_num(l['ai_%s' % k] or None) for k in SCORES] +
                 [_num(l.ai_overall or None), l.ai_strengths or '', l.ai_improvements or '', l.ai_coaching or ''] for l in lines]))
        return Markup('').join(parts)

    def _render_report(self):
        r = json.loads(self.report_json)
        o = r['overview']
        h = Markup
        parts = [h('<h3>Tổng quan</h3>'), self._html_table(['Chỉ số', 'Giá trị'], [
            ['Hội thoại có tin trong kỳ', _num(o['conversations'])],
            ['Tin của khách / nhân viên / bot', '%s / %s / %s' % (o['messages_customer'], o['messages_staff'], o['messages_bot'])],
            ['Hội thoại khách mới', _num(o['new_conversations'])],
            ['Khách mới được trả lời', _num(o['new_answered_pct'], '%')],
            ['Khách mới nhắn ngoài giờ làm việc', _num(o['new_after_hours_pct'], '%')],
            ['Khách nhắn lại sau lượt trả lời đầu', _num(o['new_engaged_pct'], '%')],
            ['Đã báo giá', _num(o['new_quoted_pct'], '%')],
            ['Chốt đơn (gửi STK / cọc / chuyển khoản / có đơn Odoo)', _num(o['new_deal_pct'], '%')],
            ['Khách để lại SĐT trong chat', _num(o['new_phone_pct'], '%')],
            ['Có đơn Odoo có giá', _num(o['new_ordered_pct'], '%')],
            ['Trả lời tin đầu (trung vị, trong giờ)', _num(o['first_response_in_hours_median'], ' phút')],
            ['Trả lời tin đầu (trung vị, khách nhắn ngoài giờ)', _num(o['first_response_after_hours_median'], ' phút')],
        ])]
        g = r.get('good_first_turn') or {}
        parts.append(h('<p class="text-muted">Ở %s hội thoại tốt (chốt đơn hoặc có SĐT), lượt trả lời đầu: trung vị <b>%s tin</b>, '
                       'mỗi tin <b>%s ký tự</b> (75%% tin ≤ %s ký tự), trả lời sau <b>%s phút</b>.</p>') % (
            _num(g.get('conversations')), _num(g.get('messages_per_turn_median')), _num(g.get('chars_median')), _num(g.get('chars_p75')),
            _num(g.get('response_median'))))
        if self.ai_summary:
            parts.append(h('<h3>Agent điều phối kết luận</h3><div class="alert alert-info" style="white-space: pre-line">%s</div>') % self.ai_summary)
        if self.coord_bot_scope:
            parts.append(h('<h5>Phạm vi chatbot</h5><div class="alert alert-light" style="white-space: pre-line">%s</div>') % self.coord_bot_scope)
        parts.append(self._render_ai_sections())

        parts.append(h('<h3>Theo nhân viên</h3>'))
        parts.append(self._html_table(
            ['Nhân viên', 'Hội thoại', 'Lượt', 'Tin/lượt', '1 tin', '2 tin', '3 tin', '4+', 'Ký tự (TB)', 'Ký tự (giữa)', '90% ≤',
             'Câu hỏi', '"dạ/ạ"', 'Ảnh/file', 'Trả lời (phút)', 'Khách mới', 'Nhắn lại', 'Báo giá', 'Chốt'],
            [[s['name'], _num(s['conversations']), _num(s['turns']), _num(s['messages_per_turn']), _num(s['turn_1'], '%'),
              _num(s['turn_2'], '%'), _num(s['turn_3'], '%'), _num(s['turn_4plus'], '%'), _num(s['chars_avg']), _num(s['chars_median']),
              _num(s['chars_p90']), _num(s['question_pct'], '%'), _num(s['polite_pct'], '%'), _num(s['media_pct'], '%'),
              _num(s['response_in_hours_median']), _num(s['new_first_replies']), _num(s['new_engaged_pct'], '%'),
              _num(s.get('new_quoted_pct'), '%'), _num(s.get('new_deal_pct'), '%')] for s in r['staff']]))

        parts.append(h('<h3>Kiểu trả lời đầu tiên → kết quả (hội thoại khách mới)</h3>'
                       '<p class="text-muted">Tương quan, không phải nhân quả. Nhóm dưới ~20 hội thoại chỉ để tham khảo.</p>'))
        for effect in r['effects']:
            parts.append(h('<h5 class="mt-3">%s</h5>') % effect['feature'])
            parts.append(self._html_table(['Nhóm', 'Hội thoại', 'Khách nhắn lại', 'Báo giá', 'Chốt đơn', 'Có SĐT'],
                                     [[row['value'], _num(row['n']), _num(row['engaged_pct'], '%'), _num(row.get('quoted_pct'), '%'),
                                       _num(row.get('deal_pct'), '%'), _num(row['phone_pct'], '%')] for row in effect['rows']]))

        parts.append(h('<h3>Thông tin nhân viên hay xin (khách mới)</h3>'))
        parts.append(self._html_table(['Thông tin', 'Có hỏi', 'Hỏi ở lượt thứ (giữa)', 'Có SĐT khi đã hỏi'],
                                 [[i['label'], _num(i['asked_pct'], '%'), _num(i['median_turn']), _num(i['phone_pct_when_asked'], '%')]
                                  for i in r['info_questions']]))
        parts.append(h('<h3>Câu trả lời đầu tiên hay dùng</h3>'))
        parts.append(self._html_table(['Câu (SĐT đã che)', 'Số lần', 'Khách nhắn lại', 'Chốt'],
                                 [[o_['text'], _num(o_['n']), _num(o_['engaged_pct'], '%'), _num(o_.get('deal_pct'), '%')] for o_ in r['openers']]))
        if r.get('customer_openers'):
            parts.append(h('<h3>Khách mới hay mở đầu bằng</h3>'))
            parts.append(self._html_table(['Tin đầu của khách', 'Số lần'], [[c['text'], _num(c['n'])] for c in r['customer_openers']]))
        parts.append(h('<h3>Từ ngữ nhân viên dùng</h3><p>Số tin có từ này trên 100 tin: %s</p>') % ', '.join(
            '%s %s' % (k, _num(v)) for k, v in (r.get('markers_per_100') or {}).items()))
        parts.append(h('<p><b>Cụm 3 từ hay gặp:</b> %s</p><p><b>Cụm 2 từ hay gặp:</b> %s</p>') % (
            ', '.join('%s (%s)' % (g_, n) for g_, n in r.get('top_trigrams') or []),
            ', '.join('%s (%s)' % (g_, n) for g_, n in (r.get('top_bigrams') or [])[:30])))
        parts.append(h('<h3>Khách mới nhắn lúc nào</h3>'))
        parts.append(self._html_table(list((r.get('new_by_weekday') or {}).keys()), [list((r.get('new_by_weekday') or {}).values())], numeric_from=0))
        by_hour = r.get('new_by_hour') or {}
        parts.append(self._html_table(['%sh' % k for k in by_hour], [list(by_hour.values())], numeric_from=0))
        return Markup('').join(parts)


class ChatAnalysisStaff(models.Model):
    _name = 'dac.chat.analysis.staff'
    _description = 'Thống kê cách nhắn theo nhân viên'
    _order = 'turns desc, id'

    analysis_id = fields.Many2one('dac.chat.analysis', required=True, ondelete='cascade', index=True)
    name = fields.Char(string='Nhân viên (tên Pancake)', required=True)
    pancake_staff_id = fields.Many2one('dac.pancake.staff', string='Nhân viên Pancake', ondelete='set null')
    conversations = fields.Integer(string='Hội thoại')
    turns = fields.Integer(string='Số lượt')
    messages = fields.Integer(string='Số tin')
    messages_per_turn = fields.Float(string='Tin / lượt', digits=(6, 2))
    turn_1 = fields.Float(string='Lượt 1 tin (%)', digits=(6, 1))
    turn_2 = fields.Float(string='Lượt 2 tin (%)', digits=(6, 1))
    turn_3 = fields.Float(string='Lượt 3 tin (%)', digits=(6, 1))
    turn_4plus = fields.Float(string='Lượt 4+ tin (%)', digits=(6, 1))
    chars_avg = fields.Float(string='Ký tự / tin (TB)', digits=(6, 1))
    chars_median = fields.Float(string='Ký tự / tin (giữa)', digits=(6, 1))
    chars_p90 = fields.Integer(string='90% tin ≤ (ký tự)')
    question_pct = fields.Float(string='Tin là câu hỏi (%)', digits=(6, 1))
    polite_pct = fields.Float(string='Lượt có "dạ/ạ" (%)', digits=(6, 1))
    media_pct = fields.Float(string='Tin ảnh/file (%)', digits=(6, 1))
    response_in_hours_median = fields.Float(string='Trả lời sau (phút, trong giờ)', digits=(8, 1))
    new_first_replies = fields.Integer(string='Khách mới đã trả lời đầu')
    new_engaged_pct = fields.Float(string='Khách nhắn lại (%)', digits=(6, 1))
    new_quoted_pct = fields.Float(string='Đã báo giá (%)', digits=(6, 1))
    new_deal_pct = fields.Float(string='Chốt đơn (%)', digits=(6, 1))
    new_phone_pct = fields.Float(string='Có SĐT (%)', digits=(6, 1))
    # Đánh giá AI (tính từ nhãn từng hội thoại + nhận xét của Agent điều phối)
    ai_conversations = fields.Integer(string='Hội thoại AI chấm')
    ai_deal_pct = fields.Float(string='AI: chốt (%)', digits=(6, 1))
    ai_lost_pct = fields.Float(string='AI: mất khách (%)', digits=(6, 1))
    ai_greeting = fields.Float(string='Chào hỏi', digits=(3, 1))
    ai_discovery = fields.Float(string='Khai thác nhu cầu', digits=(3, 1))
    ai_consulting = fields.Float(string='Tư vấn', digits=(3, 1))
    ai_closing = fields.Float(string='Chốt / bước tiếp', digits=(3, 1))
    ai_clarity = fields.Float(string='Rõ ràng', digits=(3, 1))
    ai_attitude = fields.Float(string='Thái độ', digits=(3, 1))
    ai_overall = fields.Integer(string='Điểm tổng (1–5)')
    ai_strengths = fields.Text(string='Điểm mạnh')
    ai_improvements = fields.Text(string='Cần cải thiện')
    ai_coaching = fields.Text(string='Lời khuyên')
