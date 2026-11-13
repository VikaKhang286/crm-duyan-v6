from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['dac.pancake.pos.connection']._migrate_legacy_connections()
