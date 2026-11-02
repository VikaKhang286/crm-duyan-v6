"""Run through `odoo shell` on the isolated empty dac_zalo_test database only.

The existing DAC modules reference menus/client actions before their XML data
files load. Production already has these IDs. Seed their records for a blank
integration-test database without changing the existing modules or their order.
Their real definitions are applied during the subsequent normal installation.
"""
from pathlib import Path

from lxml import etree

assert env.cr.dbname == 'dac_zalo_test', 'Only the dedicated Zalo test database is supported.'  # noqa: F821
root = Path('/mnt/extra-addons')
for module in ('dac_erp', 'CRM_DAC', 'dac_report'):
    for path in (root / module / 'views').glob('*.xml'):
        tree = etree.parse(str(path))
        for node in tree.xpath('//menuitem[@id] | //record[@model="ir.actions.client"]'):
            name = node.get('id')
            if '.' in name or env.ref(module + '.' + name, raise_if_not_found=False):  # noqa: F821
                continue
            if node.tag == 'menuitem':
                model = 'ir.ui.menu'
                values = {'name': node.get('name') or name}
            else:
                tag = node.find('field[@name="tag"]')
                if tag is None or not tag.text:
                    continue
                model = 'ir.actions.client'
                values = {'name': 'Test prerequisite ' + name, 'tag': tag.text}
            record = env[model].create(values)  # noqa: F821
            env['ir.model.data'].create({  # noqa: F821
                'module': module, 'name': name, 'model': model, 'res_id': record.id,
            })
env.cr.commit()  # noqa: F821
