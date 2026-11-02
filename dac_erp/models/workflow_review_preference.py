from odoo import fields, models


class DacWorkflowReviewPreference(models.Model):
    """Per-user UI state for inspecting an order's completed workflow steps."""

    _name = 'dac.workflow.review.preference'
    _description = 'DAC Workflow Review Preference'
    _rec_name = 'order_id'

    order_id = fields.Many2one(
        'sale.order', required=True, index=True, ondelete='cascade',
    )
    user_id = fields.Many2one(
        'res.users', required=True, index=True, default=lambda self: self.env.user,
        ondelete='cascade',
    )
    review_state = fields.Selection([
        ('quotation', 'Báo giá'),
        ('deposit', 'Thiết kế / Cọc'),
        ('production', 'Sản xuất'),
        ('installation', 'Thi công - lắp đặt'),
        ('delivery', 'Giao hàng'),
        ('completed', 'Hoàn thành'),
    ], required=True)

    _sql_constraints = [
        (
            'order_user_unique',
            'unique(order_id, user_id)',
            'Mỗi người dùng chỉ có một bước đang xem trên mỗi đơn hàng.',
        ),
    ]
