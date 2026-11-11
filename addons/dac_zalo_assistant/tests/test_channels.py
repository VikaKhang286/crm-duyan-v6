from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestZaloChannels(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env.ref('dac_zalo_assistant.config_default')
        cls.config.write({'active': True, 'bridge_token': 'test-secret-over-32-characters-long-for-zalo', 'assistant_uid': 'bot-test'})
        cls.manager = new_test_user(cls.env, login='zalo_ch_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        cls.staff = new_test_user(cls.env, login='zalo_ch_staff', groups='base.group_user,dac_erp.group_dac_erp_sale')
        cls.event = cls.env['dac.zalo.event.type'].create({'name': 'Lead mới', 'code': 'unit.lead', 'group_name': 'Test'})
        cls.other_event = cls.env['dac.zalo.event.type'].create({'name': 'Loại khác', 'code': 'unit.other'})
        cls.channels = cls.env['dac.zalo.channel']
        cls.group = cls.channels.create({'name': 'Nhóm Sale', 'thread_id': 'G1', 'thread_type': 'group', 'event_type_ids': [(6, 0, cls.event.ids)]})
        cls.direct = cls.channels.create({'name': 'Kiệt', 'thread_id': 'U1', 'thread_type': 'user', 'event_type_ids': [(6, 0, cls.other_event.ids)]})

    def test_notify_targets_only_subscribed_active_channels(self):
        messages = self.channels._notify('unit.lead', 'Có khách mới', 'lead:1')
        self.assertEqual(messages.channel_id, self.group)
        self.assertEqual(messages.state, 'queued')
        self.group.active = False
        self.assertFalse(self.channels._notify('unit.lead', 'x', 'lead:2'))
        self.assertFalse(self.channels._notify('unit.unknown', 'x', 'lead:3'))

    def test_notify_is_idempotent_per_event_key(self):
        first = self.channels._notify('unit.lead', 'Có khách mới', 'lead:9')
        again = self.channels._notify('unit.lead', 'Có khách mới', 'lead:9')
        self.assertEqual(first, again)
        self.assertEqual(self.env['dac.zalo.channel.message'].search_count([('channel_id', '=', self.group.id)]), 1)

    def test_thread_is_unique_per_type(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.channels.create({'name': 'Trùng', 'thread_id': 'G1', 'thread_type': 'group'})
        self.channels.create({'name': 'Cùng mã khác loại', 'thread_id': 'G1', 'thread_type': 'user'})

    def test_send_posts_group_thread_type_and_marks_sent(self):
        message = self.channels._notify('unit.lead', 'Có khách mới', 'lead:5')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm1'}) as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
        sent_payload = bridge.call_args.args[1]
        self.assertEqual((sent_payload['thread_id'], sent_payload['thread_type']), ('G1', 'group'))
        self.assertEqual(sent_payload['request_id'], 'ch-%s' % message.id)
        self.assertEqual((message.state, message.external_message_id), ('sent', 'zm1'))
        self.assertTrue(message.sent_at)

    def test_uncertain_delivery_is_never_retried(self):
        message = self.channels._notify('unit.lead', 'x', 'lead:6')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'uncertain'}) as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
            self.env['dac.zalo.channel.message']._cron_process()
        self.assertEqual(bridge.call_count, 1)
        self.assertEqual(message.state, 'uncertain')
        message.action_retry()
        self.assertEqual(message.state, 'uncertain', 'Retry is for explicit failures only')

    def test_bridge_error_backs_off_then_fails_after_five_attempts(self):
        message = self.channels._notify('unit.lead', 'x', 'lead:7')
        with patch.object(type(self.config), '_bridge_request', side_effect=UserError('down')):
            self.env['dac.zalo.channel.message']._cron_process()
            self.assertEqual((message.state, message.attempts), ('queued', 1))
            self.assertTrue(message.next_attempt_at)
            message.write({'attempts': 4, 'next_attempt_at': False})
            self.env['dac.zalo.channel.message']._cron_process()
        self.assertEqual(message.state, 'failed')
        message.action_retry()
        self.assertEqual((message.state, message.attempts), ('queued', 0))

    def test_not_sent_until_assistant_is_confirmed(self):
        self.config.assistant_uid = False
        message = self.channels._notify('unit.lead', 'x', 'lead:8')
        with patch.object(type(self.config), '_bridge_request') as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
        bridge.assert_not_called()
        self.assertEqual(message.state, 'queued')

    def test_search_wizard_lists_and_adds_selected_threads(self):
        threads = {'threads': [
            {'thread_id': 'G1', 'thread_type': 'group', 'name': 'Nhóm Sale', 'members': 5},
            {'thread_id': 'G2', 'thread_type': 'group', 'name': 'Nhóm Thiết kế', 'members': 8},
            {'thread_id': 'U2', 'thread_type': 'user', 'name': 'Lan', 'members': None},
            {'thread_id': 'bad', 'thread_type': 'channel', 'name': 'ignored'},
        ]}
        wizard = self.env['dac.zalo.thread.search'].with_user(self.manager).create({'query': 'nhóm đơn'})
        with patch.object(type(self.config), '_bridge_request', return_value=threads) as bridge:
            wizard.action_search()
        self.assertEqual(bridge.call_args.args[0], '/threads?q=nh%C3%B3m%20%C4%91%C6%A1n')
        self.assertEqual(len(wizard.line_ids), 3)
        self.assertEqual(wizard.line_ids.filtered(lambda l: l.thread_id == 'G1').added, True)
        with self.assertRaises(UserError):
            wizard.action_add()
        wizard.line_ids.filtered(lambda l: l.thread_id in ('G1', 'G2')).write({'selected': True})
        action = wizard.action_add()
        created = self.channels.search([('thread_id', '=', 'G2')])
        self.assertEqual((created.name, created.thread_type, created.members), ('Nhóm Thiết kế', 'group', 8))
        self.assertEqual(action['res_id'], created.id, 'G1 already existed and must not be duplicated')
        self.assertEqual(self.channels.search_count([('thread_id', '=', 'G1')]), 1)

    def test_send_test_message_ignores_subscriptions(self):
        self.direct.with_user(self.manager).action_send_test()
        message = self.direct.message_ids
        self.assertEqual((message.state, message.event_type_id.code), ('queued', 'channel.test'))

    def test_plain_staff_cannot_manage_channels(self):
        with self.assertRaises(AccessError):
            self.channels.with_user(self.staff).search([])
        with self.assertRaises(AccessError):
            self.env['dac.zalo.channel.message'].with_user(self.staff).search([])
