"""System prompt cho chatbot trả lời khách: Agent tổng hợp viết ra từ kết quả phân tích, quản lý thử rồi kích hoạt.

Cấu trúc khi chatbot chạy (`_runtime_prompt`):
1. Phần Agent viết, quản lý sửa được (`system_prompt`), có biến {{now}}, {{work_status}}, {{next_working_time}}, {{customer_name}}.
2. Quy tắc CẤM và CHUYỂN NHÂN VIÊN đã duyệt — code chèn nguyên văn lúc chạy, không qua AI, không sửa được ở đây.
   Kèm quy định theo KÊNH khách nhắn (Zalo: không xin SĐT; Facebook: xin được, không bắt buộc).
3. Khuôn trả lời cố định (tool `reply_customer`).
Sau khi AI trả lời, code còn chặn lần cuối các tin có giá / số tài khoản / cam kết (`_guard`).
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.addons.dac_push.models import working_time

from . import chat_stats as cs
from .ai_agent import ai_call, vision_call
from .chat_analysis import AI_SYSTEM
from .chat_rule import CATEGORIES

GEN_TIMEOUT = (10, 100)
REPLY_TIMEOUT = (10, 45)
IMPROVE_TIMEOUT = (10, 300)  # thinking tối đa chạy lâu
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,30}$')
# Tin chatbot không bao giờ được gửi: số tiền, số tài khoản, cam kết chốt/cọc
BLOCK_RE = re.compile(
    r'\d{1,3}(?:[.,]\d{3})+|\d+\s?(?:k|tr|triệu|nghìn|ngàn|đ|vnđ|vnd)\b|số tài khoản|\bstk\b|chuyển khoản|đặt cọc|cọc trước|'
    r'giảm giá|khuyến mãi|cam kết|chắc chắn (?:xong|giao|kịp)', re.IGNORECASE)
# Tin lộ là máy/AI hoặc tự giới thiệu "em là trợ lý…": không gửi (khách không được biết đang nhắn với chatbot)
IDENTITY_RE = re.compile(r'chat\s?bot|\bbot\b|trợ lý|trí tuệ nhân tạo|trả lời tự động|tin nhắn tự động|hệ thống tự động|'
                         r'\bem là (?:nhân viên|người|bạn)\b|\bmình là (?:trợ lý|nhân viên|bot)', re.IGNORECASE)
IDENTITY_AI_RE = re.compile(r'\bAI\b')
IDENTITY_RULE = ('Nhắn như một nhân viên thật của DAC: KHÔNG tự giới thiệu ("em là trợ lý/nhân viên…"), KHÔNG nói hay ám chỉ mình là AI, '
                 'chatbot, bot, trợ lý ảo hay hệ thống tự động; chào hỏi thật tự nhiên như người đang trực page.')
PLACEHOLDERS = ('{{now}}', '{{work_status}}', '{{next_working_time}}', '{{customer_name}}', '{{channel}}')
CHANNELS = [('zalo', 'Zalo'), ('facebook', 'Facebook Messenger')]
CHANNEL_RULES = {
    'zalo': 'Khách nhắn qua Zalo: shop đã liên lạc được với khách ngay trên Zalo này, nên KHÔNG xin số điện thoại hay Zalo (kể cả khi '
            'phần trên có ghi). Khách tự gửi số hoặc muốn được gọi lại thì ghi nhận.',
    'facebook': 'Khách nhắn qua Facebook Messenger: khi đã có sản phẩm và kích thước, có thể xin số Zalo/điện thoại để bạn phụ trách gửi '
                'báo giá, file thuận tiện; không bắt buộc, khách không muốn thì thôi, vẫn hỗ trợ tiếp trong Messenger.',
}
PHONE_KEY_RE = re.compile(r'^(phone|sdt|so_dien_thoai|dien_thoai|zalo|contact|lien_he)')


def channel_key(value):
    """'Zalo' / 'Facebook' (trường platform của hội thoại Pancake) hoặc mã → 'zalo' | 'facebook'."""
    text = cs.fold(str(value or ''))
    return 'facebook' if ('facebook' in text or 'messenger' in text or text.startswith('fb')) else 'zalo'

MODES = [('greeting', 'Chào hỏi'), ('info', 'Hỏi thông tin'), ('consult', 'Tư vấn')]
MODE_FLOW_KEY = {'greeting': 'flow_greeting', 'info': 'flow_info', 'consult': 'flow_consult'}
MODE_FLOW_FALLBACK = {'info': ('flow_basic', 'flow'), 'consult': ('flow_full', 'flow')}  # tên cũ (trước bản 1.6)
MODE_AFTER_HOURS_KEY = {'greeting': 'after_hours_greeting', 'info': 'after_hours', 'consult': 'after_hours'}
MODE_HANDOFF = {
    'greeting': 'đã chào và biết khách cần làm gì (bộ này KHÔNG xin thêm thông tin chi tiết)',
    'info': 'đã đủ các thông tin bắt buộc ở trên',
    'consult': 'đã đủ thông tin bắt buộc và đã trả lời/tư vấn sơ bộ xong, hoặc khách cần báo giá, chốt đơn',
}

AGENT_TASK = """Bạn là AGENT TỔNG HỢP. Từ số liệu, nhận xét và quy tắc dưới đây, hãy viết SYSTEM PROMPT hoàn chỉnh cho chatbot trả lời
khách mới nhắn Zalo/Facebook của DAC. Chatbot đọc prompt này mỗi lần trả lời, nên viết rõ ràng, cụ thể, làm theo được.
ĐỊNH HƯỚNG CỦA QUẢN LÝ (bên dưới) ưu tiên cao nhất.

Yêu cầu:
- persona: xưng hô, giọng nhắn như nhân viên thật đang trực page; độ dài và số tin mỗi lượt lấy từ số liệu nhân viên giỏi. Khách KHÔNG được
  biết đang nhắn với chatbot: không viết câu tự giới thiệu ("em là trợ lý…"), không nhắc AI/bot trong mọi câu mẫu.
- Viết 3 BỘ quy trình cho 3 chế độ chatbot (cùng giọng, cùng persona). Mỗi bộ là một prompt RIÊNG, chatbot chỉ thấy bộ của nó:
  · flow_greeting — CHÀO HỎI: chào ngay, hỏi một câu khách cần làm gì, báo bạn phụ trách sẽ liên hệ (trong giờ: sớm;
    ngoài giờ: mốc {{next_working_time}}); KHÔNG hỏi kích thước/số lượng/file, không tư vấn, không xin thông tin chi tiết.
  · flow_info — HỎI THÔNG TIN: chào → hỏi nhu cầu → xin lần lượt các thông tin cần để báo giá (mỗi lượt 1–2 ý) → tóm tắt lại →
    báo bạn phụ trách sẽ báo giá. Trả lời ngắn câu hỏi cơ bản (địa chỉ, giờ làm, có làm sản phẩm X không) từ KIẾN THỨC DOANH NGHIỆP,
    không tư vấn chất liệu/kiểu làm.
  · flow_consult — TƯ VẤN: như flow_info, cộng trả lời câu hỏi về sản phẩm, chất liệu, quy trình, bảo hành và tư vấn sơ bộ (gợi ý lựa
    chọn phổ biến phù hợp nhu cầu) dựa trên KIẾN THỨC DOANH NGHIỆP; không báo giá, không cam kết; xong thì chuyển bạn phụ trách.
  Khách đã nói thông tin nào thì không hỏi lại. Đủ thông tin của bộ đó thì cảm ơn và báo chuyển nhân viên.
- after_hours_greeting: NGOÀI giờ cho bộ Chào hỏi: chào, hỏi khách cần làm gì, hẹn đúng mốc {{next_working_time}}; không xin chi tiết.
- after_hours: NGOÀI giờ cho bộ Hỏi thông tin và Tư vấn: chào, xin thông tin cơ bản, hẹn đúng mốc {{next_working_time}}.
  Mốc do code điền sẵn, tuyệt đối không tự đoán ngày giờ khác.
- consult_guide: hướng dẫn tư vấn sơ bộ cho các nhóm sản phẩm DAC (chỉ bộ Tư vấn dùng), DỰA TRÊN KIẾN THỨC DOANH NGHIỆP bên dưới
  (đúng danh mục, chất liệu công ty có) — cách chọn phổ biến và câu hỏi cần làm rõ, không giá, không thông số chắc chắn.
  Không chép lại kiến thức (code tự chèn).
- style_rules: CHỈ cách nhắn dùng chung cho cả 3 bộ (độ dài, số tin, xưng hô, giọng, từ nên tránh). KHÔNG đưa cách xử lý hỏi giá,
  gửi file, xin thông tin vào đây (để trong flow của từng bộ).
- info_fields: thông tin cần xin, theo thứ tự nên hỏi; key là chữ thường không dấu (vd product, size, quantity, material, design_file,
  deadline, area, phone); required = true chỉ khi thiếu thì nhân viên không báo giá được (file thiết kế không bắt buộc: khách
  "chưa có file" là đã trả lời). Số điện thoại/Zalo luôn required = false: khách nhắn Zalo thì không xin (code tự xử lý theo kênh).
- examples: 6–9 đoạn mẫu ngắn; mỗi đoạn ghi modes = các bộ dùng đoạn đó (greeting/info/consult), mỗi bộ có ít nhất 2 đoạn ĐÚNG phạm vi
  của bộ (bộ Chào hỏi không có đoạn hỏi kích thước, tóm tắt hay báo giá). Có: trong giờ, ngoài giờ/Chủ nhật, khách hỏi giá, khách muốn
  chốt/cọc → chuyển nhân viên. Câu của bot đúng giọng nhân viên DAC; trong mẫu ngoài giờ viết đúng chữ {{next_working_time}}
  (code điền mốc thật), không viết ngày giờ cụ thể.
- Biến có sẵn: {{now}}, {{work_status}}, {{next_working_time}}, {{customer_name}}, {{channel}} (kênh khách nhắn: Zalo hoặc Facebook).
- used_rule_ids: id các quy tắc bạn đã đưa vào prompt (chỉ chọn quy tắc hợp lý; bỏ quy tắc trái phạm vi chatbot).
- Không nhắc lại quy tắc cấm/chuyển nhân viên: code sẽ tự chèn nguyên văn."""

OPTIMIZE_PROMPT_TASK = """Bạn là AGENT TỐI ƯU SYSTEM PROMPT cho chatbot trả lời khách của DAC. Admin đã thử chatbot và để lại GÓP Ý (kèm câu
khách nhắn và câu bot đã trả lời). Hãy viết lại SYSTEM PROMPT HIỆN TẠI để chatbot không mắc lại các lỗi đó:
- Sửa đúng chỗ liên quan (vai trò, quy trình, cách nhắn, hội thoại mẫu…); góp ý cụ thể thì rút thành nguyên tắc chung áp dụng được cho khách khác.
- Giữ nguyên cấu trúc tiêu đề (# …), các biến {{now}}, {{work_status}}, {{next_working_time}}, {{customer_name}}, {{channel}} và phần
  "Thông tin cần xin". Không thêm giá, không thêm cam kết, không làm prompt dài hơn đáng kể; bỏ câu mâu thuẫn với góp ý.
- Câu mẫu phải đúng giọng nhân viên thật; khách không được biết đang nhắn với chatbot.
- changes: liệt kê ngắn từng chỗ đã sửa và theo góp ý nào.
Không viết quy tắc cấm/chuyển nhân viên, kiến thức doanh nghiệp: code tự chèn."""
OPTIMIZE_PROMPT_SCHEMA = {'type': 'object', 'properties': {
    'system_prompt': {'type': 'string', 'description': 'Toàn bộ system prompt đã viết lại.'},
    'changes': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['system_prompt', 'changes']}

GEN_SCHEMA = {
    'type': 'object',
    'properties': {
        'persona': {'type': 'string'},
        'flow_greeting': {'type': 'string', 'description': 'Quy trình bộ CHỈ CHÀO HỎI.'},
        'flow_info': {'type': 'string', 'description': 'Quy trình bộ HỎI THÔNG TIN.'},
        'flow_consult': {'type': 'string', 'description': 'Quy trình bộ TƯ VẤN.'},
        'after_hours_greeting': {'type': 'string', 'description': 'Ngoài giờ, bộ Chào hỏi.'},
        'after_hours': {'type': 'string', 'description': 'Ngoài giờ, bộ Hỏi thông tin và Tư vấn.'},
        'consult_guide': {'type': 'string'},
        'style_rules': {'type': 'array', 'items': {'type': 'string'}},
        'info_fields': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'key': {'type': 'string'}, 'label': {'type': 'string'}, 'ask_example': {'type': 'string'}, 'required': {'type': 'boolean'},
},
            'required': ['key', 'label']}},
        'examples': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'situation': {'type': 'string'}, 'customer': {'type': 'string'}, 'bot': {'type': 'array', 'items': {'type': 'string'}},
            'modes': {'type': 'array', 'items': {'type': 'string', 'enum': ['greeting', 'info', 'consult']}}},
            'required': ['situation', 'customer', 'bot', 'modes']}},
        'used_rule_ids': {'type': 'array', 'items': {'type': 'integer'}},
    },
    'required': ['persona', 'flow_greeting', 'flow_info', 'flow_consult', 'after_hours_greeting', 'after_hours', 'info_fields', 'examples'],
}


def _clean_key(key, used):
    key = cs.fold(str(key or '')).replace(' ', '_')
    key = re.sub(r'[^a-z0-9_]', '', key)[:30] or 'info'
    if not key[0].isalpha():
        key = 'f_' + key
    base, n = key, 2
    while key in used:
        key, n = '%s_%s' % (base, n), n + 1
    used.add(key)
    return key


class ChatPrompt(models.Model):
    _name = 'dac.chat.prompt'
    _description = 'System prompt chatbot trả lời khách'
    _order = 'id desc'

    name = fields.Char(string='Phiên bản', required=True, default='Bản tay')
    mode = fields.Selection(MODES, string='Bộ', required=True, default='consult', index=True,
        help='Chào hỏi · Hỏi thông tin · Tư vấn. Mỗi bộ có một bản Đang dùng riêng; chatbot chọn bộ theo cấu hình.')
    state = fields.Selection([('queued', 'Agent đang viết'), ('draft', 'Nháp'), ('active', 'Đang dùng'), ('archived', 'Cũ'),
                              ('failed', 'Lỗi')], string='Trạng thái', default='draft', required=True, readonly=True, copy=False, index=True)
    analysis_id = fields.Many2one('dac.chat.analysis', string='Từ đợt phân tích', readonly=True, ondelete='set null')
    parent_id = fields.Many2one('dac.chat.prompt', string='Tối ưu từ bản', readonly=True, ondelete='set null')
    optimize_note = fields.Text(string='Agent đã sửa gì (theo góp ý)', readonly=True)
    agent_model_id = fields.Many2one('dac.ai.model', string='Model Agent viết prompt', ondelete='set null',
        domain="[('connection_id.active', '=', True)]", help='Trống = thứ tự kết nối chung.')
    bot_model_id = fields.Many2one('dac.ai.model', string='Model chatbot trả lời khách', ondelete='set null',
        domain="[('connection_id.active', '=', True)]",
        help='Model dùng khi chatbot trả lời khách (và khi bấm Thử chatbot). Nên chọn model nhanh. Trống = thứ tự kết nối chung.')
    system_prompt = fields.Text(string='System prompt (sửa được)',
        help='Biến được code điền khi chạy: {{now}} thời điểm hiện tại · {{work_status}} trong/ngoài giờ · '
             '{{next_working_time}} mốc làm việc kế tiếp · {{customer_name}} tên khách.')
    info_fields_json = fields.Text(string='Thông tin cần xin (JSON)', default='[]',
        help='[{"key": "size", "label": "Kích thước", "ask_example": "...", "required": true}, ...]')
    rule_ids = fields.Many2many('dac.chat.rule', 'dac_chat_prompt_rule_rel', 'prompt_id', 'rule_id', string='Quy tắc đưa vào',
        help='Quy tắc đề xuất trong danh sách này sẽ được DUYỆT khi kích hoạt bản prompt.')
    runtime_preview = fields.Text(string='Bản đầy đủ chatbot nhận (xem trước)', compute='_compute_runtime_preview')
    generated_by = fields.Char(string='Agent đã dùng', readonly=True, copy=False)
    error_message = fields.Text(string='Lỗi', readonly=True, copy=False)
    activated_by_id = fields.Many2one('res.users', string='Người kích hoạt', readonly=True, copy=False)
    activated_at = fields.Datetime(string='Kích hoạt lúc', readonly=True, copy=False)

    @api.depends('system_prompt', 'info_fields_json')
    def _compute_runtime_preview(self):
        now = fields.Datetime.now()
        for prompt in self:
            prompt.runtime_preview = prompt._runtime_prompt(now, 'Chị Lan (ví dụ)') if prompt.system_prompt else False

    # ── Agent viết prompt ────────────────────────────────────────────────────
    @api.model
    def _generate(self, analysis):
        """Gọi Agent (model của đợt phân tích) → bản prompt nháp. Trả (prompt, nhãn model)."""
        Rule = self.env['dac.chat.rule']
        candidates = Rule.search(['|', ('state', '=', 'approved'),
                                  '&', ('state', '=', 'proposed'), '|', ('analysis_id', '=', analysis.id), ('source', '=', 'optimized'),
                                  ('category', 'not in', ('forbidden', 'handoff'))])
        # Quy tắc đã duyệt đang chờ được thay bằng quy tắc AI tối ưu → dùng quy tắc mới, tránh đưa trùng
        candidates = candidates.filtered(lambda r: not (r.state == 'approved' and r.merged_into_id.state == 'proposed'))
        labels = dict(CATEGORIES)
        rules_text = '\n'.join('[id %s] (%s, %s, %s) %s: %s%s' % (
            r.id, labels[r.category], r.priority, 'đã duyệt' if r.state == 'approved' else 'đề xuất', r.name, r.content,
            (' | Câu mẫu: %s' % r.example) if r.example else '') for r in candidates)
        Insight = self.env['dac.chat.insight']
        scope = analysis.summary_scope
        labels_text = json.dumps({'san_pham': Insight._tool_label_stats(analysis, 'product', scope=scope),
                                  'muc_dich': Insight._tool_label_stats(analysis, 'intent', scope=scope),
                                  'phu_hop_chatbot': Insight._tool_label_stats(analysis, 'bot_fit', scope=scope)}, ensure_ascii=False)[:8000]
        guidance = (self.env['dac.chat.config']._get().agent_guidance or '').strip() or '(không có)'
        text = ('%s\n\nĐỊNH HƯỚNG CỦA QUẢN LÝ (ưu tiên cao nhất):\n%s\n\nSỐ LIỆU:\n%s\n\nKẾT LUẬN CỦA AGENT ĐIỀU PHỐI:\n%s\n\nPHẠM VI CHATBOT (Agent điều phối đề xuất):\n%s\n\n'
                'PHÂN LOẠI HỘI THOẠI:\n%s\n\nKIẾN THỨC DOANH NGHIỆP (code tự chèn vào prompt; dùng để viết consult_guide và ví dụ đúng sự thật):\n%s\n\n'
                'QUY TẮC (chọn để đưa vào):\n%s') % (
            AGENT_TASK, guidance, analysis._stats_digest() if analysis.report_json else '(không có)', analysis.ai_summary or '(không có)',
            analysis.coord_bot_scope or '(không có)', labels_text, self.env['dac.chat.knowledge']._text_for_mode('consult') or '(chưa có)',
            rules_text or '(chưa có)')
        ai = self.env['dac.chat.task']._resolve('write_prompt', analysis.ai_model_id, analysis.ai_fallback)
        data, label = ai_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, text, GEN_SCHEMA,
                              'write_chatbot_prompt', 'Viết system prompt chatbot.', GEN_TIMEOUT)
        used_ids = {int(i) for i in data.get('used_rule_ids') or [] if str(i).isdigit()}
        name = 'Bản %s' % fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), fields.Datetime.now()).strftime('%d/%m/%Y %H:%M')
        rule_ids = candidates.filtered(lambda r: r.id in used_ids).ids
        prompts = self.create([{
            'name': name, 'mode': mode, 'state': 'draft', 'analysis_id': analysis.id, 'agent_model_id': ai.model.id,
            'bot_model_id': (self._active(mode).bot_model_id or analysis.ai_model_id).id,
            'system_prompt': self._assemble(data, mode),
            'info_fields_json': json.dumps(self._info_fields(data, mode), ensure_ascii=False, indent=1),
            'rule_ids': [(6, 0, rule_ids)], 'generated_by': label,
        } for mode, _label in MODES])
        return prompts, label

    @staticmethod
    def _info_fields(data, mode='consult'):
        """Thông tin cần xin của một bộ: Chào hỏi → không xin; Hỏi thông tin và Tư vấn → đủ danh sách."""
        if mode == 'greeting':
            return []
        used, out = set(), []
        for item in data.get('info_fields') or []:
            if isinstance(item, dict) and item.get('label'):
                key = _clean_key(item.get('key') or item['label'], used)
                phone = bool(PHONE_KEY_RE.match(key))
                out.append({'key': key, 'label': str(item['label'])[:80], 'ask_example': str(item.get('ask_example') or '')[:300],
                            'required': bool(item.get('required')) and not phone, **({'channel_only': 'facebook'} if phone else {})})
        return out[:12]

    @staticmethod
    def _assemble(data, mode='consult'):
        """Ghép các phần Agent viết thành system prompt có tiêu đề, theo bộ `mode`."""
        def block(title, body):
            body = (body or '').strip()
            return '# %s\n%s' % (title, body) if body else ''

        info = ChatPrompt._info_fields(data, mode)
        flow = data.get(MODE_FLOW_KEY[mode]) or next((data.get(k) for k in MODE_FLOW_FALLBACK.get(mode, ()) if data.get(k)), '')
        after_hours = data.get(MODE_AFTER_HOURS_KEY[mode]) or data.get('after_hours')
        examples = []
        for ex in data.get('examples') or []:
            if isinstance(ex, dict) and ex.get('customer') and ex.get('bot'):
                modes = [m for m in ex.get('modes') or [] if m in MODE_FLOW_KEY]
                if modes and mode not in modes:
                    continue  # đoạn mẫu của bộ khác
                bot = ex['bot'] if isinstance(ex['bot'], list) else [ex['bot']]
                # Giữ biến {{next_working_time}}: code điền mốc thật lúc chạy (không ghi cứng "thứ 2")
                examples.append('## %s\nKhách: %s\n%s' % (ex.get('situation') or 'Ví dụ', ex['customer'], '\n'.join('Bot: %s' % b for b in bot)))

        def info_line(f):
            note = ' — BẮT BUỘC' if f['required'] else ''
            if f.get('channel_only') == 'facebook':
                note = ' — chỉ khi khách nhắn Facebook, không bắt buộc; khách nhắn Zalo thì KHÔNG xin'
            return '- %s%s (key: %s)%s' % (f['label'], note, f['key'], (': "%s"' % f['ask_example']) if f['ask_example'] else '')

        parts = [
            block('Vai trò', data.get('persona')),
            '# Bối cảnh lúc trả lời (hệ thống tự điền)\n- Bây giờ: {{now}}\n- Tình trạng: {{work_status}}\n'
            '- Mốc làm việc kế tiếp: {{next_working_time}}\n- Kênh khách nhắn: {{channel}}\n- Tên khách: {{customer_name}}',
            '# Chế độ: %s' % dict(MODES)[mode].upper(),
            block('Quy trình với khách mới', flow),
            block('Khi khách nhắn ngoài giờ làm việc', after_hours),
            block('Thông tin cần xin', '\n'.join(info_line(f) for f in info) if info else
                  'Bộ này KHÔNG xin thông tin chi tiết: chỉ hỏi khách cần làm gì rồi báo bạn phụ trách sẽ liên hệ.'),
            block('Cách nhắn', '\n'.join('- %s' % r for r in data.get('style_rules') or [] if r)),
            block('Tư vấn sơ bộ', data.get('consult_guide') if mode == 'consult' else ''),
            block('Hội thoại mẫu', '\n\n'.join(examples)),
        ]
        return '\n\n'.join(p for p in parts if p)

    # ── Chạy ─────────────────────────────────────────────────────────────────
    @api.model
    def _active(self, mode='consult'):
        return self.search([('state', '=', 'active'), ('mode', '=', mode)], limit=1)

    def _info_list(self):
        try:
            items = json.loads(self.info_fields_json or '[]')
        except ValueError:
            return []
        return [i for i in items if isinstance(i, dict) and KEY_RE.match(str(i.get('key') or ''))]

    def _context_values(self, now, customer_name, channel='zalo'):
        _start, next_label = cs.next_working(now, working_time.WORK_DAYS, working_time.WORK_SHIFTS)
        in_hours = cs.in_work_hours(now, working_time.WORK_DAYS, working_time.WORK_SHIFTS)
        return {
            '{{now}}': cs.now_label(now),
            '{{work_status}}': 'TRONG giờ làm việc (nhân viên đang làm, sẽ trả lời sớm)' if in_hours else
                               'NGOÀI giờ làm việc (giờ làm: thứ 2–thứ 7, 8:00–12:00 và 13:30–17:30; Chủ nhật nghỉ)',
            '{{next_working_time}}': 'trong hôm nay' if in_hours else next_label,
            '{{customer_name}}': customer_name or 'chưa rõ (gọi "anh/chị")',
            '{{channel}}': dict(CHANNELS)[channel_key(channel)],
        }

    def _runtime_prompt(self, now, customer_name='', channel='zalo'):
        self.ensure_one()
        text = self.system_prompt or ''
        for key, value in self._context_values(now, customer_name, channel).items():
            text = text.replace(key, value)
        guard = self.env['dac.chat.rule'].sudo().search([('state', '=', 'approved'), ('category', 'in', ('forbidden', 'handoff'))])
        guard_text = '\n'.join('- %s: %s' % (r.name, r.content.strip()) for r in guard)
        keys = ', '.join(i['key'] for i in self._info_list()) or '(không có)'
        feedback = self.env['dac.chat.feedback']._text_for_mode(self.mode, self)
        if feedback:
            text += '\n\n# GÓP Ý CỦA NGƯỜI DUYỆT (rút từ các lần duyệt trước; làm theo khi phù hợp)\n' + feedback
        return (
            '%s\n\n# THÔNG TIN DOANH NGHIỆP (chỉ dùng thông tin này khi trả lời về công ty/sản phẩm; không có thì nói sẽ hỏi bạn phụ trách, '
            'tuyệt đối không bịa; thời gian, bảo hành chỉ là thông tin chung, không phải cam kết)\n%s\n\n'
            '# TUYỆT ĐỐI TUÂN THỦ (quy tắc đã duyệt, ưu tiên cao nhất, đè lên mọi phần khác)\n%s\n- %s\n'
            '- ' + IDENTITY_RULE + '\n'
            '- Phạm vi của bạn chỉ là chào và lấy thông tin: không báo giá, không hứa thời gian, không chốt đơn, không gửi số tài khoản, '
            'không nhận cọc.\n'
            '- Không bịa thông tin về DAC. Không chắc thì nói sẽ chuyển bạn phụ trách.\n\n'
            '# Cách trả kết quả (bắt buộc)\nGọi tool reply_customer: messages = 1–3 tin sẽ gửi khách (ngắn, đúng giọng ở trên); '
            'collected = thông tin khách ĐÃ cung cấp trong cả đoạn chat, theo key: %s; handoff = true khi cần nhân viên '
            '(khách muốn chốt/cọc/hỏi STK, phàn nàn, khách cũ hỏi đơn, muốn gặp người thật, ngoài phạm vi, hoặc %s); '
            'handoff_reason = lý do ngắn.'
        ) % (text, self.env['dac.chat.knowledge']._text_for_mode(self.mode) or '(chưa có)', guard_text or '(chưa có)',
             CHANNEL_RULES[channel_key(channel)], keys,
             MODE_HANDOFF.get(self.mode, MODE_HANDOFF['consult']))

    def _reply_schema(self):
        props = {i['key']: {'type': 'string', 'description': i.get('label') or i['key']} for i in self._info_list()}
        collected = {'type': 'object', 'properties': props} if props else {'type': 'object', 'properties': {'note': {'type': 'string'}}}
        return {'type': 'object', 'properties': {
            'messages': {'type': 'array', 'items': {'type': 'string'}, 'description': '1–3 tin gửi khách.'},
            'collected': collected,
            'handoff': {'type': 'boolean'},
            'handoff_reason': {'type': 'string'},
            'returning_customer': {'type': 'boolean', 'description': 'true nếu tin của khách cho thấy họ ĐÃ liên hệ/đặt hàng với shop '
                                   'trước đó (gửi file/thông tin cho đơn cũ, nhắc lần trước, nhắc tên nhân viên, "như hôm qua"...).'},
            'returning_reason': {'type': 'string'},
        }, 'required': ['messages', 'handoff']}

    def _reply(self, history, now, customer_name='', channel='zalo', note=''):
        """history: [(vai trò 'customer'|'bot'|'staff', nội dung)] cũ → mới. Trả dict(messages, collected, handoff, handoff_reason,
        blocked, model). Không gửi gì cho khách — nơi gọi quyết định gửi."""
        self.ensure_one()
        lines = '\n'.join('%s: %s' % ({'customer': 'Khách', 'bot': 'Bot', 'staff': 'Nhân viên'}.get(role, role), cs.mask_phones(text))
                          for role, text in history if text)
        user_text = 'Đoạn chat (cũ → mới), dòng cuối là tin mới nhất của khách:\n%s\n\nHãy trả lời khách.' % (lines or '(trống)')
        if note:
            user_text += '\n\n' + note
        system = self._runtime_prompt(now, customer_name, channel)
        started = time.monotonic()
        # Model + thinking của bộ này (Chào hỏi / Hỏi thông tin / Tư vấn) chọn ở màn hình "Model & thinking theo tác vụ"
        ai = self.env['dac.chat.task']._resolve('bot_%s' % self.mode, self.bot_model_id, True, use_config_model=False)
        data, label, _sent, usage = vision_call(ai.env, ai.model, ai.fallback, system, user_text, [], self._reply_schema(),
                                                'reply_customer', 'Trả lời khách.', REPLY_TIMEOUT)
        result = self._guard(data, label)
        result.update(usage=usage or {}, seconds=round(time.monotonic() - started, 1), system_prompt=system, user_text=user_text,
                      thinking=ai.effort or 'auto')
        return result

    # ── Demo chatbot (client action dac_chat_demo) ───────────────────────────
    @api.model
    def demo_options(self):
        """Danh sách bản prompt để chọn, mốc giờ giả lập, giá token tham khảo."""
        modes = dict(MODES)
        states = {'active': 'Đang dùng', 'draft': 'Nháp', 'archived': 'Cũ'}
        prompts = self.search([('state', 'in', ('active', 'draft', 'archived'))], order='mode, state, id desc', limit=60)
        order = {'active': 0, 'draft': 1, 'archived': 2}
        prompts = prompts.sorted(lambda p: ([m for m, _l in MODES].index(p.mode), order[p.state], -p.id))
        default = self._active('greeting') or self._active('info') or self._active('consult') or prompts[:1]
        config = self.env['dac.chat.config']._get()

        def bot_label(prompt):
            """Model và thinking sẽ thực sự dùng cho bản prompt này (theo màn hình Model & thinking theo tác vụ)."""
            ai = self.env['dac.chat.task']._resolve('bot_%s' % prompt.mode, prompt.bot_model_id, True, use_config_model=False)
            return (ai.model.display_name or 'Thứ tự kết nối chung') + (' · thinking %s' % ai.effort if ai.effort else '')

        return {
            'prompts': [{'id': p.id, 'mode': p.mode, 'label': '%s · %s · %s' % (modes[p.mode], states[p.state], p.name),
                         'model': bot_label(p)} for p in prompts],
            'default_prompt_id': default.id or False,
            'presets': self._demo_presets(),
            'channels': [{'key': key, 'label': label} for key, label in CHANNELS],
            'pricing': {'in': config.price_in, 'out': config.price_out, 'rate': config.usd_rate},
        }

    @api.model
    def _demo_presets(self):
        """Mốc giờ hay dùng để thử (giờ VN) → chuỗi UTC 'YYYY-MM-DD HH:MM:SS'."""
        now_local = datetime.now(cs.TZ)

        def at(days, hour, minute=0):
            moment = (now_local + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        def next_weekday(weekday):
            return (weekday - now_local.weekday()) % 7

        work_day = 1 if now_local.weekday() == 6 else 0  # Chủ nhật → thứ 2
        return [
            {'key': 'now', 'label': 'Bây giờ', 'at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')},
            {'key': 'work', 'label': 'Giờ làm (10:00)', 'at': at(work_day, 10)},
            {'key': 'lunch', 'label': 'Nghỉ trưa (12:30)', 'at': at(work_day, 12, 30)},
            {'key': 'evening', 'label': 'Tối (20:00)', 'at': at(0, 20)},
            {'key': 'saturday', 'label': 'Tối thứ 7 (19:00)', 'at': at(next_weekday(5), 19)},
            {'key': 'sunday', 'label': 'Chủ nhật (10:00)', 'at': at(next_weekday(6), 10)},
        ]

    @api.model
    def demo_reply(self, prompt_id, history, at=False, customer_name='', channel='zalo'):
        """Một lượt chatbot trả lời trong màn hình demo (KHÔNG gửi gì cho khách).
        history: [[vai trò 'customer'|'bot'|'staff', nội dung], ...]; at: 'YYYY-MM-DD HH:MM:SS' (UTC) hoặc trống = bây giờ."""
        prompt = self.browse(int(prompt_id)).exists()
        if not prompt:
            raise UserError('Bản prompt không còn.')
        clean = [(str(role), str(text).strip()[:2000]) for role, text in history or [] if role in ('customer', 'bot', 'staff') and str(text).strip()]
        if not clean or clean[-1][0] != 'customer':
            raise UserError('Tin cuối phải là tin của khách.')
        moment = fields.Datetime.to_datetime(at) if at else fields.Datetime.now()
        result = prompt._reply(clean[-40:], moment, customer_name or '', channel)
        values = prompt._context_values(moment, customer_name or '', channel)
        result['context'] = {'now': values['{{now}}'], 'work_status': values['{{work_status}}'], 'next': values['{{next_working_time}}'],
                             'channel': values['{{channel}}']}
        result['prompt_chars'] = len(result['system_prompt'])
        return result

    @api.model
    def demo_sample(self):
        """Tin mở đầu của một khách mới thật gần đây (ngẫu nhiên, SĐT đã che) để thử."""
        return self.env['dac.chat.prompt.test']._sample_opening() or {}

    # ── Huấn luyện & kiểm thử: góp ý → Agent tối ưu prompt ───────────────────
    def _feedback_rows(self):
        self.ensure_one()
        Feedback = self.env['dac.chat.feedback'].with_context(active_test=False)
        items = Feedback.search([('mode', '=', self.mode)], limit=60)
        return [{'id': f.id, 'text': f.text, 'customer': f.customer_text or '', 'bot': f.bot_text or '', 'active': f.active,
                 'applied': self in f.applied_prompt_ids, 'source': dict(f._fields['source'].selection).get(f.source),
                 'author': f.author or '', 'on_this': f.prompt_id == self,
                 'date': fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), f.create_date).strftime('%d/%m %H:%M')}
                for f in items]

    @api.model
    def demo_feedback(self, prompt_id):
        """Góp ý của bộ (Chào hỏi / Hỏi thông tin / Tư vấn) của bản đang thử; `pending` = số góp ý đang bật chưa có trong bản này."""
        prompt = self.browse(int(prompt_id)).exists()
        if not prompt:
            return {'items': [], 'pending': 0}
        rows = prompt._feedback_rows()
        return {'items': rows, 'pending': len([r for r in rows if r['active'] and not r['applied']]),
                'parent': prompt.parent_id.name or False, 'note': prompt.optimize_note or False}

    @api.model
    def demo_feedback_add(self, prompt_id, text, customer_text='', bot_text=''):
        prompt = self.browse(int(prompt_id)).exists()
        text = (text or '').strip()
        if not prompt or not text:
            raise UserError('Nhập nội dung góp ý.')
        self.env['dac.chat.feedback'].create({
            'mode': prompt.mode, 'text': text[:1000], 'source': 'trainer', 'prompt_id': prompt.id, 'author': self.env.user.name,
            'customer_text': (customer_text or '')[:1000] or False, 'bot_text': (bot_text or '')[:1000] or False})
        return self.demo_feedback(prompt.id)

    @api.model
    def demo_feedback_toggle(self, feedback_id, prompt_id):
        item = self.env['dac.chat.feedback'].with_context(active_test=False).browse(int(feedback_id)).exists()
        item.active = not item.active
        return self.demo_feedback(prompt_id)

    @api.model
    def demo_feedback_delete(self, feedback_id, prompt_id):
        self.env['dac.chat.feedback'].with_context(active_test=False).browse(int(feedback_id)).exists().unlink()
        return self.demo_feedback(prompt_id)

    @api.model
    def demo_optimize(self, prompt_id):
        """Xếp lượt Agent viết lại system prompt của bản này theo các góp ý đang bật (chạy nền) → bản nháp mới."""
        prompt = self.browse(int(prompt_id)).exists()
        if not prompt or not (prompt.system_prompt or '').strip():
            raise UserError('Bản prompt không còn hoặc đang trống.')
        if not self.env['dac.chat.feedback']._pending(prompt.mode, prompt):
            raise UserError('Chưa có góp ý mới nào cho bản này.')
        name = 'Bản %s (tối ưu từ góp ý)' % fields.Datetime.context_timestamp(
            self.with_context(tz='Asia/Ho_Chi_Minh'), fields.Datetime.now()).strftime('%d/%m/%Y %H:%M')
        new = self.create({'name': name, 'mode': prompt.mode, 'state': 'queued', 'parent_id': prompt.id, 'analysis_id': prompt.analysis_id.id,
                           'bot_model_id': prompt.bot_model_id.id, 'system_prompt': prompt.system_prompt,
                           'info_fields_json': prompt.info_fields_json, 'rule_ids': [(6, 0, prompt.rule_ids.ids)]})
        # sudo(): quản lý không có quyền ir.cron; chỉ hẹn cron của addon này chạy sớm
        self.env.ref('dac_chat_playbook.cron_process_analysis').sudo()._trigger()
        return {'id': new.id}

    @api.model
    def demo_prompt_state(self, prompt_id):
        prompt = self.browse(int(prompt_id)).exists()
        return {'state': prompt.state, 'error': prompt.error_message or False, 'note': prompt.optimize_note or False,
                'name': prompt.name, 'model': prompt.generated_by or False} if prompt else {}

    @api.model
    def _cron_optimize(self):
        """Chạy các lượt "AI tối ưu prompt từ góp ý" đang chờ. Không raise."""
        for prompt in self.sudo().search([('state', '=', 'queued'), ('parent_id', '!=', False)], order='id', limit=3):
            try:
                prompt._optimize_from_feedback()
                self.env.cr.commit()
            except Exception as error:  # noqa: BLE001 — cron không được raise
                self.env.cr.rollback()
                prompt.write({'state': 'failed', 'error_message': str(error)[:500]})
                self.env.cr.commit()

    def _optimize_from_feedback(self):
        self.ensure_one()
        parent = self.parent_id
        feedback = self.env['dac.chat.feedback']._pending(self.mode, parent)
        guidance = (self.env['dac.chat.config']._get().agent_guidance or '').strip() or '(không có)'
        text = ('%s\n\nĐỊNH HƯỚNG CỦA QUẢN LÝ (ưu tiên cao nhất):\n%s\n\nBỘ: %s\n\nGÓP Ý CẦN ÁP DỤNG (%s):\n%s\n\n'
                'SYSTEM PROMPT HIỆN TẠI:\n%s') % (OPTIMIZE_PROMPT_TASK, guidance, dict(MODES)[self.mode], len(feedback),
                                                  '\n'.join(f._line() for f in feedback) or '(không có)', parent.system_prompt)
        # Model + thinking ở dòng "Cải tiến prompt từ góp ý" (mặc định Haiku, thinking tối đa)
        ai = self.env['dac.chat.task']._resolve('improve_prompt', self.env['dac.chat.config']._get().ai_model_id, True)
        data, label = ai_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, text, OPTIMIZE_PROMPT_SCHEMA, 'rewrite_prompt',
                              'Viết lại system prompt theo góp ý.', IMPROVE_TIMEOUT)
        label = '%s%s' % (label, ' · thinking %s' % ai.effort if ai.effort else '')
        new_text = (data.get('system_prompt') or '').strip()
        missing = [p for p in PLACEHOLDERS if p in parent.system_prompt and p not in new_text]
        if len(new_text) < 200 or missing:
            raise UserError('Agent trả prompt không hợp lệ%s.' % (' (thiếu %s)' % ', '.join(missing) if missing else ''))
        changes = [str(c).strip() for c in data.get('changes') or [] if str(c).strip()]
        self.write({'system_prompt': new_text, 'state': 'draft', 'generated_by': label, 'error_message': False,
                    'optimize_note': '\n'.join('- %s' % c for c in changes) or False})
        # Góp ý đã viết vào bản cha thì bản mới cũng đã có; cộng thêm các góp ý vừa áp dụng
        (feedback | self.env['dac.chat.feedback'].with_context(active_test=False).search([('applied_prompt_ids', 'in', parent.ids)])).write(
            {'applied_prompt_ids': [(4, self.id)]})

    @staticmethod
    def _guard(data, label=''):
        """Chặn lần cuối: tin có giá/STK/cam kết thì không gửi, chuyển nhân viên."""
        messages = [str(m).strip() for m in data.get('messages') or [] if str(m).strip()][:3]
        identity = [m for m in messages if IDENTITY_RE.search(m) or IDENTITY_AI_RE.search(m)]
        blocked = [m for m in messages if BLOCK_RE.search(m) and m not in identity]
        result = {'messages': [m for m in messages if m not in blocked and m not in identity], 'collected': data.get('collected') or {},
                  'handoff': bool(data.get('handoff')), 'handoff_reason': str(data.get('handoff_reason') or ''), 'blocked': blocked,
                  'returning_customer': bool(data.get('returning_customer')), 'returning_reason': str(data.get('returning_reason') or ''),
                  'model': label, 'identity_blocked': identity}
        result['blocked'] = blocked + identity
        if blocked:
            result['handoff'] = True
            result['handoff_reason'] = ('Đã chặn tin có giá/STK/cam kết. ' + result['handoff_reason']).strip()
            if not result['messages']:
                result['messages'] = ['Dạ em chuyển bạn phụ trách hỗ trợ mình ngay ạ.']
        elif identity and not result['messages']:
            result['messages'] = ['Dạ em chào anh/chị ạ, anh/chị đang cần làm gì để em hỗ trợ ạ?']
        return result

    # ── Nút bấm ──────────────────────────────────────────────────────────────
    def action_activate(self):
        self.ensure_one()
        if self.state not in ('draft', 'archived'):
            raise UserError('Chỉ kích hoạt được bản nháp hoặc bản cũ.')
        if not (self.system_prompt or '').strip():
            raise UserError('System prompt đang trống.')
        self.search([('state', '=', 'active'), ('mode', '=', self.mode), ('id', '!=', self.id)]).write({'state': 'archived'})
        self.write({'state': 'active', 'activated_by_id': self.env.user.id, 'activated_at': fields.Datetime.now()})

    def action_archive_prompt(self):
        self.filtered(lambda p: p.state == 'active').write({'state': 'archived'})

    def action_test(self):
        """Mở màn hình Huấn luyện & kiểm thử với bản prompt này."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'dac_chat_demo', 'name': 'Huấn luyện & kiểm thử', 'params': {'prompt_id': self.id}}


class ChatPromptTest(models.TransientModel):
    """Thử chatbot với một bản prompt: KHÔNG gửi gì cho khách."""
    _name = 'dac.chat.prompt.test'
    _description = 'Thử chatbot'

    prompt_id = fields.Many2one('dac.chat.prompt', string='Bản prompt', required=True, ondelete='cascade')
    at = fields.Datetime(string='Giả lập lúc khách nhắn', required=True, default=fields.Datetime.now,
                         help='Đổi sang Chủ nhật/buổi tối để thử cách chatbot hẹn ngoài giờ.')
    customer_name = fields.Char(string='Tên khách', default='Chị Lan')
    channel = fields.Selection(CHANNELS, string='Kênh khách nhắn', default='zalo', required=True)
    conversation = fields.Text(string='Đoạn chat', required=True, default='KH: Cho em hỏi làm bảng hiệu alu giá sao ạ',
        help='Mỗi dòng một tin, bắt đầu bằng "KH:" (khách), "Bot:" hoặc "NV:" (nhân viên). Dòng cuối là tin mới nhất của khách.')
    work_status = fields.Char(string='Bối cảnh', readonly=True)
    reply = fields.Text(string='Chatbot trả lời', readonly=True)
    collected = fields.Text(string='Thông tin đã lấy được', readonly=True)
    handoff = fields.Char(string='Chuyển nhân viên', readonly=True)
    blocked = fields.Text(string='Tin bị chặn (không gửi)', readonly=True)
    used_model = fields.Char(string='Model đã trả lời', readonly=True)

    def _history(self):
        history = []
        for line in (self.conversation or '').splitlines():
            line = line.strip()
            if not line:
                continue
            head, _sep, body = line.partition(':')
            role = {'kh': 'customer', 'khach': 'customer', 'bot': 'bot', 'nv': 'staff'}.get(cs.fold(head), None)
            if role and body:
                history.append((role, body.strip()))
            elif history:
                history[-1] = (history[-1][0], history[-1][1] + ' ' + line)
            else:
                history.append(('customer', line))
        return history

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'name': 'Thử chatbot — %s' % self.prompt_id.name}

    def action_send(self):
        self.ensure_one()
        if not self._history():
            raise UserError('Nhập đoạn chat, mỗi dòng bắt đầu bằng "KH:".')
        result = self.prompt_id._reply(self._history(), self.at, self.customer_name, self.channel)
        values = self.prompt_id._context_values(self.at, self.customer_name, self.channel)
        self.write({
            'work_status': '%s · %s · mốc kế tiếp: %s' % (values['{{now}}'], values['{{work_status}}'], values['{{next_working_time}}']),
            'reply': '\n'.join('Bot: %s' % m for m in result['messages']),
            'collected': json.dumps(result['collected'], ensure_ascii=False, indent=1) if result['collected'] else '(chưa có)',
            'handoff': ('Có — %s' % result['handoff_reason']) if result['handoff'] else 'Không',
            'blocked': '\n'.join(result['blocked']) or False, 'used_model': result['model'],
        })
        return self._reopen()

    def action_continue(self):
        """Đưa câu trả lời của bot vào đoạn chat để gõ tiếp tin khách."""
        self.ensure_one()
        if self.reply:
            self.write({'conversation': '%s\n%s\nKH: ' % (self.conversation.rstrip(), self.reply), 'reply': False, 'collected': False,
                        'handoff': False, 'blocked': False})
        return self._reopen()

    @api.model
    def _sample_opening(self):
        """Tin mở đầu của một khách mới thật gần đây (ngẫu nhiên): dict(customer_name, text, at) hoặc None."""
        self.env.flush_all()
        self.env.cr.execute("""
            SELECT c.id FROM dac_pancake_conversation c
             WHERE NOT COALESCE(c.is_internal, FALSE) AND c.inserted_at_fm >= now() - interval '120 days'
             ORDER BY random() LIMIT 40""")
        # sudo(): chỉ đọc tin khách để thử, không ghi; người dùng là quản lý
        Message = self.env['dac.pancake.message'].sudo()
        from odoo.addons.dac_pancake_remind.models.remind_wait import classify
        for (conversation_id,) in self.env.cr.fetchall():
            first = Message.search([('conversation_id', '=', conversation_id)], order='inserted_at, id', limit=6)
            customer = [m for m in first if classify(m) == 'customer']
            if customer and classify(first[0]) == 'customer':
                text = ' / '.join(cs.plain(m.content_html) for m in customer[:3] if cs.plain(m.content_html))
                if text:
                    return {'customer_name': customer[0].conversation_id.customer_name or '', 'text': cs.mask_phones(text)[:500],
                            'at': fields.Datetime.to_string(customer[0].inserted_at)}
        return None

    def action_sample(self):
        """Lấy tin đầu của một khách mới thật gần đây (ngẫu nhiên) để thử."""
        self.ensure_one()
        sample = self._sample_opening()
        if sample:
            self.write({'conversation': 'KH: %s' % sample['text'], 'at': sample['at'], 'customer_name': sample['customer_name'],
                        'reply': False, 'collected': False, 'handoff': False, 'blocked': False})
        return self._reopen()
