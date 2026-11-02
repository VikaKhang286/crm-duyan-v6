from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from ..models.assistant import pairing_digest


@tagged('post_install', '-at_install')
class TestZaloAssistant(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.staff = new_test_user(cls.env, login='zalo_staff_a', groups='base.group_user,dac_erp.group_dac_erp_sale')
        cls.other = new_test_user(cls.env, login='zalo_staff_b', groups='base.group_user,dac_erp.group_dac_erp_sale')
        cls.config = cls.env.ref('dac_zalo_assistant.config_default')
        cls.config.write({'active': True, 'bridge_token': 'test-secret-over-32-characters-long-for-zalo', 'assistant_uid': 'bot-test'})
        cls.messages = cls.env['dac.zalo.message']

    def issue_code(self, user=None):
        wizard = self.env['dac.zalo.link.wizard'].with_user(user or self.staff).create({})
        wizard.action_generate()
        return wizard.code.split(' ', 1)[1]

    def ingest(self, text, uid='zalo-a', message_id='m1', **kwargs):
        self.messages._ingest('bot-test', [dict(thread_id=uid, message_id=message_id, text=text, direction='in', **kwargs)])

    def linked(self, user=None, uid='zalo-a'):
        code = self.issue_code(user)
        self.ingest('/xac-thuc ' + code, uid)
        return self.env['dac.zalo.binding'].search([('user_id', '=', (user or self.staff).id)])

    def test_unverified_dms_are_not_imported(self):
        self.ingest('private message from unknown user')
        self.assertFalse(self.messages.search([]))
        self.assertFalse(self.env['dac.zalo.conversation'].search([]))

    def test_pairing_is_one_time_and_redacts_code(self):
        code = self.issue_code()
        binding = self.env['dac.zalo.binding'].search([('user_id', '=', self.staff.id)])
        self.assertEqual(binding.pairing_hash, pairing_digest(code))
        self.assertNotIn(code, binding.pairing_hash)
        self.ingest('/xac-thuc ' + code)
        self.assertEqual(binding.state, 'linked')
        self.assertFalse(binding.pairing_hash)
        self.assertFalse(self.messages.search([('text', 'like', code)]))
        self.ingest('/xac-thuc ' + code, uid='imposter', message_id='m2')
        self.assertEqual(binding.zalo_uid, 'zalo-a')
        self.assertEqual(self.env['dac.zalo.conversation'].search_count([]), 1)

    def test_expired_code_cannot_link(self):
        code = self.issue_code()
        binding = self.env['dac.zalo.binding'].search([('user_id', '=', self.staff.id)])
        binding.pairing_expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.ingest('/xac-thuc ' + code)
        self.assertEqual(binding.state, 'unlinked')

    def test_same_zalo_cannot_impersonate_second_employee(self):
        self.linked()
        code = self.issue_code(self.other)
        self.ingest('/xac-thuc ' + code, message_id='m2')
        other_binding = self.env['dac.zalo.binding'].search([('user_id', '=', self.other.id)])
        self.assertEqual(other_binding.state, 'unlinked')

    def test_duplicate_inbound_does_not_duplicate_ai_or_reply(self):
        self.linked()
        self.ingest('/congviec', message_id='m2')
        count = self.messages.search_count([])
        self.ingest('/congviec', message_id='m2')
        self.assertEqual(self.messages.search_count([]), count)

    def test_employee_can_only_read_own_chat_and_cannot_read_secrets(self):
        self.linked()
        self.linked(self.other, 'zalo-b')
        own = self.env['dac.zalo.conversation'].with_user(self.staff).search([])
        self.assertEqual(own.mapped('user_id'), self.staff)
        self.assertEqual(self.messages.with_user(self.staff).search([]).mapped('user_id'), self.staff)
        with self.assertRaises(AccessError):
            self.config.with_user(self.staff).read(['ai_api_key'])
        manager = new_test_user(self.env, login='zalo_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        with self.assertRaises(AccessError):
            self.config.with_user(manager).read(['bridge_token'])
        self.assertEqual(self.env['dac.zalo.conversation'].with_user(manager).search_count([]), 2)

    def test_staff_cannot_forge_binding_or_other_conversation_send(self):
        self.linked(self.other, 'zalo-b')
        with self.assertRaises(AccessError):
            self.env['dac.zalo.binding'].with_user(self.staff).create({'user_id': self.staff.id, 'zalo_uid': 'zalo-b'})
        conversation = self.env['dac.zalo.conversation'].search([], limit=1)
        wizard = self.env['dac.zalo.compose.wizard'].with_user(self.staff).create({'conversation_id': conversation.id, 'text': 'forged'})
        with self.assertRaises(AccessError):
            wizard.action_send()

    def test_revocation_stops_future_import_and_pending_delivery(self):
        binding = self.linked()
        binding.with_user(self.staff).action_revoke()
        self.ingest('after unlink', message_id='m2')
        self.assertFalse(self.messages.search([('text', '=', 'after unlink')]))
        with patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('must not send')):
            self.messages._cron_process()
        self.assertFalse(self.messages.search([('state', '=', 'queued')]))

    def test_wrong_assistant_account_is_rejected(self):
        with self.assertRaises(UserError):
            self.messages._ingest('other-bot', [])

    def test_ai_sees_only_this_conversation_and_users_own_tasks(self):
        self.linked()
        self.linked(self.other, 'zalo-b')
        tasks = self.env['dac.work.task']
        tasks.create({'name': 'ALLOWED-TASK', 'assigned_user_id': self.staff.id,
            'is_personal_reminder': True, 'deadline': fields.Datetime.now() + timedelta(days=1)})
        tasks.create({'name': 'OTHER-EMPLOYEE-SECRET', 'assigned_user_id': self.other.id,
            'is_personal_reminder': True, 'deadline': fields.Datetime.now() + timedelta(days=1)})
        self.config.write({'ai_enabled': True, 'ai_api_key': 'mock-ai-key', 'ai_model': 'mock-model'})
        self.ingest('OTHER-CHAT-SECRET', uid='zalo-b', message_id='m2')
        self.ingest('/ai Tôi cần làm gì?', message_id='m3')
        captured = []
        with patch.object(type(self.config), '_ask_ai', side_effect=lambda prompt: captured.extend(prompt) or 'Bạn có công việc được giao.'):
            message = self.messages.search([('state', '=', 'ai_pending')], limit=1)
            message._process_ai(self.config)
        prompt = str(captured)
        self.assertIn('ALLOWED-TASK', prompt)
        self.assertNotIn('OTHER-EMPLOYEE-SECRET', prompt)
        self.assertNotIn('OTHER-CHAT-SECRET', prompt)
        self.assertNotIn('/xac-thuc', prompt)
        self.assertTrue(message.ai_processed)

    def test_ai_failure_is_visible_without_provider_body(self):
        self.linked()
        self.config.write({'ai_enabled': True, 'ai_api_key': 'mock-ai-key', 'ai_model': 'mock-model'})
        self.ingest('/ai test', message_id='m2')
        message = self.messages.search([('state', '=', 'ai_pending')])
        with patch('odoo.addons.dac_zalo_assistant.models.assistant.requests.post') as post:
            post.return_value.status_code = 401
            post.return_value.text = 'SECRET-PROVIDER-ERROR'
            message._process_ai(self.config)
        self.assertEqual(message.state, 'failed')
        self.assertIn('401', message.error_message)
        self.assertNotIn('SECRET-PROVIDER-ERROR', str(self.messages.search([]).mapped('text')))

    def test_historical_replay_cannot_trigger_ai_or_import_prelink_chat(self):
        self.linked()
        self.config.write({'ai_enabled': True, 'ai_api_key': 'mock-ai-key', 'ai_model': 'mock-model', 'ai_auto_reply': True})
        now_ms = int((fields.Datetime.now() - fields.Datetime.to_datetime('1970-01-01')).total_seconds() * 1000)
        self.ingest('/ai old', message_id='old1', historical=True, timestamp=now_ms - 86400000)
        self.assertFalse(self.messages.search([('external_message_id', '=', 'old1')]))
        before = self.messages.search_count([('direction', '=', 'out')])
        self.ingest('/ai recent', message_id='old2', historical=True, timestamp=now_ms + 1000)
        message = self.messages.search([('external_message_id', '=', 'old2')])
        self.assertEqual(message.state, 'received')
        self.assertEqual(self.messages.search_count([('direction', '=', 'out')]), before)

    def test_uncertain_delivery_is_not_silently_retried(self):
        self.linked()
        message = self.messages.search([('state', '=', 'queued')], limit=1)
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'uncertain'}):
            message._send(self.config)
        self.assertEqual(message.state, 'uncertain')

    def test_automatic_ai_daily_limit_counts_non_command_messages(self):
        self.linked()
        self.config.write({'ai_enabled': True, 'ai_api_key': 'mock-ai-key', 'ai_model': 'mock-model', 'ai_auto_reply': True})
        conversation = self.env['dac.zalo.conversation'].search([], limit=1)
        self.messages.create([{'conversation_id': conversation.id, 'event_key': 'used:%s' % i,
            'direction': 'in', 'text': 'ordinary question', 'state': 'received', 'ai_processed': True} for i in range(50)])
        self.ingest('one more ordinary question', message_id='over-limit')
        message = self.messages.search([('external_message_id', '=', 'over-limit')])
        with patch.object(type(self.config), '_ask_ai', side_effect=AssertionError('must not bill a 51st request')):
            message._process_ai(self.config)
        self.assertEqual(message.state, 'received')
        self.assertTrue(self.messages.search([('text', 'ilike', 'giới hạn AI')]))
