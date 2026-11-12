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

    def test_styles_are_stored_and_forwarded_to_the_bridge(self):
        styles = [{'start': 0, 'len': 11, 'style': 'bold'}]
        message = self.channels._notify('unit.lead', 'Có khách mới\nchi tiết', 'lead:styled', styles=styles)
        self.assertEqual(message.styles, styles)
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zs1'}) as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
        self.assertEqual(bridge.call_args.args[1]['styles'], styles)
        plain = self.channels._notify('unit.lead', 'Tin thường', 'lead:plain')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zs2'}) as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
        self.assertNotIn('styles', bridge.call_args.args[1])
        self.assertFalse(plain.styles)

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
        with patch.object(type(self.config), '_bridge_request', return_value={'members': [{'uid': '10', 'name': 'An'}]}):
            action = wizard.action_add()
        created = self.channels.search([('thread_id', '=', 'G2')])
        self.assertEqual((created.name, created.thread_type, created.members), ('Nhóm Thiết kế', 'group', 8))
        self.assertEqual(action['res_id'], created.id, 'G1 already existed and must not be duplicated')
        self.assertEqual(self.channels.search_count([('thread_id', '=', 'G1')]), 1)

    def test_send_test_message_ignores_subscriptions(self):
        self.direct.with_user(self.manager).action_send_test()
        message = self.direct.message_ids
        self.assertEqual((message.state, message.event_type_id.code), ('queued', 'channel.test'))
        self.assertEqual(message.styles, [{'start': 0, 'len': len('Tin thử từ Odoo'), 'style': 'bold'}])
        self.assertTrue(message.text.startswith('Tin thử từ Odoo\nHội thoại "Kiệt" sẽ nhận: Loại khác'))

    def test_plain_staff_cannot_manage_channels(self):
        with self.assertRaises(AccessError):
            self.channels.with_user(self.staff).search([])
        with self.assertRaises(AccessError):
            self.env['dac.zalo.channel.message'].with_user(self.staff).search([])


    # ── Nhắc tên (@tag) trong nhóm ─────────────────────────────────────────────
    def _members(self, channel, *pairs):
        return self.env['dac.zalo.channel.member'].create([{'channel_id': channel.id, 'uid': uid, 'name': name} for uid, name in pairs])

    def _rule(self, channel, event, members):
        return self.env['dac.zalo.channel.mention'].create({'channel_id': channel.id, 'event_type_id': event.id, 'member_ids': [(6, 0, members.ids)]})

    def test_mentions_are_appended_with_correct_utf16_positions(self):
        an, binh = self._members(self.group, ('111', 'An'), ('222', 'Bình  Phạm'))
        self._rule(self.group, self.event, an | binh)
        text = '👤 Khách\n📞 0909 xem'
        message = self.channels._notify('unit.lead', text, 'lead:mention')
        self.assertEqual(message.text, text + '\n@An @Bình Phạm', 'names are space-normalised, mention line is last')
        utf16 = lambda value: len(value.encode('utf-16-le')) // 2
        pos_an = utf16(text + '\n')
        self.assertEqual(message.mentions, [{'pos': pos_an, 'len': 3, 'uid': '111'},
                                            {'pos': pos_an + 4, 'len': utf16('@Bình Phạm'), 'uid': '222'}])
        self.assertGreater(utf16(text), len(text), 'the text has emoji outside the BMP, so UTF-16 differs from len()')

    def test_reply_reminder_puts_names_first_and_quotes_the_sent_message(self):
        an, binh = self._members(self.group, ('111', 'An 😀'), ('222', 'Bình'))
        self._rule(self.group, self.event, an | binh)
        original = self.channels._notify('unit.lead', 'Có khách mới', 'lead:reply')
        original.write({'state': 'sent', 'external_message_id': 'zm-1'})
        reply = original._queue_reply('Bạn đã liên hệ khách chưa ạ', 'remind:reply')
        self.assertEqual(reply.text, '@An 😀 @Bình Bạn đã liên hệ khách chưa ạ')
        utf16 = lambda value: len(value.encode('utf-16-le')) // 2
        self.assertEqual(reply.mentions, [{'pos': 0, 'len': utf16('@An 😀'), 'uid': '111'},
                                          {'pos': utf16('@An 😀') + 1, 'len': 5, 'uid': '222'}])
        self.assertEqual(reply.reply_to_id, original)
        self.assertEqual(original._queue_reply('x', 'remind:reply'), reply, 'idempotent by key')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm-2', 'quoted': True}) as bridge:
            reply._send(self.config)
        self.assertEqual(bridge.call_args.args[1]['quote_message_id'], 'zm-1')
        self.assertEqual(original.reply_to_id.id, False)
        # tin gốc chưa gửi được thì không đòi trích dẫn
        unsent = self.channels._notify('unit.lead', 'Khách nữa', 'lead:unsent')
        later = unsent._queue_reply('Nhắc', 'remind:unsent')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm-3'}) as bridge:
            later._send(self.config)
        self.assertNotIn('quote_message_id', bridge.call_args.args[1])

    def test_no_mentions_without_rule_for_event_or_for_private_chats(self):
        an = self._members(self.group, ('111', 'An'))
        self._rule(self.group, self.other_event, an)
        plain = self.channels._notify('unit.lead', 'Tin', 'lead:nomention')
        self.assertEqual((plain.text, plain.mentions), ('Tin', False))
        user_member = self.env['dac.zalo.channel.member'].create({'channel_id': self.group.id, 'uid': '5', 'name': 'X'})
        self.assertFalse(self.direct._mention_members(self.other_event), 'private chats never mention')
        self.assertFalse(self.group._mention_members(self.env['dac.zalo.event.type']))
        self.assertTrue(user_member)

    def test_mentions_are_forwarded_to_the_bridge(self):
        an = self._members(self.group, ('111', 'An'))
        self._rule(self.group, self.event, an)
        message = self.channels._notify('unit.lead', 'Có khách', 'lead:mention-send')
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm9'}) as bridge:
            self.env['dac.zalo.channel.message']._cron_process()
        self.assertEqual(bridge.call_args.args[1]['mentions'], message.mentions)
        self.assertEqual(bridge.call_args.args[1]['text'], 'Có khách\n@An')

    def test_a_rule_only_accepts_members_of_its_own_group_and_at_most_thirty(self):
        other_group = self.channels.create({'name': 'Nhóm khác', 'thread_id': 'G9', 'thread_type': 'group'})
        stranger = self._members(other_group, ('900', 'Người lạ'))
        with self.assertRaises(UserError), self.cr.savepoint():
            self._rule(self.group, self.event, stranger)
        many = self._members(self.group, *[(str(1000 + i), 'Tv %s' % i) for i in range(31)])
        with self.assertRaises(UserError), self.cr.savepoint():
            self._rule(self.group, self.event, many)
        self._rule(self.group, self.event, many[:30])
        with self.assertRaises(Exception), self.cr.savepoint():
            self._rule(self.group, self.event, many[:1])

    def test_sync_members_adds_renames_and_removes_people_who_left(self):
        an, binh = self._members(self.group, ('111', 'An cũ'), ('222', 'Bình'))
        rule = self._rule(self.group, self.event, an | binh)
        reply = {'members': [{'uid': '111', 'name': 'An mới'}, {'uid': '333', 'name': ' Chi\n Lê '}, {'uid': 'abc', 'name': 'bad'}, 'junk']}
        with patch.object(type(self.config), '_bridge_request', return_value=reply) as bridge:
            self.group.with_user(self.manager).action_sync_members()
        self.assertEqual(bridge.call_args.args[0], '/threads/G1/members')
        self.assertEqual({m.uid: m.name for m in self.group.member_ids}, {'111': 'An mới', '333': 'Chi Lê'})
        self.assertEqual(rule.member_ids.mapped('uid'), ['111'], 'a member who left the group is removed from the rule')
        self.assertTrue(self.group.members_synced_at)
        with self.assertRaises(UserError):
            self.direct._sync_members()

    def test_wizard_with_event_codes_subscribes_new_groups_and_prepares_the_mention_row(self):
        listing = {'threads': [{'thread_id': 'G7', 'thread_type': 'group', 'name': 'Nhóm Lead', 'members': 4}]}
        with patch.object(type(self.config), '_bridge_request', return_value=listing) as bridge:
            action = self.env['dac.zalo.thread.search'].action_open_for_events(['unit.lead'])
        self.assertEqual(bridge.call_args.args[0], '/threads?q=', 'opening the picker lists every conversation without pressing Search')
        wizard = self.env['dac.zalo.thread.search'].with_user(self.manager).browse(action['res_id'])
        self.assertEqual([l.name for l in wizard.line_ids], ['Nhóm Lead'])
        self.assertEqual(wizard.event_names, 'Lead mới')
        wizard.line_ids.unlink()
        wizard.line_ids = [(0, 0, {'thread_id': 'G7', 'thread_type': 'group', 'name': 'Nhóm Lead', 'members': 4, 'selected': True}),
                           (0, 0, {'thread_id': 'U7', 'thread_type': 'user', 'name': 'Lan', 'members': 0, 'selected': True})]
        with patch.object(type(self.config), '_bridge_request', return_value={'members': [{'uid': '7', 'name': 'Hoa'}]}):
            wizard.action_add()
        group = self.channels.search([('thread_id', '=', 'G7')])
        private = self.channels.search([('thread_id', '=', 'U7')])
        self.assertEqual(group.event_type_ids, self.event)
        self.assertEqual(group.member_ids.mapped('name'), ['Hoa'])
        self.assertEqual(group.mention_ids.event_type_id, self.event)
        self.assertEqual(private.event_type_ids, self.event)
        self.assertFalse(private.mention_ids, 'private chats get no mention row')

    def test_picker_still_opens_when_the_bridge_is_down(self):
        with patch.object(type(self.config), '_bridge_request', side_effect=UserError('bridge down')):
            action = self.channels.action_open_search()
        wizard = self.env['dac.zalo.thread.search'].browse(action['res_id'])
        self.assertEqual((wizard.searched, len(wizard.line_ids)), (False, 0))

    def test_failed_member_sync_does_not_block_adding_the_group(self):
        wizard = self.env['dac.zalo.thread.search'].create({})
        wizard.line_ids = [(0, 0, {'thread_id': 'G8', 'thread_type': 'group', 'name': 'Nhóm tạm', 'selected': True})]
        with patch.object(type(self.config), '_bridge_request', side_effect=UserError('bridge down')):
            wizard.action_add()
        self.assertTrue(self.channels.search([('thread_id', '=', 'G8')]))

    def test_test_message_mentions_people_configured_for_subscribed_events(self):
        an = self._members(self.group, ('111', 'An'))
        self._rule(self.group, self.event, an)
        self.group.with_user(self.manager).action_send_test()
        message = self.group.message_ids
        self.assertTrue(message.text.endswith('\n@An'))
        self.assertEqual(message.mentions[0]['uid'], '111')
