from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestResUsersHomeAction(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'DAC Home Action Test',
            'login': 'dac.home.action.test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })

    def test_setting_production_role_updates_home_action(self):
        self.user.dac_role = 'production'

        self.assertEqual(
            self.user.action_id.id,
            self.env.ref('dac_erp.dac_production_dashboard_action').id,
        )

    def test_sync_repairs_existing_role_home_action(self):
        production_group = self.env.ref('dac_erp.group_dac_erp_production')
        attendance_action = self.env.ref(
            'dac_attendance.dac_attendance_manager_dashboard_action'
        )
        self.user.write({
            'groups_id': [(4, production_group.id)],
            'action_id': attendance_action.id,
        })

        self.env['res.users']._dac_sync_role_home_actions()

        self.assertEqual(
            self.user.action_id.id,
            self.env.ref('dac_erp.dac_production_dashboard_action').id,
        )
