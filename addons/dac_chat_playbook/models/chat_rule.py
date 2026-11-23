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


PROTECTED = ('forbidden', 'handoff')  # quy tắc code chèn nguyên văn vào prompt: AI tối ưu không tự sửa/bỏ, chỉ đề xuất

OPTIMIZE_TASK = """Bạn là AGENT TỐI ƯU BỘ QUY TẮC cho chatbot chào khách của DAC. Dưới đây là TOÀN BỘ quy tắc ĐÃ DUYỆT và CHỜ DUYỆT.
Hãy viết lại thành MỘT BỘ QUY TẮC GỌN, không trùng, không mâu thuẫn, để Agent viết system prompt và chatbot làm theo được:
- Gộp mọi quy tắc cùng ý hoặc cùng một chủ đề hẹp (vd: các quy tắc về độ dài tin; các quy tắc khi khách hỏi giá; các quy tắc ngoài
  giờ) thành MỘT quy tắc đầy đủ ý nhất.
- Mâu thuẫn: ĐỊNH HƯỚNG CỦA QUẢN LÝ ưu tiên cao nhất, rồi quy tắc ĐÃ DUYỆT, rồi quy tắc chờ duyệt. Ghi lý do chọn.
- dropped: quy tắc chatbot không làm được (vd: gửi ảnh, gọi điện), trái định hướng của quản lý, hoặc đã nằm trọn trong quy tắc khác.
- Mục tiêu: tổng cộng khoảng 12–18 quy tắc, mỗi nhóm 1–4 quy tắc; mỗi quy tắc 1–3 câu, cụ thể, làm theo được; câu mẫu ngắn, đúng giọng
  nhân viên DAC (xưng em, gọi anh/chị).
- Nhóm "Không được làm" và "Chuyển nhân viên": giữ ĐỦ mọi ràng buộc của các quy tắc gốc (được gộp câu chữ, không được bớt ý).
- Không đưa giá, số tiền, mốc giờ cố định (vd "trong 30 phút", "trước 11 giờ") vào quy tắc hay câu mẫu; hẹn khách thì nói "mốc làm việc
  kế tiếp" (code tự điền).
- MỖI id đầu vào phải xuất hiện ĐÚNG MỘT LẦN: trong source_ids của một quy tắc ở rules, hoặc trong dropped. Quy tắc gốc đã ổn thì vẫn
  đưa vào rules với source_ids chỉ gồm id đó và giữ nguyên câu chữ."""

OPTIMIZE_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string', 'description': '1–3 câu: đã gộp/bỏ gì, giải quyết mâu thuẫn nào.'},
        'rules': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'source_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'title': {'type': 'string'}, 'rule': {'type': 'string'}, 'example': {'type': 'string'},
            'category': {'type': 'string', 'enum': CATEGORY_ORDER}, 'priority': {'type': 'string', 'enum': ['must', 'should', 'may']},
            'reason': {'type': 'string'}}, 'required': ['source_ids', 'title', 'rule', 'category', 'priority']}},
        'dropped': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'rule_id': {'type': 'integer'}, 'reason': {'type': 'string'}}, 'required': ['rule_id', 'reason']}},
    },
    'required': ['summary', 'rules'],
}


def _same(a, b):
    return ' '.join(str(a or '').lower().split()) == ' '.join(str(b or '').lower().split())


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
    source = fields.Selection([('system', 'Mặc định an toàn'), ('stats', 'Từ thống kê'), ('ai', 'AI đề xuất'), ('optimized', 'AI tối ưu'),
                               ('manual', 'Nhập tay')],
        string='Nguồn', default='manual', required=True, readonly=True)
    analysis_id = fields.Many2one('dac.chat.analysis', string='Đợt phân tích', ondelete='set null', index=True, readonly=True)
    approved_by_id = fields.Many2one('res.users', string='Người duyệt', readonly=True, copy=False)
    approved_at = fields.Datetime(string='Duyệt lúc', readonly=True, copy=False)
    active = fields.Boolean(default=True)
    review_note = fields.Text(string='Ghi chú tối ưu (AI)', readonly=True, copy=False,
        help='Kết quả lượt AI tối ưu bộ quy tắc gần nhất.')
    merged_into_id = fields.Many2one('dac.chat.rule', string='Thay bằng', readonly=True, copy=False, ondelete='set null',
        help='Quy tắc mới (AI tối ưu) thay cho quy tắc này. Quy tắc đã duyệt thuộc nhóm an toàn chỉ được thay khi quản lý duyệt quy tắc mới.')
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
        # Quy tắc cũ đang chờ được thay bằng quy tắc vừa duyệt → thôi dùng
        for old in self.search([('merged_into_id', 'in', self.ids), ('state', '=', 'approved')]):
            old.write({'state': 'rejected', 'review_note': 'Đã thay bằng #%s "%s".' % (old.merged_into_id.id, old.merged_into_id.name)})

    def action_reject(self):
        self.write({'state': 'rejected', 'approved_by_id': False, 'approved_at': False})
        # Bỏ quy tắc AI tối ưu → các quy tắc cũ nó định thay vẫn dùng như trước
        self.search([('merged_into_id', 'in', self.ids), ('state', '=', 'approved')]).write({'merged_into_id': False, 'review_note': False})

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

    # ── Agent tối ưu bộ quy tắc ─────────────────────────────────────────────
    def action_ai_review(self):
        """Nút "AI tối ưu bộ quy tắc": xếp một lượt chạy nền (AI mất vài phút, quá giới hạn thời gian của trang web)."""
        log = self.env['dac.chat.rule.optimize'].create({'state': 'queued'})
        self.env.ref('dac_chat_playbook.cron_process_analysis').sudo()._trigger()  # sudo(): quản lý không có quyền ir.cron
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'AI đang tối ưu bộ quy tắc', 'type': 'info', 'sticky': False,
            'message': 'Chạy nền khoảng 2–5 phút, không cần giữ trang. Kết quả và nút hoàn tác ở "Lịch sử tối ưu quy tắc".',
            'next': {'type': 'ir.actions.act_window', 'res_model': log._name, 'res_id': log.id, 'views': [(False, 'form')],
                     'target': 'current'}}}

    @api.model
    def _active_rules(self):
        return self.search([('state', 'in', ('approved', 'proposed'))])

    @api.model
    def _ai_optimize(self, analysis=None, log=None):
        """Một lượt AI viết lại toàn bộ quy tắc đã duyệt + chờ duyệt thành bộ gọn. Áp dụng ngay (có lưu để hoàn tác).
        Trả dict(message, usage, log)."""
        from .ai_agent import vision_call
        from .chat_analysis import AI_SYSTEM
        Log = self.env['dac.chat.rule.optimize']
        log = log or Log.create({'state': 'queued', 'analysis_id': analysis.id if analysis else False})
        rules = self._active_rules()
        if len(rules) < 2:
            log.write({'state': 'done', 'summary': 'Chưa đủ quy tắc để tối ưu.', 'before_count': len(rules), 'after_count': len(rules)})
            return {'message': log.summary, 'usage': {}, 'log': log}
        labels = dict(CATEGORIES)
        lines = '\n'.join('[id %s] [%s] [%s] [%s] %s: %s%s' % (
            r.id, 'ĐÃ DUYỆT' if r.state == 'approved' else 'chờ duyệt', labels[r.category], r.priority, r.name, r.content,
            (' | Câu mẫu: %s' % r.example) if r.example else '') for r in rules)
        config = self.env['dac.chat.config']._get()
        text = '%s\n\nĐỊNH HƯỚNG CỦA QUẢN LÝ (ưu tiên cao nhất):\n%s\n\nDANH SÁCH QUY TẮC (%s):\n%s' % (
            OPTIMIZE_TASK, (config.agent_guidance or '').strip() or '(không có)', len(rules), lines)
        model = (analysis.ai_model_id if analysis else False) or config.ai_model_id
        fallback = analysis.ai_fallback if analysis else config.ai_fallback
        ai = self.env['dac.chat.task']._resolve('review', model, fallback)
        data, label, _sent, usage = vision_call(ai.env, ai.model, ai.fallback, AI_SYSTEM, text, [], OPTIMIZE_SCHEMA, 'optimize_rules',
                                                'Tối ưu bộ quy tắc.', (10, 300))
        return self._apply_optimize(rules, data, label, usage, log)

    @api.model
    def _apply_optimize(self, rules, data, label='', usage=None, log=None):
        """Áp dụng kết quả AI. An toàn:
        - Quy tắc mới thay cho toàn quy tắc đã duyệt (ngoài nhóm Cấm/Chuyển nhân viên) → duyệt ngay, quy tắc cũ thôi dùng.
        - Có dính quy tắc chờ duyệt, hoặc thuộc nhóm Cấm/Chuyển nhân viên → quy tắc mới chờ duyệt; quy tắc đã duyệt cũ VẪN dùng
          cho tới khi quản lý duyệt quy tắc mới (action_approve tự thay).
        - Không tự bỏ quy tắc đã duyệt thuộc nhóm Cấm/Chuyển nhân viên. Id AI không nhắc tới thì giữ nguyên.
        Mọi thay đổi được lưu ở log để hoàn tác."""
        log = log or self.env['dac.chat.rule.optimize'].create({'state': 'queued'})
        by_id = {r.id: r for r in rules}
        valid = dict(CATEGORIES)
        seen, changes = set(), []
        created = self.browse()
        auto = waiting = dropped = 0

        def remember(rule):
            changes.append({'id': rule.id, 'state': rule.state, 'merged_into_id': rule.merged_into_id.id or False,
                            'review_note': rule.review_note or False, 'approved_by_id': rule.approved_by_id.id or False,
                            'approved_at': fields.Datetime.to_string(rule.approved_at) if rule.approved_at else False})

        for item in data.get('rules') or []:
            if not isinstance(item, dict) or not item.get('title') or not item.get('rule'):
                continue
            members = [by_id[i] for i in item.get('source_ids') or [] if isinstance(i, int) and i in by_id and i not in seen]
            if not members:
                continue  # AI không được tự thêm quy tắc không có gốc
            seen.update(m.id for m in members)
            title, content = _clean(item['title'])[:200], _clean(item['rule'])
            if len(members) == 1 and _same(members[0].name, title) and _same(members[0].content, content):
                continue  # giữ nguyên
            category = item.get('category') if item.get('category') in valid else members[0].category
            safe = category not in PROTECTED and all(m.state == 'approved' and m.category not in PROTECTED for m in members)
            new = self.create({
                'name': title, 'content': content, 'example': _clean(item.get('example')) or False,
                'evidence': ('\n'.join(filter(None, (m.evidence for m in members))) or '')[:3000] or False,
                'category': category,
                'priority': item.get('priority') if item.get('priority') in ('must', 'should', 'may') else members[0].priority,
                'source': 'optimized', 'state': 'approved' if safe else 'proposed',
                'analysis_id': (log.analysis_id or members[0].analysis_id).id,
                'review_note': 'AI tối ưu, thay cho: %s. %s' % (', '.join('#%s %s' % (m.id, m.name) for m in members),
                                                                _clean(item.get('reason'))),
            })
            created |= new
            auto += safe
            waiting += not safe
            for member in members:
                remember(member)
                if safe or member.state == 'proposed':
                    member.write({'state': 'rejected', 'merged_into_id': new.id, 'review_note': 'Đã thay bằng #%s "%s" (AI tối ưu).' % (new.id, new.name)})
                else:
                    member.write({'merged_into_id': new.id, 'review_note': 'Vẫn đang dùng; sẽ thay bằng #%s "%s" khi quản lý duyệt quy tắc đó.' % (new.id, new.name)})
        for item in data.get('dropped') or []:
            rule = by_id.get((item or {}).get('rule_id')) if isinstance(item, dict) else None
            if not rule or rule.id in seen or (rule.state == 'approved' and rule.category in PROTECTED):
                continue
            seen.add(rule.id)
            remember(rule)
            rule.write({'state': 'rejected', 'review_note': 'AI tối ưu bỏ: %s' % (_clean(item.get('reason')) or 'thừa')})
            dropped += 1
        # Số quy tắc còn hiệu lực sau lượt này (quy tắc đã duyệt đang chờ được thay không tính)
        after = len([r for r in rules if r.state in ('approved', 'proposed') and not (r.state == 'approved' and r.merged_into_id)]) + len(created)
        usage = usage or {}
        message = 'Từ %s quy tắc còn %s: %s quy tắc mới áp dụng ngay, %s quy tắc mới chờ duyệt (nhóm an toàn hoặc từ đề xuất), bỏ %s. %s' % (
            len(rules), after, auto, waiting, dropped, _clean(data.get('summary'))[:500])
        if usage.get('out'):
            message += ' (%s token vào / %s token ra)' % (usage.get('in', 0), usage['out'])
        log.write({'state': 'done', 'summary': message.strip(), 'before_count': len(rules), 'after_count': after,
                   'created_ids': [(6, 0, created.ids)], 'changes_json': json.dumps(changes), 'model_label': label,
                   'tokens_in': usage.get('in', 0), 'tokens_out': usage.get('out', 0), 'error_message': False})
        _logger.info('Tối ưu quy tắc bằng %s: %s', label, message)
        return {'message': message.strip(), 'usage': usage, 'log': log}

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


class ChatRuleOptimize(models.Model):
    """Mỗi lượt AI tối ưu bộ quy tắc: kết quả + dữ liệu để hoàn tác."""
    _name = 'dac.chat.rule.optimize'
    _description = 'Lượt AI tối ưu quy tắc chat'
    _order = 'id desc'

    name = fields.Char(string='Lượt', compute='_compute_name')
    state = fields.Selection([('queued', 'AI đang tối ưu'), ('done', 'Đã áp dụng'), ('undone', 'Đã hoàn tác'), ('failed', 'Lỗi')],
                             string='Trạng thái', default='queued', required=True, readonly=True)
    analysis_id = fields.Many2one('dac.chat.analysis', string='Đợt phân tích', readonly=True, ondelete='set null')
    summary = fields.Text(string='Kết quả', readonly=True)
    before_count = fields.Integer(string='Trước', readonly=True)
    after_count = fields.Integer(string='Sau', readonly=True)
    created_ids = fields.Many2many('dac.chat.rule', 'dac_chat_rule_optimize_rel', 'optimize_id', 'rule_id', string='Quy tắc mới',
                                   readonly=True, context={'active_test': False})
    changes_json = fields.Text(readonly=True)
    model_label = fields.Char(string='Model', readonly=True)
    tokens_in = fields.Integer(string='Token vào', readonly=True)
    tokens_out = fields.Integer(string='Token ra', readonly=True)
    error_message = fields.Text(string='Lỗi', readonly=True)
    can_undo = fields.Boolean(compute='_compute_can_undo')

    def _compute_name(self):
        for log in self:
            moment = fields.Datetime.context_timestamp(log.with_context(tz='Asia/Ho_Chi_Minh'), log.create_date or fields.Datetime.now())
            log.name = 'Tối ưu %s' % moment.strftime('%d/%m/%Y %H:%M')

    def _compute_can_undo(self):
        for log in self:
            log.can_undo = log._undoable()

    def _undoable(self):
        self.ensure_one()
        return self.state == 'done' and self == self.search([('state', '=', 'done')], limit=1) and bool(self.changes_json or self.created_ids)

    @api.model
    def _cron_run(self):
        """Chạy các lượt đang chờ (bấm nút). Không raise."""
        for log in self.sudo().search([('state', '=', 'queued')], order='id'):
            try:
                self.env['dac.chat.rule'].sudo()._ai_optimize(log.analysis_id or None, log)
                self.env.cr.commit()
            except Exception as error:  # noqa: BLE001 — cron không được raise
                self.env.cr.rollback()
                _logger.exception('Tối ưu quy tắc %s lỗi', log.id)
                log.write({'state': 'failed', 'error_message': str(error)[:500]})
                self.env.cr.commit()

    def action_undo(self):
        """Trả mọi quy tắc về như trước lượt này; quy tắc mới do lượt này tạo thì ẩn đi. Chỉ hoàn tác được lượt gần nhất."""
        self.ensure_one()
        if not self._undoable():
            raise UserError('Chỉ hoàn tác được lượt tối ưu gần nhất đã áp dụng.')
        Rule = self.env['dac.chat.rule']
        for change in json.loads(self.changes_json or '[]'):
            rule = Rule.browse(change['id']).exists()
            if rule:
                rule.write({key: change.get(key) or False for key in ('state', 'merged_into_id', 'review_note', 'approved_by_id', 'approved_at')})
        self.created_ids.write({'state': 'rejected', 'active': False})
        self.write({'state': 'undone'})
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_open_rules(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_rule_action')
