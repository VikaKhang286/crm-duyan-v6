"""Quy tắc chat (playbook) cho chatbot chào khách: đề xuất từ thống kê/AI, quản lý duyệt mới có hiệu lực."""
import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CATEGORIES = [
    ('greeting', 'Chào hỏi'),
    ('info', 'Lấy thông tin'),
    ('style', 'Cách nhắn (số tin, độ dài, xưng hô)'),
    ('consult', 'Tư vấn sơ bộ'),
    ('after_hours', 'Ngoài giờ làm việc'),
    ('handoff', 'Chuyển nhân viên'),
    ('forbidden', 'Không được làm'),
]
CATEGORY_ORDER = [key for key, _label in CATEGORIES]
PRIORITIES = [('must', 'Bắt buộc'), ('should', 'Nên'), ('may', 'Tham khảo')]
MIN_GROUP = 20  # nhóm ít hơn số hội thoại này thì không tự đề xuất quy tắc từ thống kê
# Câu mẫu do AI viết không được chứa giá / số tài khoản / SĐT
MONEY_RE = re.compile(r'\d{1,3}(?:[.,]\d{3})+|\d+\s?(?:k|tr|triệu|nghìn|ngàn|đ|vnđ|vnd)\b', re.IGNORECASE)
PHONE_RE = re.compile(r'(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){9}(?!\d)')


def _clean(text):
    text = PHONE_RE.sub('[SĐT]', str(text or '')).strip()
    return MONEY_RE.sub('[giá]', text)


REVIEW_TASK = """Bạn là AGENT RÀ SOÁT QUY TẮC cho chatbot chào khách của DAC. Đọc danh sách quy tắc dưới đây và:
- merges: gộp các quy tắc cùng ý (dù khác chữ) thành MỘT quy tắc rõ ràng, đầy đủ ý nhất; liệt kê đủ rule_ids được gộp.
- duplicates: quy tắc CHỜ DUYỆT đã được một quy tắc khác (thường là quy tắc ĐÃ DUYỆT) bao trọn ý → duplicate_of là id quy tắc giữ lại.
- conflicts: các quy tắc mâu thuẫn nhau (làm theo cái này là trái cái kia).
Không gộp các quy tắc chỉ liên quan một phần. Không viết lại quy tắc an toàn đã duyệt. Mỗi quy tắc chỉ xuất hiện ở MỘT mục.
Quy tắc gộp phải giữ đúng phạm vi chatbot (không báo giá, không cam kết, không nhận cọc)."""

REVIEW_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string', 'description': '1–3 câu nhận xét bộ quy tắc.'},
        'merges': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'rule_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'title': {'type': 'string'}, 'rule': {'type': 'string'}, 'example': {'type': 'string'},
            'category': {'type': 'string', 'enum': CATEGORY_ORDER}, 'priority': {'type': 'string', 'enum': ['must', 'should', 'may']},
            'reason': {'type': 'string'}}, 'required': ['rule_ids', 'title', 'rule', 'category', 'priority']}},
        'duplicates': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'rule_id': {'type': 'integer'}, 'duplicate_of': {'type': 'integer'}, 'reason': {'type': 'string'}},
            'required': ['rule_id', 'duplicate_of']}},
        'conflicts': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'rule_ids': {'type': 'array', 'items': {'type': 'integer'}}, 'reason': {'type': 'string'}}, 'required': ['rule_ids', 'reason']}},
    },
    'required': ['summary'],
}


class ChatRule(models.Model):
    _name = 'dac.chat.rule'
    _description = 'Quy tắc chat cho chatbot (playbook)'
    _order = 'state_order, category_order, priority_order, sequence, id'

    name = fields.Char(string='Tiêu đề', required=True)
    sequence = fields.Integer(default=10)
    category = fields.Selection(CATEGORIES, string='Nhóm', required=True, default='greeting', index=True)
    content = fields.Text(string='Quy tắc', required=True)
    example = fields.Text(string='Câu mẫu')
    evidence = fields.Text(string='Bằng chứng / lý do')
    priority = fields.Selection(PRIORITIES, string='Mức', required=True, default='should')
    state = fields.Selection([('proposed', 'Đề xuất'), ('approved', 'Đã duyệt'), ('rejected', 'Bỏ')], string='Trạng thái',
        default='proposed', required=True, index=True, copy=False)
    source = fields.Selection([('system', 'Mặc định an toàn'), ('stats', 'Từ thống kê'), ('ai', 'AI đề xuất'), ('manual', 'Nhập tay')],
        string='Nguồn', default='manual', required=True, readonly=True)
    analysis_id = fields.Many2one('dac.chat.analysis', string='Đợt phân tích', ondelete='set null', index=True, readonly=True)
    approved_by_id = fields.Many2one('res.users', string='Người duyệt', readonly=True, copy=False)
    approved_at = fields.Datetime(string='Duyệt lúc', readonly=True, copy=False)
    active = fields.Boolean(default=True)
    review_note = fields.Text(string='Ghi chú rà soát (AI)', readonly=True, copy=False,
        help='Kết quả lượt AI rà soát trùng lặp / mâu thuẫn gần nhất.')
    merged_into_id = fields.Many2one('dac.chat.rule', string='Đã gộp vào', readonly=True, copy=False, ondelete='set null')
    # Cột sắp xếp (đã duyệt lên đầu, theo thứ tự nhóm, bắt buộc trước)
    state_order = fields.Integer(compute='_compute_order', store=True)
    category_order = fields.Integer(compute='_compute_order', store=True)
    priority_order = fields.Integer(compute='_compute_order', store=True)

    @api.depends('state', 'category', 'priority')
    def _compute_order(self):
        states = {'approved': 0, 'proposed': 1, 'rejected': 2}
        priorities = {'must': 0, 'should': 1, 'may': 2}
        for rule in self:
            rule.state_order = states.get(rule.state, 9)
            rule.category_order = CATEGORY_ORDER.index(rule.category) if rule.category in CATEGORY_ORDER else 99
            rule.priority_order = priorities.get(rule.priority, 9)

    def action_approve(self):
        self.write({'state': 'approved', 'approved_by_id': self.env.user.id, 'approved_at': fields.Datetime.now()})

    def action_reject(self):
        self.write({'state': 'rejected', 'approved_by_id': False, 'approved_at': False})

    def action_reset(self):
        self.write({'state': 'proposed', 'approved_by_id': False, 'approved_at': False})

    # ── Bản hướng dẫn cho chatbot ────────────────────────────────────────────
    @api.model
    def _playbook_text(self):
        """Văn bản quy tắc ĐÃ DUYỆT, gom theo nhóm, để đưa vào system prompt của chatbot."""
        rules = self.search([('state', '=', 'approved')])
        labels = dict(CATEGORIES)
        priority_labels = dict(PRIORITIES)
        blocks = []
        for category in CATEGORY_ORDER:
            group = rules.filtered(lambda r: r.category == category)
            if not group:
                continue
            lines = ['## %s' % labels[category]]
            for rule in group:
                lines.append('- [%s] %s: %s' % (priority_labels[rule.priority], rule.name, rule.content.strip()))
                if rule.example:
                    lines.append('  Câu mẫu: %s' % rule.example.strip())
            blocks.append('\n'.join(lines))
        return '\n\n'.join(blocks)

    def action_preview_playbook(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.playbook_preview_action')

    # ── Tạo đề xuất ──────────────────────────────────────────────────────────
    @api.model
    def _propose_from_ai(self, analysis, items):
        vals_list = []
        valid = dict(CATEGORIES)
        for item in items:
            if not isinstance(item, dict) or not item.get('title') or not item.get('rule'):
                continue
            vals_list.append({
                'name': _clean(item['title'])[:200], 'content': _clean(item['rule']), 'example': _clean(item.get('example')) or False,
                'evidence': _clean(item.get('evidence')) or False,
                'category': item.get('category') if item.get('category') in valid else 'consult',
                'priority': item.get('priority') if item.get('priority') in ('must', 'should', 'may') else 'should',
                'source': 'ai', 'state': 'proposed', 'analysis_id': analysis.id,
            })
        return self.create(vals_list)

    # ── Agent rà soát quy tắc ────────────────────────────────────────────────
    def action_ai_review(self):
        """Nút "AI rà soát trùng lặp": rà toàn bộ quy tắc đã duyệt + chờ duyệt (không phụ thuộc dòng đang chọn)."""
        result = self.env['dac.chat.rule']._ai_review()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'AI đã rà soát quy tắc', 'type': 'success', 'sticky': True, 'message': result['message'],
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    @api.model
    def _ai_review(self, analysis=None):
        """Một lượt AI rà toàn bộ quy tắc đã duyệt + chờ duyệt: gộp quy tắc trùng, bỏ quy tắc thừa, chỉ ra mâu thuẫn.
        Chỉ đổi quy tắc CHỜ DUYỆT; quy tắc đã duyệt không tự sửa (chỉ ghi gợi ý). Trả dict(message, merged, rejected, conflicts, usage)."""
        from .ai_agent import vision_call
        from .chat_analysis import AI_SYSTEM
        rules = self.search([('state', 'in', ('approved', 'proposed'))])
        if len(rules) < 2:
            return {'message': 'Chưa đủ quy tắc để rà soát.', 'merged': 0, 'rejected': 0, 'conflicts': 0, 'usage': {}}
        labels = dict(CATEGORIES)
        lines = '\n'.join('[id %s] [%s] [%s] [%s] %s: %s%s' % (
            r.id, 'ĐÃ DUYỆT' if r.state == 'approved' else 'chờ duyệt', labels[r.category], r.priority, r.name, r.content,
            (' | Câu mẫu: %s' % r.example) if r.example else '') for r in rules)
        text = REVIEW_TASK + '\n\nDANH SÁCH QUY TẮC:\n' + lines
        config = self.env['dac.chat.config']._get()
        model = (analysis.ai_model_id if analysis else False) or config.ai_model_id
        fallback = analysis.ai_fallback if analysis else config.ai_fallback
        ai = self.env['dac.chat.task']._resolve('review', model, fallback)
        data, label, _sent, usage = vision_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, text, [], REVIEW_SCHEMA, 'review_rules',
                                                'Rà soát trùng lặp và mâu thuẫn.', (10, 120))
        return self._apply_review(rules, data, label, usage)

    @api.model
    def _apply_review(self, rules, data, label='', usage=None):
        by_id = {r.id: r for r in rules}
        valid = dict(CATEGORIES)
        merged = rejected = 0
        for item in data.get('merges') or []:
            if not isinstance(item, dict) or not item.get('title') or not item.get('rule'):
                continue
            members = [by_id[i] for i in item.get('rule_ids') or [] if isinstance(i, int) and i in by_id]
            proposed = [r for r in members if r.state == 'proposed']
            approved = [r for r in members if r.state == 'approved']
            if len(members) < 2 or not proposed:
                continue
            note = 'Gộp từ: %s. %s' % (', '.join('#%s %s' % (r.id, r.name) for r in members), _clean(item.get('reason')))
            if approved:
                note += ' Quy tắc đang dùng %s trùng ý: duyệt quy tắc gộp này rồi bỏ quy tắc cũ.' % ', '.join('#%s' % r.id for r in approved)
            new = self.create({
                'name': _clean(item['title'])[:200], 'content': _clean(item['rule']), 'example': _clean(item.get('example')) or False,
                'evidence': '\n'.join(filter(None, (r.evidence for r in members))) or False,
                'category': item.get('category') if item.get('category') in valid else members[0].category,
                'priority': item.get('priority') if item.get('priority') in ('must', 'should', 'may') else 'should',
                'source': 'ai', 'state': 'proposed', 'analysis_id': members[0].analysis_id.id, 'review_note': note,
            })
            for rule in proposed:
                rule.write({'state': 'rejected', 'merged_into_id': new.id, 'review_note': 'Đã gộp vào #%s "%s".' % (new.id, new.name)})
                rejected += 1
            merged += 1
        for item in data.get('duplicates') or []:
            if not isinstance(item, dict):
                continue
            rule, keep = by_id.get(item.get('rule_id')), by_id.get(item.get('duplicate_of'))
            if not rule or not keep or rule == keep or rule.state != 'proposed' or rule.merged_into_id:
                continue
            rule.write({'state': 'rejected', 'merged_into_id': keep.id,
                        'review_note': 'Trùng với #%s "%s". %s' % (keep.id, keep.name, _clean(item.get('reason')))})
            rejected += 1
        conflicts = 0
        for item in data.get('conflicts') or []:
            members = [by_id[i] for i in (item or {}).get('rule_ids') or [] if isinstance(i, int) and i in by_id]
            if len(members) < 2:
                continue
            conflicts += 1
            for rule in members:
                others = ', '.join('#%s' % r.id for r in members if r != rule)
                rule.review_note = ((rule.review_note or '') + '\nMâu thuẫn với %s: %s' % (others, _clean(item.get('reason')))).strip()
        usage = usage or {}
        message = 'Gộp %s nhóm quy tắc trùng, bỏ %s quy tắc thừa, %s chỗ mâu thuẫn cần xem. %s' % (
            merged, rejected, conflicts, _clean(data.get('summary'))[:400])
        if usage.get('out'):
            message += ' (%s token vào / %s token ra)' % (usage.get('in', 0), usage['out'])
        _logger.info('Rà soát quy tắc bằng %s: %s', label, message)
        return {'message': message.strip(), 'merged': merged, 'rejected': rejected, 'conflicts': conflicts, 'usage': usage}

    @api.model
    def _propose_from_stats(self, analysis, report):
        """Quy tắc rút thẳng từ số liệu (không cần AI) khi đủ mẫu."""
        overview = report.get('overview') or {}
        effects = {e['feature']: {row['value']: row for row in e['rows']} for e in report.get('effects') or []}
        good = report.get('good_first_turn') or {}
        vals_list = []

        def add(category, priority, name, content, evidence, example=False):
            vals_list.append({'category': category, 'priority': priority, 'name': name, 'content': content, 'evidence': evidence,
                              'example': example, 'source': 'stats', 'state': 'proposed', 'analysis_id': analysis.id})

        if (good.get('conversations') or 0) >= MIN_GROUP and good.get('chars_median'):
            per_turn = max(1, round(good.get('messages_per_turn_median') or 1))
            add('style', 'should', 'Độ dài và số tin mỗi lượt',
                'Mỗi lượt gửi %s tin (tối đa 2–3 tin khi cần hỏi nhiều ý), mỗi tin khoảng %s ký tự, hạn chế tin dài quá %s ký tự.' % (
                    per_turn, int(good['chars_median']), int(good.get('chars_p75') or good['chars_median']) * 2),
                'Ở %s hội thoại tốt (chốt đơn hoặc có SĐT): lượt trả lời đầu trung vị %s tin, mỗi tin %s ký tự, 75%% tin ≤ %s ký tự.' % (
                    good['conversations'], good.get('messages_per_turn_median'), good['chars_median'], good.get('chars_p75')))

        speed = effects.get('Thời gian trả lời đầu (trong giờ)') or {}
        fast, slow = speed.get('≤ 5 phút'), speed.get('> 60 phút')
        if fast and slow and fast['n'] >= MIN_GROUP and slow['n'] >= MIN_GROUP and (fast['engaged_pct'] or 0) > (slow['engaged_pct'] or 0):
            add('greeting', 'must', 'Chào khách mới ngay',
                'Khách mới nhắn là chào lại ngay (trong vòng 1–2 phút), kể cả khi chưa trả lời được câu hỏi.',
                'Trả lời trong 5 phút: %s%% khách nhắn lại, %s%% chốt đơn (%s hội thoại). Trả lời sau 60 phút: %s%% nhắn lại, %s%% chốt (%s hội thoại).' % (
                    fast['engaged_pct'], fast.get('deal_pct'), fast['n'], slow['engaged_pct'], slow.get('deal_pct'), slow['n']))

        hours = effects.get('Khách nhắn lúc') or {}
        after, inside = hours.get('Ngoài giờ làm việc'), hours.get('Trong giờ làm việc')
        if after and after['n'] >= MIN_GROUP:
            add('after_hours', 'must', 'Khách nhắn ngoài giờ: giữ khách và hẹn mốc cụ thể',
                'Ngoài giờ làm việc: chào, xin thông tin cơ bản (sản phẩm, kích thước, số lượng, khu vực), rồi hẹn đúng mốc làm việc kế '
                'tiếp (tối thứ 2–thứ 6 → sáng mai; chiều thứ 7, Chủ nhật → sáng thứ 2).',
                '%s%% khách mới nhắn ngoài giờ; nhân viên trả lời sau trung vị %s phút; khách ngoài giờ nhắn lại %s%% so với %s%% trong giờ.' % (
                    overview.get('new_after_hours_pct'), overview.get('first_response_after_hours_median'), after['engaged_pct'],
                    (inside or {}).get('engaged_pct')),
                'Dạ em chào anh/chị ạ. Hôm nay bên em nghỉ, anh/chị cho em xin kích thước, số lượng và nội dung cần làm, sáng thứ 2 '
                'em kiểm tra rồi báo anh/chị liền nha.')

        asked = [i for i in report.get('info_questions') or [] if (i.get('asked_pct') or 0) >= 5]
        if asked:
            add('info', 'should', 'Thông tin cần xin và thứ tự hỏi',
                'Xin lần lượt: %s. Mỗi lượt hỏi 1–2 ý, khách đã nói thì không hỏi lại.' % ', '.join(
                    i['label'].lower() for i in sorted(asked, key=lambda i: (i.get('median_turn') or 99, -(i.get('asked_pct') or 0)))),
                'Tỉ lệ nhân viên có hỏi ở khách mới: %s.' % ', '.join('%s %s%%' % (i['label'], i['asked_pct']) for i in asked))

        media = effects.get('Lượt đầu có gửi ảnh/file') or {}
        yes, no = media.get('Có'), media.get('Không')
        if yes and no and yes['n'] >= MIN_GROUP and (yes.get('deal_pct') or 0) > (no.get('deal_pct') or 0):
            add('consult', 'may', 'Gửi ảnh mẫu khi khách hỏi sản phẩm',
                'Khi khách hỏi một loại sản phẩm, gửi kèm ảnh mẫu/công trình thực tế phù hợp (nếu có sẵn trong thư viện ảnh).',
                'Lượt đầu có ảnh/file: %s%% chốt (%s hội thoại) so với %s%% khi không có (%s hội thoại).' % (
                    yes.get('deal_pct'), yes['n'], no.get('deal_pct'), no['n']))
        return self.create(vals_list)


class PlaybookPreview(models.TransientModel):
    _name = 'dac.chat.playbook.preview'
    _description = 'Xem bản hướng dẫn chatbot'

    text = fields.Text(string='Bản hướng dẫn (chỉ gồm quy tắc đã duyệt)', readonly=True,
                       default=lambda self: self.env['dac.chat.rule']._playbook_text() or 'Chưa có quy tắc nào được duyệt.')
