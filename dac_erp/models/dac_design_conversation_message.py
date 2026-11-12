from odoo import fields, models


class DacDesignConversationMessage(models.Model):
    """Dedicated Sale/Manager ↔ Design conversation, separate from chatter."""

    _name = 'dac.design.conversation.message'
    _description = 'DAC Design Conversation Message'
    _order = 'create_date asc, id asc'

    order_id = fields.Many2one(
        'sale.order', required=True, index=True, ondelete='cascade', string='Đơn hàng',
    )
    author_id = fields.Many2one(
        'res.partner', required=True, default=lambda self: self.env.user.partner_id,
        readonly=True, string='Người gửi',
    )
    body = fields.Text(string='Nội dung')
    attachment_ids = fields.Many2many(
        'ir.attachment', 'dac_design_conversation_message_attachment_rel',
        'message_id', 'attachment_id', string='Tệp đính kèm',
    )
