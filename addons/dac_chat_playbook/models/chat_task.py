"""Model AI và mức thinking cho TỪNG tác vụ trong workflow chatbot (một màn hình duy nhất để chỉnh).

Mỗi tác vụ (đọc hội thoại, Agent điều phối, rà soát quy tắc, viết prompt, chatbot trả lời ở 3 bộ) có một dòng:
model, "dùng thứ tự chung khi lỗi" và mức thinking. Thứ tự ưu tiên khi gọi AI cho một tác vụ:

1. Model chọn ở dòng tác vụ (nếu có).
2. Model do nơi gọi truyền vào (ghi đè riêng của một đợt phân tích / một bản prompt — cách cũ, vẫn hoạt động).
3. Model mặc định ở Thiết lập phân tích chat.
4. Trống → thứ tự kết nối chung (Cài đặt → Kết nối AI dùng chung).

Mức thinking chỉ gửi đi khi khác "Mặc định" và chỉ với kết nối kiểu OpenAI (9router…) dưới dạng `reasoning_effort`;
kết nối Gemini bỏ qua (xem `ai_connection_effort.py`).
"""
import time
from collections import namedtuple

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ai_agent import vision_call

THINKING = [
    ('auto', 'Mặc định của model'),
    ('none', 'Tắt thinking'),
    ('low', 'Thấp'),
    ('medium', 'Vừa'),
    ('high', 'Cao'),
    ('xhigh', 'Rất cao'),
    ('max', 'Tối đa'),
]
EFFORTS = tuple(key for key, _label in THINKING if key != 'auto')

# (mã, tên, mô tả, giai đoạn, gợi ý)
TASKS = [
    ('read', 'Đọc & gán nhãn hội thoại',
     'AI đọc từng lô hội thoại (kèm ảnh) và gán nhãn: sản phẩm, kết quả, lý do mất khách, điểm nhân viên. Bước gọi AI nhiều nhất.',
     '1. Phân tích', 'Thấp hoặc mặc định: gọi hàng chục lô, cần nhanh và rẻ.'),
    ('coordinate', 'Agent điều phối & tổng hợp',
     'Agent tra cứu nhãn, thống kê, đọc lại hội thoại qua nhiều vòng rồi viết kết luận và đề xuất quy tắc.',
     '1. Phân tích', 'Vừa đến cao: cần suy luận nhiều bước.'),
    ('review', 'Rà soát quy tắc',
     'Gộp quy tắc trùng lặp, loại quy tắc thừa, chỉ ra quy tắc mâu thuẫn.',
     '2. Quy tắc', 'Vừa: so sánh nhiều quy tắc với nhau.'),
    ('write_prompt', 'Viết system prompt',
     'Agent viết 3 bộ prompt (Chào hỏi / Hỏi thông tin / Tư vấn) từ kết quả phân tích, quy tắc và kiến thức doanh nghiệp.',
     '3. Prompt', 'Cao: chạy hiếm, chất lượng quan trọng.'),
    ('improve_prompt', 'Cải tiến prompt từ góp ý',
     'Màn hình Huấn luyện & kiểm thử: Agent viết lại system prompt theo góp ý của admin cho từng lượt trả lời.',
     '3. Prompt', 'Tối đa: chạy khi admin bấm "Cải tiến", chất lượng quan trọng hơn tốc độ.'),
    ('bot_greeting', 'Chatbot trả lời — Chào hỏi',
     'Trả lời khách mới nhắn lần đầu: chào, xin thông tin cơ bản, hẹn mốc làm việc. Khách đang chờ nên cần nhanh.',
     '4. Chatbot', 'Thấp hoặc tắt: ưu tiên tốc độ và chi phí.'),
    ('bot_info', 'Chatbot trả lời — Hỏi thông tin',
     'Hỏi và ghi nhận thông tin khách cần cho báo giá (kích thước, vật liệu, số lượng…).',
     '4. Chatbot', 'Thấp đến vừa.'),
    ('bot_consult', 'Chatbot trả lời — Tư vấn',
     'Tư vấn sơ bộ theo kiến thức doanh nghiệp, không báo giá, chuyển nhân viên khi cần.',
     '4. Chatbot', 'Vừa: cần bám đúng kiến thức và quy tắc.'),
]
# Giá trị mặc định khi tạo dòng lần đầu (không ghi đè lựa chọn của quản lý): model tìm theo tên, mức thinking
TASK_DEFAULTS = {'improve_prompt': {'model_like': 'claude-haiku', 'thinking': 'max'}}
TASK_BY_CODE = {code: (name, desc, stage, hint) for code, name, desc, stage, hint in TASKS}

TaskAI = namedtuple('TaskAI', 'env model fallback effort')


class ChatTask(models.Model):
    _name = 'dac.chat.task'
    _description = 'Model & thinking theo tác vụ AI'
    _order = 'sequence, id'
    _sql_constraints = [('code_unique', 'unique(code)', 'Mỗi tác vụ chỉ có một dòng thiết lập.')]

    code = fields.Selection([(c, n) for c, n, *_rest in TASKS], string='Mã tác vụ', required=True, readonly=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Tác vụ', readonly=True)
    stage = fields.Char(string='Giai đoạn', readonly=True)
    description = fields.Text(string='Làm gì', readonly=True)
    hint = fields.Char(string='Gợi ý thinking', readonly=True)
    ai_model_id = fields.Many2one('dac.ai.model', string='Model', ondelete='set null', domain="[('connection_id.active', '=', True)]",
        help='Để trống = dùng model mặc định ở Thiết lập, rồi đến thứ tự kết nối chung.')
    ai_fallback = fields.Boolean(string='Lỗi thì dùng thứ tự chung', default=True,
        help='Model đã chọn lỗi hoặc quá tải thì thử các model khác trong Kết nối AI dùng chung. Tắt = báo lỗi ngay.')
    thinking = fields.Selection(THINKING, string='Thinking', default='auto', required=True,
        help='Mức suy nghĩ của model. Chỉ gửi đi khi khác "Mặc định" và chỉ với kết nối kiểu OpenAI (9router…). '
             'Mức không hợp lệ cho model đó sẽ do 9router tự điều chỉnh hoặc bỏ qua.')
    thinking_note = fields.Char(string='Lưu ý', compute='_compute_thinking_note')

    @api.depends('thinking', 'ai_model_id.connection_id.kind')
    def _compute_thinking_note(self):
        for task in self:
            kind = task.ai_model_id.connection_id.sudo().kind  # sudo(): chỉ để đọc loại kết nối, không lộ key
            if task.thinking == 'auto':
                task.thinking_note = 'Không gửi tham số thinking.'
            elif kind == 'gemini':
                task.thinking_note = 'Kết nối Gemini: bỏ qua mức thinking.'
            else:
                task.thinking_note = 'Gửi reasoning_effort=%s (kết nối OpenAI/9router).' % task.thinking

    # ── Thiết lập ────────────────────────────────────────────────────────────
    @api.model
    def _ensure_all(self):
        """Đủ một dòng cho mỗi tác vụ; làm mới tên/mô tả (giữ nguyên lựa chọn của quản lý)."""
        # sudo(): dòng thiết lập dùng chung, đọc/tạo được cả trong cron và khi quản lý (không có quyền tạo) mở màn hình
        rows = {row.code: row for row in self.sudo().search([])}
        for index, (code, name, desc, stage, hint) in enumerate(TASKS):
            values = {'name': name, 'description': desc, 'stage': stage, 'hint': hint, 'sequence': (index + 1) * 10}
            if code in rows:
                row = rows[code]
                if any(row[key] != value for key, value in values.items()):
                    row.write(values)
            else:
                defaults = TASK_DEFAULTS.get(code, {})
                if defaults.get('thinking'):
                    values['thinking'] = defaults['thinking']
                if defaults.get('model_like'):
                    model = self.env['dac.ai.model'].sudo().search([('name', 'ilike', defaults['model_like']), ('selected', '=', True),
                                                                    ('connection_id.active', '=', True)], limit=1)
                    values['ai_model_id'] = model.id or False
                rows[code] = self.sudo().create(dict(values, code=code))
        return self.sudo().search([])

    @api.model
    def _get(self, code):
        if code not in TASK_BY_CODE:
            raise UserError('Tác vụ AI không có trong danh sách: %s' % code)
        # sudo(): xem _ensure_all
        task = self.sudo().search([('code', '=', code)], limit=1)
        return task or self._ensure_all().filtered(lambda t: t.code == code)

    @api.model
    def _resolve(self, code, model=None, fallback=None, use_config_model=True):
        """Model, "thử thứ tự chung khi lỗi" và mức thinking để gọi AI cho tác vụ `code` → TaskAI(env, model, fallback, effort).

        `model`/`fallback`: ghi đè riêng của nơi gọi (một đợt phân tích, một bản prompt). Dòng tác vụ có model thì thắng ghi đè đó.
        `use_config_model`: có lấy "Model mặc định" ở Thiết lập khi không ai chọn không (chatbot trả lời khách: không, vì đó là
        model của khâu phân tích).
        Truyền `env` và `model` trả về cho `ai_call` / `vision_call` / `AgentLoop`: mức thinking đi theo context `dac_ai_effort`."""
        task = self._get(code)
        config = self.env['dac.chat.config']._get()
        if task.ai_model_id:
            chosen, use_fallback = task.ai_model_id, task.ai_fallback
        else:
            chosen = model or (config.ai_model_id if use_config_model else self.env['dac.ai.model'])
            use_fallback = config.ai_fallback if fallback is None else fallback
        effort = task.thinking if task.thinking in EFFORTS else False
        env = self.env(context=dict(self.env.context, dac_ai_effort=effort)) if effort else self.env
        if chosen and effort:
            chosen = chosen.with_context(dac_ai_effort=effort)
        return TaskAI(env, chosen or self.env['dac.ai.model'], bool(use_fallback), effort)

    @api.model
    def _any_model(self):
        """Có dòng tác vụ nào đã chọn model không (để biết "có AI dùng được" dù chưa có thứ tự chung)."""
        return bool(self.sudo().search_count([('ai_model_id', '!=', False)]))

    # ── Giao diện ────────────────────────────────────────────────────────────
    @api.model
    def action_open(self):
        self._ensure_all()
        return {'type': 'ir.actions.act_window', 'name': 'Model & thinking theo tác vụ', 'res_model': self._name,
                'view_mode': 'list,form', 'target': 'current', 'context': {'create': False}}

    def action_test(self):
        """Gọi thử một lượt nhỏ đúng model + thinking của tác vụ này; báo model đã trả lời, thời gian và token."""
        self.ensure_one()
        ai = self._resolve(self.code, use_config_model=not self.code.startswith('bot_'))
        schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
        started = time.monotonic()
        try:
            _data, label, _sent, usage = vision_call(
                ai.env, ai.model, ai.fallback, 'Bạn là bộ kiểm tra kết nối. Luôn trả kết quả bằng cách gọi tool.',
                'Trả lời đúng một từ: OK', [], schema, 'report_test', 'Báo kết quả kiểm tra.', (10, 60))
        except UserError as error:
            raise UserError('Thử tác vụ "%s" không được: %s' % (self.name, error)) from None
        message = '%s · thinking: %s · %.1f giây · %s token vào / %s token ra' % (
            label, dict(THINKING)[ai.effort or 'auto'], time.monotonic() - started, usage.get('in', '?'), usage.get('out', '?'))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Thử "%s" thành công' % self.name, 'message': message, 'type': 'success', 'sticky': False}}
