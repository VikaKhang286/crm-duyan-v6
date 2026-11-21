"""Kiến thức doanh nghiệp cho chatbot: công ty, liên hệ, sản phẩm/dịch vụ, quy trình, chính sách, câu hỏi thường gặp.

Quản lý sửa trực tiếp. Mỗi mục có phạm vi dùng theo bộ prompt (Chào hỏi / Hỏi thông tin / Tư vấn). Khi chatbot trả lời, code chèn các
mục phù hợp vào system prompt. Không ghi giá ở đây: chatbot không được báo giá, bộ lọc cũng sẽ chặn tin có giá."""
from odoo import api, fields, models

KNOWLEDGE_CATEGORIES = [
    ('company', 'Công ty'),
    ('contact', 'Liên hệ & giờ làm việc'),
    ('product', 'Sản phẩm / dịch vụ'),
    ('process', 'Quy trình & khu vực'),
    ('policy', 'Chính sách'),
    ('faq', 'Câu hỏi thường gặp'),
]
SCOPES = [
    ('all', 'Mọi bộ'),
    ('info', 'Hỏi thông tin + Tư vấn'),
    ('consult', 'Chỉ Tư vấn'),
]
SCOPES_FOR_MODE = {'greeting': ('all',), 'info': ('all', 'info'), 'consult': ('all', 'info', 'consult')}


class ChatKnowledge(models.Model):
    _name = 'dac.chat.knowledge'
    _description = 'Kiến thức doanh nghiệp cho chatbot'
    _order = 'category_order, sequence, id'

    name = fields.Char(string='Tiêu đề', required=True)
    category = fields.Selection(KNOWLEDGE_CATEGORIES, string='Nhóm', required=True, default='product', index=True)
    category_order = fields.Integer(compute='_compute_category_order', store=True)
    content = fields.Text(string='Nội dung', required=True,
        help='Viết ngắn gọn, đúng sự thật. Không ghi giá. Thời gian/bảo hành chỉ là thông tin chung, nhân viên xác nhận cụ thể.')
    scope = fields.Selection(SCOPES, string='Dùng cho', required=True, default='consult',
        help='Mọi bộ: cả Chào hỏi (thông tin chung như địa chỉ, giờ làm). Hỏi thông tin + Tư vấn: trả lời nhanh câu hỏi cơ bản. '
             'Chỉ Tư vấn: chi tiết sản phẩm, chính sách, câu hỏi thường gặp.')
    source_url = fields.Char(string='Nguồn')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    char_count = fields.Integer(string='Số ký tự', compute='_compute_char_count')

    @api.depends('category')
    def _compute_category_order(self):
        order = [key for key, _label in KNOWLEDGE_CATEGORIES]
        for item in self:
            item.category_order = order.index(item.category) if item.category in order else 99

    @api.depends('content')
    def _compute_char_count(self):
        for item in self:
            item.char_count = len(item.content or '')

    @api.model
    def _text_for_mode(self, mode):
        """Văn bản kiến thức cho một bộ prompt (gom theo nhóm)."""
        # sudo(): chatbot chạy nền (cron/webhook) vẫn cần đọc kiến thức
        items = self.sudo().search([('scope', 'in', SCOPES_FOR_MODE.get(mode, ('all',)))])
        labels = dict(KNOWLEDGE_CATEGORIES)
        blocks = []
        for category, _label in KNOWLEDGE_CATEGORIES:
            group = items.filtered(lambda i: i.category == category)
            if group:
                blocks.append('## %s\n%s' % (labels[category], '\n'.join('- %s: %s' % (i.name, ' '.join(i.content.split())) for i in group)))
        return '\n\n'.join(blocks)
