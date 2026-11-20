from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for event in env['dac.meta.conversion.event'].search([('delivery_provider', '=', 'pancake_pos'),
            ('pancake_connection_id', '=', False)]):
        connection = event.company_id.pancake_pos_connection_id
        if connection and connection.pancake_pos_shop_id == event.pancake_shop_id:
            values = {'pancake_connection_id': connection.id}
            if event.pancake_fingerprint and event.pancake_fingerprint == event.company_id._pancake_fingerprint():
                values['pancake_fingerprint'] = connection._pancake_fingerprint()
            event.write(values)
            event.order_id.write({'pancake_pos_connection_id': connection.id, 'pancake_pos_route_locked': True})
