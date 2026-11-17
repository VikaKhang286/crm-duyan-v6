from odoo import api, fields, models


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

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        messages.mapped('order_id')._notify_design_dashboard_changed()
        return messages

    def write(self, vals):
        orders = self.mapped('order_id')
        result = super().write(vals)
        (orders | self.mapped('order_id'))._notify_design_dashboard_changed()
        return result

    def unlink(self):
        orders = self.mapped('order_id')
        result = super().unlink()
        orders._notify_design_dashboard_changed()
        return result
