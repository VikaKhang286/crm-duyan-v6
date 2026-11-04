from odoo import fields, models, _
from odoo.exceptions import AccessError, ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    pancake_export_reference = fields.Char(string='Mã đối chiếu xuất POS', copy=False, readonly=True, index=True)
    pancake_export_shop_id = fields.Char(copy=False, readonly=True, groups='base.group_system')
    pancake_connector_log_ids = fields.One2many('dac.pancake.api.log', 'order_id', readonly=True)

    _sql_constraints = [('pancake_export_reference_unique', 'unique(pancake_export_reference)', 'Mã xuất POS phải duy nhất.')]

    def _get_pancake_api_key(self):
        company = self.env.company.sudo()
        if company.pancake_pos_api_key:
            return company.pancake_pos_api_key
        if company == self.env.ref('base.main_company'):
            return super()._get_pancake_api_key()
        raise AccessError(_('Công ty này chưa có cấu hình POS riêng.'))

    def _get_pancake_shop_id(self):
        company = self.env.company.sudo()
        if company.pancake_pos_shop_id:
            return company.pancake_pos_shop_id
        if company == self.env.ref('base.main_company'):
            return super()._get_pancake_shop_id()
        raise AccessError(_('Công ty này chưa có Shop ID POS riêng.'))

    def _pancake_exported_order(self, data):
        reference = data.get('custom_id')
        if not isinstance(reference, str) or not reference.startswith('ODOO-'):
            return self.browse()
        order = self.sudo().search([('pancake_export_reference', '=', reference)], limit=1)
        if not order:
            return order
        # A callback without the configured shop cannot claim a CRM export.
        if str(data.get('shop_id') or '') != order.pancake_export_shop_id:
            raise ValidationError(_('Mã đối chiếu đơn CRM không khớp shop POS của webhook.'))
        remote_id = str(data.get('id') or '')
        if not remote_id.isascii() or not remote_id.isdigit():
            raise ValidationError(_('Webhook thiếu mã đơn POS hợp lệ.'))
        if order.pancake_order_id and order.pancake_order_id != remote_id:
            return order  # Never replace an existing link with a second order.
        order.write({'pancake_order_id': remote_id, 'pancake_system_id': str(data.get('system_id') or '')})
        return order

    def action_sync_single_pancake_order(self, order_data):
        exported = self._pancake_exported_order(order_data)
        if exported:
            return exported
        return super().action_sync_single_pancake_order(order_data)
