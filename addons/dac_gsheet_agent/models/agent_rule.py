"""Playbook quy tắc agent tự học — theo mô hình ACE (Agentic Context Engineering,
ICLR 2026, github.com/ace-agent/ace):

* Generator  = agent chat, đọc playbook trong system prompt, trích dẫn [R<id>].
* Reflector  = LLM xem lại phiên làm việc + kết quả (đề xuất được duyệt / bị từ
               chối kèm lý do) để rút ra bài học.
* Curator    = code tất định áp các "delta" (ADD / HELPFUL / HARMFUL / UPDATE)
               vào playbook: chống trùng, đếm helpful/harmful, tự lưu trữ quy tắc
               có hại. Không bao giờ viết lại toàn bộ playbook (tránh "context
               collapse" mà paper ACE mô tả).
"""
import difflib
import logging

from odoo import _, api, fields, models

from .gsheet_source import norm_text

_logger = logging.getLogger(__name__)

CATEGORIES = [
    ('matching', 'So khớp dữ liệu'),
    ('status_mapping', 'Ánh xạ trạng thái / tiến độ'),
    ('sheet_structure', 'Cấu trúc file sheet'),
    ('data_format', 'Định dạng dữ liệu'),
    ('workflow', 'Quy trình làm việc'),
    ('pitfall', 'Lỗi cần tránh'),
    ('preference', 'Ưu tiên của người dùng'),
]
DUPLICATE_RATIO = 0.85
AUTO_ARCHIVE_NET_HARM = 3
PLAYBOOK_MAX_RULES = 60


class DacGsheetAgentRule(models.Model):
    _name = 'dac.gsheet.agent.rule'
    _description = 'Quy tắc AI Agent tự học (playbook)'
    _order = 'state, score desc, id desc'

    name = fields.Char(compute='_compute_name', store=True, string='Tóm tắt')
    content = fields.Text(string='Nội dung quy tắc', required=True)
    category = fields.Selection(CATEGORIES, string='Nhóm', default='workflow', required=True)
    source_id = fields.Many2one(
        'dac.gsheet.source', string='Áp dụng cho file', ondelete='cascade',
        help='Trống: áp dụng cho mọi file.')
    state = fields.Selection([
        ('proposed', 'Chờ duyệt'), ('active', 'Đang dùng'), ('archived', 'Đã bỏ'),
    ], default='active', required=True, index=True, string='Trạng thái')
    origin = fields.Selection([
        ('user', 'Người dùng dạy'), ('agent', 'Agent tự ghi'), ('reflection', 'Rút kinh nghiệm'),
    ], default='agent', string='Nguồn gốc')
    helpful_count = fields.Integer(string='Hữu ích', default=0)
    harmful_count = fields.Integer(string='Có hại', default=0)
    score = fields.Integer(compute='_compute_score', store=True)
    session_id = fields.Many2one('dac.gsheet.agent.session', string='Phiên tạo ra', ondelete='set null')
    last_used_at = fields.Datetime(string='Lần dùng gần nhất')
    history = fields.Text(string='Lịch sử chỉnh sửa', readonly=True)

    @api.depends('content')
    def _compute_name(self):
        for rec in self:
            text = (rec.content or '').strip().splitlines()[0] if rec.content else ''
            rec.name = text[:80] + ('…' if len(text) > 80 else '')

    @api.depends('helpful_count', 'harmful_count')
    def _compute_score(self):
        for rec in self:
            rec.score = rec.helpful_count - rec.harmful_count

    def action_activate(self):
        self.write({'state': 'active'})

    def action_archive_rule(self):
        self.write({'state': 'archived'})

    # ------------------------------------------------------------------
    # Generator side: playbook đưa vào system prompt
    # ------------------------------------------------------------------
    @api.model
    def get_playbook(self, sources):
        domain = [('state', '=', 'active'), '|', ('source_id', '=', False), ('source_id', 'in', sources.ids)]
        rules = self.search(domain, order='score desc, id desc', limit=PLAYBOOK_MAX_RULES)
        return rules

    @api.model
    def render_playbook(self, rules):
        if not rules:
            return '(Chưa có quy tắc nào — hãy học từ người dùng.)'
        labels = dict(CATEGORIES)
        lines = []
        for cat, label in CATEGORIES:
            group = rules.filtered(lambda r: r.category == cat)
            if not group:
                continue
            lines.append('## %s' % label)
            for r in group:
                scope = (' [file: %s]' % r.source_id.name) if r.source_id else ''
                lines.append('- [R%s] (+%s/-%s)%s %s' % (r.id, r.helpful_count, r.harmful_count, scope, r.content.strip()))
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # Curator: áp delta tất định
    # ------------------------------------------------------------------
    @api.model
    def _find_duplicate(self, content, source_id):
        key = norm_text(content)
        domain = [('state', 'in', ('active', 'proposed')), ('source_id', '=', source_id or False)]
        for rule in self.search(domain):
            if difflib.SequenceMatcher(None, key, norm_text(rule.content)).ratio() >= DUPLICATE_RATIO:
                return rule
        return self.browse()

    @api.model
    def add_rule(self, content, category='workflow', source_id=False, origin='agent', session=None):
        """Thêm quy tắc; nếu trùng ý với quy tắc cũ thì tăng helpful thay vì tạo mới."""
        content = (content or '').strip()
        if len(content) < 8:
            return self.browse(), 'Nội dung quy tắc quá ngắn.'
        if category not in dict(CATEGORIES):
            category = 'workflow'
        duplicate = self._find_duplicate(content, source_id)
        if duplicate:
            duplicate.helpful_count += 1
            return duplicate, 'Đã có quy tắc tương tự [R%s] — tăng điểm hữu ích.' % duplicate.id
        require_review = self.env['dac.gsheet.source']._get_param('rule_require_review') in ('True', '1')
        # Người dùng dạy trực tiếp thì luôn dùng ngay.
        state = 'proposed' if (require_review and origin != 'user') else 'active'
        rule = self.create({
            'content': content, 'category': category, 'source_id': source_id or False,
            'origin': origin, 'state': state, 'session_id': session.id if session else False,
        })
        return rule, 'Đã lưu quy tắc [R%s] (%s).' % (rule.id, dict(rule._fields['state'].selection)[state])

    def mark(self, helpful=True):
        for rule in self:
            if helpful:
                rule.write({'helpful_count': rule.helpful_count + 1, 'last_used_at': fields.Datetime.now()})
            else:
                rule.harmful_count += 1
                if rule.harmful_count - rule.helpful_count >= AUTO_ARCHIVE_NET_HARM and rule.origin != 'user':
                    rule.state = 'archived'

    def update_content(self, new_content):
        self.ensure_one()
        stamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M')
        self.write({
            'history': '%s[%s] %s\n' % (self.history or '', stamp, self.content),
            'content': new_content.strip(),
        })

    @api.model
    def apply_operations(self, operations, session=None, default_source_id=False):
        """Áp danh sách delta từ Reflector. Trả về list mô tả thay đổi."""
        log = []
        for op in (operations or [])[:12]:
            if not isinstance(op, dict):
                continue
            kind = str(op.get('op', '')).upper()
            rule = self.browse()
            if op.get('rule_id'):
                try:
                    rule = self.browse(int(op['rule_id'])).exists()
                except (TypeError, ValueError):
                    rule = self.browse()
            if kind == 'ADD':
                source_id = default_source_id if op.get('scope') == 'source' else False
                _rule, msg = self.add_rule(op.get('content'), op.get('category') or 'workflow',
                                           source_id=source_id, origin='reflection', session=session)
                log.append(msg)
            elif kind == 'HELPFUL' and rule:
                rule.mark(True)
                log.append('[R%s] +1 hữu ích' % rule.id)
            elif kind == 'HARMFUL' and rule:
                rule.mark(False)
                log.append('[R%s] +1 có hại%s' % (rule.id, ' → đã bỏ' if rule.state == 'archived' else ''))
            elif kind == 'UPDATE' and rule and op.get('content') and rule.origin != 'user':
                rule.update_content(op['content'])
                log.append('[R%s] đã chỉnh nội dung' % rule.id)
        return log
