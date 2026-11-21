"""System prompt cho chatbot trả lời khách: Agent tổng hợp viết ra từ kết quả phân tích, quản lý thử rồi kích hoạt.

Cấu trúc khi chatbot chạy (`_runtime_prompt`):
1. Phần Agent viết, quản lý sửa được (`system_prompt`), có biến {{now}}, {{work_status}}, {{next_working_time}}, {{customer_name}}.
2. Quy tắc CẤM và CHUYỂN NHÂN VIÊN đã duyệt — code chèn nguyên văn lúc chạy, không qua AI, không sửa được ở đây.
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
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,30}$')
# Tin chatbot không bao giờ được gửi: số tiền, số tài khoản, cam kết chốt/cọc
BLOCK_RE = re.compile(
    r'\d{1,3}(?:[.,]\d{3})+|\d+\s?(?:k|tr|triệu|nghìn|ngàn|đ|vnđ|vnd)\b|số tài khoản|\bstk\b|chuyển khoản|đặt cọc|cọc trước|'
    r'giảm giá|khuyến mãi|cam kết|chắc chắn (?:xong|giao|kịp)', re.IGNORECASE)
PLACEHOLDERS = ('{{now}}', '{{work_status}}', '{{next_working_time}}', '{{customer_name}}')
MODES = [('greeting', 'Chào hỏi'), ('info', 'Hỏi thông tin'), ('consult', 'Tư vấn')]
MODE_FLOW_KEY = {'greeting': 'flow_greeting', 'info': 'flow_info', 'consult': 'flow_consult'}
MODE_FLOW_FALLBACK = {'info': ('flow_basic', 'flow'), 'consult': ('flow_full', 'flow')}  # tên cũ (trước bản 1.6)
MODE_HANDOFF = {
    'greeting': 'đã chào và biết khách cần làm gì (bộ này KHÔNG xin thêm thông tin chi tiết)',
    'info': 'đã đủ các thông tin bắt buộc ở trên',
    'consult': 'đã đủ thông tin bắt buộc và đã trả lời/tư vấn sơ bộ xong, hoặc khách cần báo giá, chốt đơn',
}

AGENT_TASK = """Bạn là AGENT TỔNG HỢP. Từ số liệu, nhận xét và quy tắc dưới đây, hãy viết SYSTEM PROMPT hoàn chỉnh cho chatbot trả lời
khách mới nhắn Zalo của DAC. Chatbot đọc prompt này mỗi lần trả lời, nên viết rõ ràng, cụ thể, làm theo được.

Yêu cầu:
- persona: chatbot là ai, xưng hô, giọng nhắn; độ dài và số tin mỗi lượt lấy từ số liệu nhân viên giỏi.
- Viết 3 BỘ quy trình cho 3 chế độ chatbot (cùng giọng, cùng persona):
  · flow_greeting — CHÀO HỎI: chào ngay, hỏi một câu khách cần làm gì, báo bạn phụ trách sẽ liên hệ (trong giờ: ngay;
    ngoài giờ: mốc {{next_working_time}}); không hỏi thêm chi tiết, không tư vấn.
  · flow_info — HỎI THÔNG TIN: chào → hỏi nhu cầu → xin lần lượt các thông tin cần để báo giá (mỗi lượt 1–2 ý) → tóm tắt lại →
    báo bạn phụ trách sẽ báo giá. Trả lời ngắn câu hỏi cơ bản (địa chỉ, giờ làm, có làm sản phẩm X không) từ KIẾN THỨC DOANH NGHIỆP,
    không tư vấn sâu.
  · flow_consult — TƯ VẤN: như flow_info, cộng trả lời câu hỏi về sản phẩm, chất liệu, quy trình, bảo hành và tư vấn sơ bộ (gợi ý lựa
    chọn phổ biến phù hợp nhu cầu) dựa trên KIẾN THỨC DOANH NGHIỆP; không báo giá, không cam kết; xong thì chuyển bạn phụ trách.
  Khách đã nói thông tin nào thì không hỏi lại. Đủ thông tin của bộ đó thì cảm ơn và báo chuyển nhân viên.
- after_hours: cách xử lý NGOÀI giờ: chào, xin thông tin cơ bản, hẹn đúng mốc {{next_working_time}} (code điền sẵn, tuyệt đối không tự
  đoán ngày giờ khác).
- consult_guide: hướng dẫn tư vấn sơ bộ cho các nhóm sản phẩm DAC, DỰA TRÊN KIẾN THỨC DOANH NGHIỆP bên dưới (đúng danh mục, chất liệu
  công ty có) — mô tả cách chọn phổ biến và câu hỏi cần làm rõ, không giá, không thông số chắc chắn. Không chép lại kiến thức (code tự chèn).
- style_rules: các quy tắc cách nhắn ngắn gọn.
- info_fields: thông tin cần xin, theo thứ tự nên hỏi; key là chữ thường không dấu (vd product, size, quantity, material, design_file,
  deadline, area, phone); required = true nếu thiếu thì nhân viên không báo giá được.
- examples: 3–6 đoạn mẫu ngắn (trong giờ, ngoài giờ/Chủ nhật, khách hỏi giá, khách muốn chốt/cọc → chuyển nhân viên); câu của bot đúng
  giọng nhân viên DAC; dùng {{next_working_time}} trong mẫu ngoài giờ.
- used_rule_ids: id các quy tắc bạn đã đưa vào prompt (chỉ chọn quy tắc hợp lý; bỏ quy tắc trái phạm vi chatbot).
- Không nhắc lại quy tắc cấm/chuyển nhân viên: code sẽ tự chèn nguyên văn."""

GEN_SCHEMA = {
    'type': 'object',
    'properties': {
        'persona': {'type': 'string'},
        'flow_greeting': {'type': 'string', 'description': 'Quy trình bộ CHỈ CHÀO HỎI.'},
        'flow_info': {'type': 'string', 'description': 'Quy trình bộ HỎI THÔNG TIN.'},
        'flow_consult': {'type': 'string', 'description': 'Quy trình bộ TƯ VẤN.'},
        'after_hours': {'type': 'string'},
        'consult_guide': {'type': 'string'},
        'style_rules': {'type': 'array', 'items': {'type': 'string'}},
        'info_fields': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'key': {'type': 'string'}, 'label': {'type': 'string'}, 'ask_example': {'type': 'string'}, 'required': {'type': 'boolean'},
},
            'required': ['key', 'label']}},
        'examples': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'situation': {'type': 'string'}, 'customer': {'type': 'string'}, 'bot': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['situation', 'customer', 'bot']}},
        'used_rule_ids': {'type': 'array', 'items': {'type': 'integer'}},
    },
    'required': ['persona', 'flow_greeting', 'flow_info', 'flow_consult', 'after_hours', 'info_fields', 'examples'],
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
        candidates = Rule.search(['|', ('state', '=', 'approved'), '&', ('analysis_id', '=', analysis.id), ('state', '=', 'proposed'),
                                  ('category', 'not in', ('forbidden', 'handoff'))])
        labels = dict(CATEGORIES)
        rules_text = '\n'.join('[id %s] (%s, %s, %s) %s: %s%s' % (
            r.id, labels[r.category], r.priority, 'đã duyệt' if r.state == 'approved' else 'đề xuất', r.name, r.content,
            (' | Câu mẫu: %s' % r.example) if r.example else '') for r in candidates)
        Insight = self.env['dac.chat.insight']
        labels_text = json.dumps({'san_pham': Insight._tool_label_stats(analysis, 'product'),
                                  'muc_dich': Insight._tool_label_stats(analysis, 'intent'),
                                  'phu_hop_chatbot': Insight._tool_label_stats(analysis, 'bot_fit')}, ensure_ascii=False)[:8000]
        text = ('%s\n\nSỐ LIỆU:\n%s\n\nKẾT LUẬN CỦA AGENT ĐIỀU PHỐI:\n%s\n\nPHẠM VI CHATBOT (Agent điều phối đề xuất):\n%s\n\n'
                'PHÂN LOẠI HỘI THOẠI:\n%s\n\nKIẾN THỨC DOANH NGHIỆP (code tự chèn vào prompt; dùng để viết consult_guide và ví dụ đúng sự thật):\n%s\n\n'
                'QUY TẮC (chọn để đưa vào):\n%s') % (
            AGENT_TASK, analysis._stats_digest() if analysis.report_json else '(không có)', analysis.ai_summary or '(không có)',
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
                out.append({'key': _clean_key(item.get('key') or item['label'], used), 'label': str(item['label'])[:80],
                            'ask_example': str(item.get('ask_example') or '')[:300], 'required': bool(item.get('required'))})
        return out[:12]

    @staticmethod
    def _assemble(data, mode='consult'):
        """Ghép các phần Agent viết thành system prompt có tiêu đề, theo bộ `mode`."""
        def block(title, body):
            body = (body or '').strip()
            return '# %s\n%s' % (title, body) if body else ''

        info = ChatPrompt._info_fields(data, mode)
        flow = data.get(MODE_FLOW_KEY[mode]) or next((data.get(k) for k in MODE_FLOW_FALLBACK.get(mode, ()) if data.get(k)), '')
        examples = []
        for ex in data.get('examples') or []:
            if isinstance(ex, dict) and ex.get('customer') and ex.get('bot'):
                bot = ex['bot'] if isinstance(ex['bot'], list) else [ex['bot']]
                text = '## %s\nKhách: %s\n%s' % (ex.get('situation') or 'Ví dụ', ex['customer'], '\n'.join('Bot: %s' % b for b in bot))
                # Ví dụ chỉ để minh họa: giữ mốc cố định, không đổi theo giờ chạy (mốc thật nằm ở phần Bối cảnh)
                examples.append(text.replace('{{next_working_time}}', '8:00 sáng thứ 2'))
        parts = [
            block('Vai trò', data.get('persona')),
            '# Bối cảnh lúc trả lời (hệ thống tự điền)\n- Bây giờ: {{now}}\n- Tình trạng: {{work_status}}\n'
            '- Mốc làm việc kế tiếp: {{next_working_time}}\n- Tên khách trên Zalo: {{customer_name}}',
            '# Chế độ: %s' % dict(MODES)[mode].upper(),
            block('Quy trình với khách mới', flow),
            block('Khi khách nhắn ngoài giờ làm việc', data.get('after_hours')),
            block('Thông tin cần xin', '\n'.join('- %s%s (key: %s)%s' % (
                f['label'], ' — BẮT BUỘC' if f['required'] else '', f['key'], (': "%s"' % f['ask_example']) if f['ask_example'] else '')
                for f in info) if info else 'Bộ này KHÔNG xin thông tin chi tiết: chỉ hỏi khách cần làm gì rồi báo bạn phụ trách sẽ liên hệ.'),
            block('Cách nhắn', '\n'.join('- %s' % r for r in data.get('style_rules') or [] if r)),
            block('Tư vấn sơ bộ', data.get('consult_guide') if mode != 'greeting' else ''),
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

    def _context_values(self, now, customer_name):
        _start, next_label = cs.next_working(now, working_time.WORK_DAYS, working_time.WORK_SHIFTS)
        in_hours = cs.in_work_hours(now, working_time.WORK_DAYS, working_time.WORK_SHIFTS)
        return {
            '{{now}}': cs.now_label(now),
            '{{work_status}}': 'TRONG giờ làm việc (nhân viên đang làm, sẽ trả lời sớm)' if in_hours else
                               'NGOÀI giờ làm việc (giờ làm: thứ 2–thứ 7, 8:00–12:00 và 13:30–17:30; Chủ nhật nghỉ)',
            '{{next_working_time}}': 'trong hôm nay' if in_hours else next_label,
            '{{customer_name}}': customer_name or 'chưa rõ (gọi "anh/chị")',
        }

    def _runtime_prompt(self, now, customer_name=''):
        self.ensure_one()
        text = self.system_prompt or ''
        for key, value in self._context_values(now, customer_name).items():
            text = text.replace(key, value)
        guard = self.env['dac.chat.rule'].sudo().search([('state', '=', 'approved'), ('category', 'in', ('forbidden', 'handoff'))])
        guard_text = '\n'.join('- %s: %s' % (r.name, r.content.strip()) for r in guard)
        keys = ', '.join(i['key'] for i in self._info_list()) or '(không có)'
        return (
            '%s\n\n# THÔNG TIN DOANH NGHIỆP (chỉ dùng thông tin này khi trả lời về công ty/sản phẩm; không có thì nói sẽ hỏi bạn phụ trách, '
            'tuyệt đối không bịa; thời gian, bảo hành chỉ là thông tin chung, không phải cam kết)\n%s\n\n'
            '# TUYỆT ĐỐI TUÂN THỦ (quy tắc đã duyệt, ưu tiên cao nhất, đè lên mọi phần khác)\n%s\n'
            '- Bạn chỉ là trợ lý chào khách: không báo giá, không hứa thời gian, không chốt đơn, không gửi số tài khoản, không nhận cọc.\n'
            '- Không bịa thông tin về DAC. Không chắc thì nói sẽ chuyển bạn phụ trách.\n\n'
            '# Cách trả kết quả (bắt buộc)\nGọi tool reply_customer: messages = 1–3 tin sẽ gửi khách (ngắn, đúng giọng ở trên); '
            'collected = thông tin khách ĐÃ cung cấp trong cả đoạn chat, theo key: %s; handoff = true khi cần nhân viên '
            '(khách muốn chốt/cọc/hỏi STK, phàn nàn, khách cũ hỏi đơn, muốn gặp người thật, ngoài phạm vi, hoặc %s); '
            'handoff_reason = lý do ngắn.'
        ) % (text, self.env['dac.chat.knowledge']._text_for_mode(self.mode) or '(chưa có)', guard_text or '(chưa có)', keys,
             MODE_HANDOFF.get(self.mode, MODE_HANDOFF['consult']))

    def _reply_schema(self):
        props = {i['key']: {'type': 'string', 'description': i.get('label') or i['key']} for i in self._info_list()}
        collected = {'type': 'object', 'properties': props} if props else {'type': 'object', 'properties': {'note': {'type': 'string'}}}
        return {'type': 'object', 'properties': {
            'messages': {'type': 'array', 'items': {'type': 'string'}, 'description': '1–3 tin gửi khách.'},
            'collected': collected,
            'handoff': {'type': 'boolean'},
            'handoff_reason': {'type': 'string'},
        }, 'required': ['messages', 'handoff']}

    def _reply(self, history, now, customer_name=''):
        """history: [(vai trò 'customer'|'bot'|'staff', nội dung)] cũ → mới. Trả dict(messages, collected, handoff, handoff_reason,
        blocked, model). Không gửi gì cho khách — nơi gọi quyết định gửi."""
        self.ensure_one()
        lines = '\n'.join('%s: %s' % ({'customer': 'Khách', 'bot': 'Bot', 'staff': 'Nhân viên'}.get(role, role), cs.mask_phones(text))
                          for role, text in history if text)
        user_text = 'Đoạn chat (cũ → mới), dòng cuối là tin mới nhất của khách:\n%s\n\nHãy trả lời khách.' % (lines or '(trống)')
        system = self._runtime_prompt(now, customer_name)
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
    def demo_reply(self, prompt_id, history, at=False, customer_name=''):
        """Một lượt chatbot trả lời trong màn hình demo (KHÔNG gửi gì cho khách).
        history: [[vai trò 'customer'|'bot'|'staff', nội dung], ...]; at: 'YYYY-MM-DD HH:MM:SS' (UTC) hoặc trống = bây giờ."""
        prompt = self.browse(int(prompt_id)).exists()
        if not prompt:
            raise UserError('Bản prompt không còn.')
        clean = [(str(role), str(text).strip()[:2000]) for role, text in history or [] if role in ('customer', 'bot', 'staff') and str(text).strip()]
        if not clean or clean[-1][0] != 'customer':
            raise UserError('Tin cuối phải là tin của khách.')
        moment = fields.Datetime.to_datetime(at) if at else fields.Datetime.now()
        result = prompt._reply(clean[-40:], moment, customer_name or '')
        values = prompt._context_values(moment, customer_name or '')
        result['context'] = {'now': values['{{now}}'], 'work_status': values['{{work_status}}'], 'next': values['{{next_working_time}}']}
        result['prompt_chars'] = len(result['system_prompt'])
        return result

    @api.model
    def demo_sample(self):
        """Tin mở đầu của một khách mới thật gần đây (ngẫu nhiên, SĐT đã che) để thử."""
        return self.env['dac.chat.prompt.test']._sample_opening() or {}

    @staticmethod
    def _guard(data, label=''):
        """Chặn lần cuối: tin có giá/STK/cam kết thì không gửi, chuyển nhân viên."""
        messages = [str(m).strip() for m in data.get('messages') or [] if str(m).strip()][:3]
        blocked = [m for m in messages if BLOCK_RE.search(m)]
        result = {'messages': [m for m in messages if m not in blocked], 'collected': data.get('collected') or {},
                  'handoff': bool(data.get('handoff')), 'handoff_reason': str(data.get('handoff_reason') or ''), 'blocked': blocked,
                  'model': label}
        if blocked:
            result['handoff'] = True
            result['handoff_reason'] = ('Đã chặn tin có giá/STK/cam kết. ' + result['handoff_reason']).strip()
            if not result['messages']:
                result['messages'] = ['Dạ em chuyển bạn phụ trách hỗ trợ mình ngay ạ.']
        return result

    # ── Nút bấm ──────────────────────────────────────────────────────────────
    def action_activate(self):
        self.ensure_one()
        if self.state not in ('draft', 'archived'):
            raise UserError('Chỉ kích hoạt được bản nháp hoặc bản cũ.')
        if not (self.system_prompt or '').strip():
            raise UserError('System prompt đang trống.')
        self.search([('state', '=', 'active'), ('mode', '=', self.mode), ('id', '!=', self.id)]).write({'state': 'archived'})
        self.rule_ids.filtered(lambda r: r.state == 'proposed').action_approve()
        self.write({'state': 'active', 'activated_by_id': self.env.user.id, 'activated_at': fields.Datetime.now()})

    def action_archive_prompt(self):
        self.filtered(lambda p: p.state == 'active').write({'state': 'archived'})

    def action_test(self):
        """Mở màn hình Demo chatbot với bản prompt này."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'dac_chat_demo', 'name': 'Demo chatbot', 'params': {'prompt_id': self.id}}


class ChatPromptTest(models.TransientModel):
    """Thử chatbot với một bản prompt: KHÔNG gửi gì cho khách."""
    _name = 'dac.chat.prompt.test'
    _description = 'Thử chatbot'

    prompt_id = fields.Many2one('dac.chat.prompt', string='Bản prompt', required=True, ondelete='cascade')
    at = fields.Datetime(string='Giả lập lúc khách nhắn', required=True, default=fields.Datetime.now,
                         help='Đổi sang Chủ nhật/buổi tối để thử cách chatbot hẹn ngoài giờ.')
    customer_name = fields.Char(string='Tên khách', default='Chị Lan')
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
        result = self.prompt_id._reply(self._history(), self.at, self.customer_name)
        values = self.prompt_id._context_values(self.at, self.customer_name)
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
