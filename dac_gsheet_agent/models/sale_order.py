from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_open_gsheet_agent(self):
        """Mở khung chat AI với các đơn đang chọn làm ngữ cảnh."""
        return {
            'type': 'ir.actions.client',
            'tag': 'dac_gsheet_agent.chat',
            'name': _('Trợ lý AI Xe đẩy'),
            'context': {'default_order_ids': self.filtered(lambda o: o.order_type == 'cart').ids},
        }
