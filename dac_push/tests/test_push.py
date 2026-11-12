import hashlib
import hmac
import json
import time
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

SECRET = 'unit-test-secret'


def sign(body, ts=None):
    ts = str(ts or int(time.time()))
    digest = hmac.new(SECRET.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return {'X-DAC-Timestamp': ts, 'X-DAC-Signature': digest, 'Content-Type': 'application/json'}


def envelope(event_id='e1', event_type='web_lead.created', **data):
    data = {'name': 'Anh A', 'phone': '0901 234 567', 'form_type': 'quote', 'items': ['In bạt', 'Standee'],
            'message': 'Cần báo giá gấp', 'site': 'duyan.vn', **data}
    return {'event_id': event_id, 'event_type': event_type, 'source': 'duyan.vn', 'data': data}


@tagged('post_install', '-at_install')
class TestPushEvents(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.events = cls.env['dac.push.event']
        cls.channel = cls.env['dac.zalo.channel'].create({
            'name': 'Nhóm Sale', 'thread_id': 'G-push', 'thread_type': 'group',
            'event_type_ids': [(6, 0, cls.env.ref('dac_push.event_type_web_lead_created').ids)]})

    def test_web_lead_creates_lead_and_queues_zalo_message(self):
        event, created = self.events._receive(envelope())
        self.assertTrue(created)
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertEqual((lead.phone, lead.items, lead.form_type), ('0901234567', 'In bạt, Standee', 'quote'))
        message = self.channel.message_ids
        self.assertEqual(len(message), 1)
        self.assertEqual(message.state, 'queued')
        self.assertEqual(message.text.splitlines(), [
            'Khách yêu cầu tư vấn', '👤 Anh A', '📞 0901234567', '➕ Yêu cầu: In bạt, Standee', '💬 Cần báo giá gấp',
            'Xem chi tiết: %s' % lead._short_link()])

    def test_lead_message_tags_the_people_configured_for_new_leads(self):
        event_type = self.env.ref('dac_push.event_type_web_lead_created')
        member = self.env['dac.zalo.channel.member'].create([
            {'channel_id': self.channel.id, 'uid': '111', 'name': 'An'}, {'channel_id': self.channel.id, 'uid': '222', 'name': 'Bình'}])
        self.env['dac.zalo.channel.mention'].create({'channel_id': self.channel.id, 'event_type_id': event_type.id, 'member_ids': [(6, 0, member.ids)]})
        self.events._receive(envelope('tag1'))
        message = self.channel.message_ids
        self.assertTrue(message.text.endswith('\n@An @Bình'))
        self.assertEqual([m['uid'] for m in message.mentions], ['111', '222'])
        self.assertEqual(message.styles, [{'start': 0, 'len': len('Khách yêu cầu tư vấn'), 'style': 'bold'}], 'the bold title is unchanged')

    def _lead_with_sent_message(self, event_id='rem1', mention=True):
        if mention:
            event_type = self.env.ref('dac_push.event_type_web_lead_created')
            members = self.env['dac.zalo.channel.member'].create([
                {'channel_id': self.channel.id, 'uid': '111', 'name': 'An'}, {'channel_id': self.channel.id, 'uid': '222', 'name': 'Bình'}])
            self.env['dac.zalo.channel.mention'].create({'channel_id': self.channel.id, 'event_type_id': event_type.id, 'member_ids': [(6, 0, members.ids)]})
        event, _ = self.events._receive(envelope(event_id))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        lead.zalo_message_ids.write({'state': 'sent', 'external_message_id': 'zm-original'})
        return lead

    def test_new_lead_gets_a_working_hours_deadline_and_remembers_its_zalo_messages(self):
        lead = self._lead_with_sent_message('rem0', mention=False)
        self.assertIn(self.channel.message_ids, lead.zalo_message_ids, 'every channel that received the lead is remembered')
        self.assertTrue(not lead.remind_due_at or lead.remind_due_at > lead.received_at)
        self.assertFalse(lead.reminded_at)

    def test_overdue_lead_is_reminded_once_as_a_reply_that_tags_the_people_for_leads(self):
        lead = self._lead_with_sent_message('rem1')
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)  # DB thử còn nhóm khác cùng nhận lead
        leads = self.env['dac.web.lead']
        leads._cron_remind_uncontacted()
        self.assertEqual(len(self.channel.message_ids), 1, 'not due yet: nothing is sent')
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        leads._cron_remind_uncontacted()
        reminder = self.channel.message_ids - original
        self.assertEqual(len(reminder), 1)
        self.assertEqual(reminder.text, '@An @Bình Bạn đã liên hệ khách chưa ạ')
        self.assertEqual(reminder.mentions, [{'pos': 0, 'len': 3, 'uid': '111'}, {'pos': 4, 'len': 5, 'uid': '222'}])
        self.assertEqual(reminder.reply_to_id, original, 'the reminder replies to the original lead message')
        self.assertTrue(lead.reminded_at)
        leads._cron_remind_uncontacted()
        self.assertEqual(len(self.channel.message_ids), 2, 'one reminder only')
        from unittest.mock import patch
        config = self.env['dac.zalo.config']._config()
        config.write({'active': True, 'assistant_uid': 'bot'})
        with patch.object(type(config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm2', 'quoted': True}) as bridge:
            reminder._send(config)
        payload = bridge.call_args.args[1]
        self.assertEqual(payload['quote_message_id'], 'zm-original')
        self.assertEqual(payload['mentions'], reminder.mentions)
        self.assertEqual(reminder.state, 'sent')

    def test_no_reminder_once_someone_decided_or_when_there_is_no_deadline(self):
        decided = self._lead_with_sent_message('rem2', mention=False)
        decided.remind_due_at = fields.Datetime.now() - timedelta(minutes=5)
        decided._set_contact_state('unreachable')
        old = self._lead_with_sent_message('rem3', mention=False)
        old.remind_due_at = False  # lead tạo trước khi có chức năng nhắc
        self.env['dac.web.lead']._cron_remind_uncontacted()
        self.assertFalse(decided.reminded_at or old.reminded_at)
        self.assertEqual(len(self.channel.message_ids), 2, 'only the two original lead messages exist')
        self.assertFalse(self.env['dac.zalo.channel.message'].search([('reply_to_id', '!=', False)]), 'no reply anywhere')

    def test_reminder_waits_for_an_unsent_original_and_works_without_tagged_people(self):
        lead = self._lead_with_sent_message('rem4', mention=False)
        lead.zalo_message_ids.state = 'queued'
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env['dac.web.lead']._cron_remind_uncontacted()
        self.assertFalse(lead.reminded_at, 'the original message has not left yet: nothing to reply to, try again later')
        lead.zalo_message_ids.state = 'sent'
        self.env['dac.web.lead']._cron_remind_uncontacted()
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)
        reminder = self.channel.message_ids - original
        self.assertEqual((reminder.text, reminder.mentions, reminder.reply_to_id), ('Bạn đã liên hệ khách chưa ạ', False, original))

    @staticmethod
    def _vn(text):
        from datetime import datetime, timezone
        from ..models.working_time import TZ
        return datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)

    def _lead_at(self, event_id, when):
        from unittest.mock import patch
        with patch.object(fields.Datetime, 'now', return_value=self._vn(when)):
            event, _ = self.events._receive(envelope(event_id))
        return self.env['dac.web.lead'].search([('event_id', '=', event.id)])

    def test_test_leads_are_recognised_by_name_or_phone(self):
        Lead = self.env['dac.web.lead']
        for name, phone in (('test', '0912345678'), ('Test', '0912345678'), ('  TEST  ', ''), ('test 2', '0912345678'), ('Anh A', '0999999999'),
                            ('Anh A', '+84999999999'), ('Anh A', '0999 999 999'), ('', '0999999999')):
            self.assertTrue(Lead.new({'customer_name': name, 'phone': phone}).is_test, (name, phone))
        for name, phone in (('Anh A', '0901234567'), ('Tester', '0912345678'), ('Nguyễn Test', '0912345678'), ('', ''), ('Anh A', '0999999998'),
                            ('Anh A', '09999999999')):
            self.assertFalse(Lead.new({'customer_name': name, 'phone': phone}).is_test, (name, phone))

    def test_test_leads_only_reach_test_channels_and_real_leads_never_do(self):
        event_type = self.env.ref('dac_push.event_type_web_lead_created')
        test_channel = self.env['dac.zalo.channel'].create({'name': 'Nhóm thử', 'thread_id': 'G-push-test', 'thread_type': 'group',
                                                            'is_test': True, 'event_type_ids': [(6, 0, event_type.ids)]})
        for event_id, data in (('rt1', {'name': 'test'}), ('rt2', {'phone': '0999999999', 'name': 'Anh B'}), ('rt3', {'name': 'Khách thật', 'phone': '0911222333'})):
            event, _ = self.events._receive(envelope(event_id, **data))
            lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
            channels = lead.zalo_message_ids.channel_id
            if lead.is_test:
                self.assertTrue(channels and all(c.is_test for c in channels), 'a test lead reaches test channels only (%s)' % event_id)
                self.assertIn(test_channel, channels)
                self.assertNotIn(self.channel, channels)
            else:
                self.assertTrue(channels and not any(c.is_test for c in channels), 'a real lead never reaches a test channel')
                self.assertIn(self.channel, channels)
                self.assertNotIn(test_channel, channels)

    def test_summary_of_test_leads_goes_to_the_test_group_only(self):
        event_type = self.env.ref('dac_push.event_type_web_lead_created')
        test_channel = self.env['dac.zalo.channel'].create({'name': 'Nhóm thử', 'thread_id': 'G-push-test2', 'thread_type': 'group',
                                                            'is_test': True, 'event_type_ids': [(6, 0, event_type.ids)]})
        real = self._lead_at('sm-real', '2026-11-12 09:00')
        event, _ = self.events._receive(envelope('sm-test', name='test'))
        test = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        for lead in (real, test):
            lead.zalo_message_ids.write({'state': 'sent'})
            lead.received_at = self._vn('2026-11-12 09:00')
        test.write({'phone': '0999999999'})
        real.write({'phone': '0933000111'})
        self.env['dac.web.lead']._send_summary(self._vn('2026-11-12 11:30'))
        in_test = test_channel.message_ids.filtered(lambda m: m.event_key.startswith('lead-summary:'))
        in_real = self.channel.message_ids.filtered(lambda m: m.event_key.startswith('lead-summary:'))
        self.assertIn('0999999999', in_test.text)
        self.assertNotIn('0933000111', in_test.text)
        self.assertIn('0933000111', in_real.text)
        self.assertNotIn('0999999999', in_real.text)

    def test_summary_slots_run_on_working_days_only(self):
        from datetime import timezone
        from ..models.working_time import TZ, latest_summary_slot, next_summary_slot

        def show(value):
            return value.replace(tzinfo=timezone.utc).astimezone(TZ).strftime('%a %H:%M')

        self.assertEqual(show(latest_summary_slot(self._vn('2026-11-12 11:29'))), 'Thu 08:00')
        self.assertEqual(show(latest_summary_slot(self._vn('2026-11-12 11:30'))), 'Thu 11:30')
        self.assertEqual(show(latest_summary_slot(self._vn('2026-11-12 16:59'))), 'Thu 14:00')
        self.assertEqual(show(latest_summary_slot(self._vn('2026-11-12 07:00'))), 'Wed 17:00', 'before the first slot of the day')
        self.assertEqual(show(latest_summary_slot(self._vn('2026-11-15 12:00'))), 'Sat 17:00', 'Sunday has no slot')
        self.assertEqual(show(next_summary_slot(self._vn('2026-11-12 11:30'))), 'Thu 14:00', 'strictly after')
        self.assertEqual(show(next_summary_slot(self._vn('2026-11-12 17:10'))), 'Fri 08:00')
        self.assertEqual(show(next_summary_slot(self._vn('2026-11-14 17:10'))), 'Mon 08:00', 'Saturday evening → Monday morning')

    def test_own_reminder_is_skipped_when_a_summary_comes_first(self):
        due = lambda event_id, when: self._lead_at(event_id, when).remind_due_at
        self.assertEqual(due('sk1', '2026-11-12 09:00'), self._vn('2026-11-12 09:30'), 'next summary 11:30 is later: remind alone first')
        self.assertFalse(due('sk2', '2026-11-12 11:10'), '11:40 would come after the 11:30 summary: the summary covers it')
        self.assertEqual(due('sk3', '2026-11-12 11:45'), self._vn('2026-11-12 13:45'), 'own reminder 13:45 precedes the 14:00 summary')
        self.assertFalse(due('sk4', '2026-11-12 17:10'), 'would be 08:10 tomorrow, after the 08:00 summary')
        self.assertFalse(due('sk5', '2026-11-12 11:00'), 'same minute as the summary: the summary wins')

    def _reminded_lead(self, event_id='ack1'):
        lead = self._lead_with_sent_message(event_id)
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env['dac.web.lead']._cron_remind_uncontacted()
        reminder = lead.reminder_message_ids.filtered(lambda m: m.channel_id == self.channel)
        reminder.write({'state': 'sent', 'external_message_id': 'zm-remind-%s' % event_id})
        return lead, reminder

    def test_reaction_of_a_tagged_person_on_the_reminder_stops_all_further_reminders(self):
        self.env['dac.zalo.config']._config().write({'active': True, 'assistant_uid': 'bot-push'})
        lead, reminder = self._reminded_lead('ack1')
        self.assertTrue(reminder, 'the reminder is remembered on the lead')
        react = lambda uid, ids=None: self.env['dac.zalo.channel.reaction']._ingest('bot-push', [{
            'thread_id': 'G-push', 'reactor_uid': uid, 'icon': '/-strong', 'message_ids': ids or ['zm-remind-ack1']}])
        react('999')  # người không được nhắc tên
        self.assertFalse(lead.acked_at, 'only the tagged people count')
        react('111', ['zm-original-other'])  # reaction lên tin khác
        self.assertFalse(lead.acked_at)
        react('111')
        self.assertTrue(lead.acked_at)
        self.assertEqual(lead.acked_uid, '111')
        before = len(self.channel.message_ids)
        slot = self._vn('2026-11-12 11:30')
        lead.received_at = self._vn('2026-11-12 09:00')
        self.env['dac.web.lead']._send_summary(slot)
        self.assertEqual(len(self.channel.message_ids), before, 'an acknowledged lead is left out of the summaries')

    def test_reaction_counts_from_anyone_when_nobody_is_tagged(self):
        self.env['dac.zalo.config']._config().write({'active': True, 'assistant_uid': 'bot-push'})
        lead = self._lead_with_sent_message('ack2', mention=False)
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env['dac.web.lead']._cron_remind_uncontacted()
        reminder = lead.reminder_message_ids.filtered(lambda m: m.channel_id == self.channel)
        reminder.write({'state': 'sent', 'external_message_id': 'zm-remind-ack2'})
        self.env['dac.zalo.channel.reaction']._ingest('bot-push', [{'thread_id': 'G-push', 'reactor_uid': '777', 'icon': '/-heart', 'message_ids': ['zm-remind-ack2']}])
        self.assertTrue(lead.acked_at)

    def test_summary_lists_every_open_number_to_the_people_for_leads(self):
        first = self._lead_with_sent_message('sm1')
        second = self._lead_at('sm2', '2026-11-12 11:10')
        second.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel).write({'state': 'sent', 'external_message_id': 'zm-sm2'})
        done = self._lead_at('sm3', '2026-11-12 09:30')
        done.zalo_message_ids.write({'state': 'sent'})
        done._set_contact_state('contacted')
        late = self._lead_at('sm4', '2026-11-12 11:40')  # nhận sau mốc 11:30
        late.zalo_message_ids.write({'state': 'sent'})
        first.write({'received_at': self._vn('2026-11-11 16:00'), 'customer_name': 'Chị Hoa', 'phone': '0933939856'})
        second.write({'customer_name': False, 'phone': '0911222333'})
        slot = self._vn('2026-11-12 11:30')
        before = self.channel.message_ids
        self.env['dac.web.lead']._send_summary(slot)
        summary = self.channel.message_ids - before
        self.assertEqual(len(summary), 1)
        lines = summary.text.splitlines()
        self.assertEqual(lines[0], '@An @Bình Còn 2 khách chưa liên hệ:')
        self.assertEqual(lines[1:], ['0933939856: ' + first._short_link(compact=True), '0911222333: ' + second._short_link(compact=True)],
                         'oldest waiting first; only "phone: short link"')
        self.assertRegex(lines[1], r'^0933939856: [^/]+/l/[\w-]{12}$')
        self.assertEqual([m['uid'] for m in summary.mentions], ['111', '222'])
        self.assertFalse(summary.reply_to_id, 'a summary is a normal message, not a reply')
        self.env['dac.web.lead']._send_summary(slot)
        self.assertEqual(len(self.channel.message_ids - before), 1, 'the same slot never sends twice')

    def test_summary_cron_sends_once_per_slot_and_not_when_stale_or_on_first_run(self):
        from unittest.mock import patch
        Lead = self.env['dac.web.lead']
        lead = self._lead_with_sent_message('cr1', mention=False)
        lead.received_at = self._vn('2026-11-12 09:00')
        params = self.env['ir.config_parameter'].sudo()
        params.search([('key', '=', 'dac_push.last_summary_slot')]).unlink()
        before = len(self.channel.message_ids)
        now = lambda text: patch.object(fields.Datetime, 'now', return_value=self._vn(text))
        with now('2026-11-12 11:31'):
            Lead._cron_summary_uncontacted()
        self.assertEqual(len(self.channel.message_ids), before, 'first run only records the current slot')
        self.assertEqual(params.get_param('dac_push.last_summary_slot'), fields.Datetime.to_string(self._vn('2026-11-12 11:30')))
        with now('2026-11-12 11:40'):
            Lead._cron_summary_uncontacted()
        self.assertEqual(len(self.channel.message_ids), before, 'same slot again: nothing')
        with now('2026-11-12 14:01'):
            Lead._cron_summary_uncontacted()
            Lead._cron_summary_uncontacted()
        self.assertEqual(len(self.channel.message_ids), before + 1, 'new slot: exactly one summary')
        self.assertIn('Còn 1 khách chưa liên hệ', self.channel.message_ids.sorted('id')[-1].text)
        with now('2026-11-12 19:30'):  # Odoo ngừng qua mốc 17:00 hơn 2 giờ: bỏ mốc đó
            Lead._cron_summary_uncontacted()
        self.assertEqual(len(self.channel.message_ids), before + 1, 'a slot missed by hours is skipped')
        self.assertEqual(params.get_param('dac_push.last_summary_slot'), fields.Datetime.to_string(self._vn('2026-11-12 17:00')))
        with now('2026-11-13 08:01'):
            Lead._cron_summary_uncontacted()
        self.assertEqual(len(self.channel.message_ids), before + 2, 'the next morning slot lists the number again')

    def test_contacted_intent_ignores_accents_case_and_negations(self):
        from ..models.contact_intent import means_contacted
        for text in ('đã liên hệ', 'Đã liên hệ ạ', 'da lien he', 'DA LIEN HE a', 'đả liên hệ', 'đã gọi', 'Da goi roi', 'gọi rồi', 'liên hệ rồi',
                     'đã call', 'đã nhắn tin', 'đã gọi điện cho khách rồi', 'Em đã liên hệ, khách hẹn chiều', 'ok đã gọi nhé'):
            self.assertTrue(means_contacted(text), text)
        for text in ('chưa liên hệ', 'chua goi', 'không liên hệ được', 'ko lien he duoc', 'đã gọi nhưng khách không nghe máy', 'liên hệ',
                     'gọi giúp em', 'ok', 'cảm ơn', 'đã nhận', 'đã xem', '', 'đã', 'đã ' + 'blah ' * 30 + 'gọi'):
            self.assertFalse(means_contacted(text), text)

    def _reply(self, message, text, reply_id='9001', sender='111'):
        self.env['dac.zalo.config']._config().write({'active': True, 'assistant_uid': 'bot-push'})
        self.env['dac.zalo.channel.message']._ingest_replies('bot-push', [{
            'thread_id': 'G-push', 'sender_uid': sender, 'message_id': reply_id, 'quoted_message_id': message.external_message_id, 'text': text}])

    def test_reply_contacted_to_the_lead_message_marks_it_and_the_bot_thanks_by_quoting(self):
        lead = self._lead_with_sent_message('rp1')
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)
        before = self.channel.message_ids
        self._reply(original, 'Da goi roi a')
        self.assertEqual(lead.contact_state, 'contacted')
        thanks = self.channel.message_ids - before
        self.assertEqual((thanks.text, thanks.quote_message_id, thanks.mentions), ('Em xin cảm ơn ạ', '9001', False))
        self._reply(original, 'Da goi roi a')  # cùng tin trả lời được gửi lại (bridge thử lại): không cảm ơn hai lần
        self.assertEqual(len(self.channel.message_ids - before), 1)

    def test_reply_to_an_old_lead_message_without_links_still_finds_the_lead_by_its_event(self):
        lead = self._lead_with_sent_message('rp0')
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)
        lead.write({'zalo_message_ids': [(5, 0, 0)]})  # như lead tạo trước khi có liên kết tin ↔ lead
        self.assertFalse(lead.zalo_message_ids)
        before = self.channel.message_ids
        self._reply(original, 'đã gọi')
        self.assertEqual(lead.contact_state, 'contacted')
        self.assertEqual((self.channel.message_ids - before).text, 'Em xin cảm ơn ạ')

    def test_reply_to_a_summary_marks_every_number_in_it(self):
        first = self._lead_with_sent_message('rp2')
        first.received_at = self._vn('2026-11-12 08:30')  # nằm trước mốc 11:30 của tin tổng giả định
        second = self._lead_at('rp3', '2026-11-12 09:00')
        second.zalo_message_ids.write({'state': 'sent', 'external_message_id': 'zm-rp3'})
        other = self._lead_at('rp4', '2026-11-12 09:10')
        other.zalo_message_ids.write({'state': 'sent', 'external_message_id': 'zm-rp4'})
        before = self.channel.message_ids
        self.env['dac.web.lead']._send_summary(self._vn('2026-11-12 11:30'))
        summary = (self.channel.message_ids - before)
        summary.write({'state': 'sent', 'external_message_id': 'zm-summary'})
        self.assertEqual(first.summary_message_ids.filtered(lambda m: m.channel_id == self.channel), summary, 'the lead remembers which summary listed it')
        other._set_contact_state('unreachable')  # đã chốt từ trước: giữ nguyên, không bị ghi đè
        self._reply(summary, 'đã liên hệ hết rồi ạ')
        self.assertEqual((first.contact_state, second.contact_state, other.contact_state), ('contacted', 'contacted', 'unreachable'))

    def test_reply_to_a_reminder_and_unrelated_replies(self):
        self.env['dac.zalo.config']._config().write({'active': True, 'assistant_uid': 'bot-push'})
        lead, reminder = self._reminded_lead('rp5')
        before = self.channel.message_ids
        self._reply(reminder, 'chưa gọi được', '9101')
        self._reply(reminder, 'ok em xem', '9102')
        self.assertEqual((lead.contact_state, self.channel.message_ids), ('new', before), 'no intent: nothing happens, the bot stays quiet')
        stranger = self.channel._queue('Tin thường', 'unit:plain', self.env.ref('dac_push.event_type_web_lead_created'))
        stranger.write({'state': 'sent', 'external_message_id': 'zm-plain'})
        self._reply(stranger, 'đã gọi', '9103')
        self.assertEqual(self.channel.message_ids, before | stranger, 'a reply to a message that is not about a lead is ignored')
        self._reply(reminder, 'Đã gọi', '9104')
        self.assertEqual(lead.contact_state, 'contacted')

    def test_working_time_skips_lunch_nights_and_sundays(self):
        from datetime import datetime, timezone
        from ..models.working_time import TZ, add_working_minutes

        def due(start):
            utc = datetime.strptime(start, '%Y-%m-%d %H:%M').replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
            return add_working_minutes(utc, 30).replace(tzinfo=timezone.utc).astimezone(TZ).strftime('%a %H:%M')

        self.assertEqual(due('2026-11-12 09:00'), 'Thu 09:30')
        self.assertEqual(due('2026-11-12 11:50'), 'Thu 13:50', '10 minutes in the morning + 20 after lunch')
        self.assertEqual(due('2026-11-12 12:10'), 'Thu 14:00', 'lunch break is not counted')
        self.assertEqual(due('2026-11-12 17:20'), 'Fri 08:20')
        self.assertEqual(due('2026-11-12 20:00'), 'Fri 08:30', 'evening leads count from the next morning')
        self.assertEqual(due('2026-11-14 17:45'), 'Mon 08:30', 'Saturday is a working day, Sunday is not')
        self.assertEqual(due('2026-11-15 10:00'), 'Mon 08:30')

    def test_push_config_lists_lead_recipients_and_opens_the_picker_with_the_lead_event(self):
        config = self.env['dac.push.config']._config()
        self.assertIn(self.channel, config.lead_channel_ids)
        self.assertEqual(config.lead_channel_count, len(config.lead_channel_ids))
        self.channel.active = False
        self.assertIn(self.channel, config.lead_channel_ids, 'paused recipients are still listed')
        from unittest.mock import patch
        with patch.object(type(self.env['dac.zalo.config']), '_bridge_request', return_value={'threads': []}):
            action = config.action_add_lead_channel()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(wizard.event_codes, 'web_lead.created')
        self.assertEqual(config.action_open_lead_channels()['domain'], [('event_type_ids.code', '=', 'web_lead.created')])

    def test_duplicate_event_is_idempotent(self):
        first, _ = self.events._receive(envelope('dup'))
        second, created = self.events._receive(envelope('dup'))
        self.assertFalse(created)
        self.assertEqual(first, second)
        self.assertEqual(self.env['dac.web.lead'].search_count([('event_id', '=', first.id)]), 1)
        self.assertEqual(len(self.channel.message_ids), 1)

    def test_lead_keeps_entered_fields_visited_pages_and_source(self):
        visited = [{'url': 'https://duyan.vn/?utm_source=google', 'title': 'Trang chủ', 'at': '2026-11-11 10:00:00'},
                   {'url': 'https://duyan.vn/in-an', 'title': 'In ấn', 'at': '2026-11-11 10:04:00'},
                   {'url': 'https://example.org/ngoai', 'title': '', 'at': '2026-11-11 10:05:00'}]
        event, _ = self.events._receive(envelope(
            'rich1', name='Chị D', message='Cần giao trước thứ 6', page_title='Báo giá', page_url='https://duyan.vn/bao-gia',
            fields=[{'label': 'Dịch vụ cần làm', 'value': 'In bạt'}, {'label': 'Kích thước', 'value': '2x3m'},
                    {'label': 'Trống', 'value': ''}, 'rác', {'label': 'A\x00B', 'value': 'ok'}],
            visited=visited, source={'utm_source': 'google', 'utm_medium': 'cpc', 'utm_campaign': 'in-an'}))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertEqual(lead.extra_info.splitlines(), ['Dịch vụ cần làm: In bạt', 'Kích thước: 2x3m', 'AB: ok'])
        self.assertEqual(lead.visited_urls.splitlines(), ['10:00 — https://duyan.vn/?utm_source=google — Trang chủ', '10:04 — https://duyan.vn/in-an — In ấn', '10:05 — https://example.org/ngoai'])
        self.assertEqual((lead.source_info, lead.page_title, lead.phone_only), ('google / cpc / in-an', 'Báo giá', False))
        # Tin Zalo gọn: chi tiết (trang đã xem, nguồn...) nằm ở trang của lead, không lặp trong tin
        text = self.channel.message_ids.text
        self.assertEqual(text.splitlines(), [
            'Khách yêu cầu tư vấn', '👤 Chị D', '📞 0901234567',
            '➕ Yêu cầu: In bạt, Standee; Dịch vụ cần làm: In bạt; Kích thước: 2x3m; AB: ok',
            '💬 Cần giao trước thứ 6', 'Xem chi tiết: %s' % lead._short_link()])
        for absent in ('utm_', 'duyan.vn/in-an', 'Đã xem', 'Gửi từ'):
            self.assertNotIn(absent, text)

    def test_zalo_message_has_bold_title_and_short_link(self):
        event, _ = self.events._receive(envelope('fmt1'))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        text, styles = lead._zalo_message()
        self.assertEqual(styles, [{'start': 0, 'len': len('Khách yêu cầu tư vấn'), 'style': 'bold'}])
        self.assertEqual(text[:styles[0]['len']], 'Khách yêu cầu tư vấn')
        self.assertEqual(self.channel.message_ids.styles, styles)
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        self.assertTrue(text.endswith('Xem chi tiết: %s/lead/0901234567-%s' % (base.split('://')[-1], lead.sudo().access_token)),
                        'link = số điện thoại của lead + mã ngẫu nhiên')
        self.assertNotIn('/lead/%s' % lead.id, text, 'link does not use the guessable sequential id')
        self.assertNotIn('http', text.splitlines()[-1], 'link is shown without a scheme to stay short')

    def test_phone_only_form_is_valid_and_reads_cleanly(self):
        event, _ = self.events._receive({'event_id': 'ph1', 'event_type': 'web_lead.created', 'source': 'duyan.vn',
            'data': {'site': 'duyan.vn', 'form_type': 'callback', 'phone': '0955 666 777', 'page_url': 'https://duyan.vn/lien-he',
                     'visited': [{'url': 'https://duyan.vn/lien-he', 'title': 'Liên hệ', 'at': '2026-11-11 10:00:00'}]}})
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertTrue(lead.phone_only)
        self.assertEqual(self.channel.message_ids.text.splitlines(), ['Khách yêu cầu tư vấn', '📞 0955666777', 'Xem chi tiết: %s' % lead._short_link()])
        self.assertEqual(lead.visited_urls, '10:00 — https://duyan.vn/lien-he — Liên hệ')

    def test_product_url_fields_are_left_out_of_the_request_line(self):
        event, _ = self.events._receive(envelope(
            'prod1', items=[], message='', fields=[{'label': 'Sản phẩm/Danh mục', 'value': 'Standee'},
                                                    {'label': 'Trang sản phẩm', 'value': 'https://duyan.vn/standee'}]))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertIn('Trang sản phẩm: https://duyan.vn/standee', lead.extra_info)
        lines = self.channel.message_ids.text.splitlines()
        self.assertIn('➕ Yêu cầu: Sản phẩm/Danh mục: Standee', lines)
        self.assertFalse([ln for ln in lines if 'duyan.vn/standee' in ln])
        self.assertFalse([ln for ln in lines if ln.startswith('💬')], 'no note line when the customer wrote none')

    def test_full_visited_urls_are_kept_in_the_lead_even_when_long(self):
        long_url = 'https://duyan.vn/?gad_source=2&gad_campaignid=24120075564&gbraid=0AAAAACdEIguj0C' + 'x' * 200
        visited = [{'url': 'https://duyan.vn/p%s/?a=1&b=2#frag' % i, 'title': 'P%s' % i, 'at': '2026-11-11 10:%02d:00' % i} for i in range(14)]
        visited.append({'url': long_url, 'title': 'Dài', 'at': '2026-11-11 10:14:00'})
        event, _ = self.events._receive(envelope('long1', visited=visited, page_url='https://duyan.vn/bao-gia/?x=1&y=2'))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        lines = lead.visited_urls.splitlines()
        self.assertEqual(len(lines), 15)
        self.assertIn('https://duyan.vn/p0/?a=1&b=2#frag', lines[0])  # không cắt tham số / fragment
        self.assertIn(long_url, lines[14])
        self.assertLess(len(self.channel.message_ids.text), 500, 'Zalo message stays compact however long the visit history is')

    def test_junk_in_new_fields_never_breaks_the_event(self):
        event, _ = self.events._receive(envelope('junk1', fields='not-a-list', visited={'a': 1}, source='x'))
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertFalse(lead.extra_info or lead.visited_urls or lead.source_info)

    def test_endpoint_url_is_https_for_public_hosts_only(self):
        icp = self.env['ir.config_parameter'].sudo()
        config = self.env['dac.push.config']._config()
        for base, expected in (('http://crm.example.vn', 'https://crm.example.vn'), ('https://crm.example.vn/', 'https://crm.example.vn'),
                               ('http://localhost:8069', 'http://localhost:8069')):
            icp.set_param('web.base.url', base)
            config.invalidate_recordset(['endpoint_url'])
            self.assertEqual(config.endpoint_url, expected + '/dac_push/v1/events')

    def test_system_ping_is_processed_without_side_effects(self):
        event, _ = self.events._receive(envelope('ping1', 'system.ping'))
        self.assertEqual(event.state, 'processed')
        self.assertFalse(self.channel.message_ids)
        self.assertFalse(self.env['dac.web.lead'].search([('event_id', '=', event.id)]))

    def test_unknown_event_type_is_kept_but_ignored(self):
        event, _ = self.events._receive(envelope('u1', 'woo.order_paid'))
        self.assertEqual(event.state, 'ignored')
        self.assertIn('_handle_woo_order_paid', event.error_message)

    def test_future_event_types_plug_in_with_a_handler_method(self):
        handled = []
        type(self.events)._handle_unit_probe = lambda rec: handled.append(rec.payload)
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_probe'))
        event, _ = self.events._receive(envelope('p1', 'unit.probe', note='x'))
        self.assertEqual(event.state, 'processed')
        self.assertEqual(handled[0]['note'], 'x')

    def test_handler_failure_is_recorded_and_can_be_reprocessed(self):
        calls = {'n': 0}

        def flaky(rec):
            calls['n'] += 1
            if calls['n'] == 1:
                raise ValueError('boom')
        type(self.events)._handle_unit_flaky = flaky
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_flaky'))
        event, _ = self.events._receive(envelope('f1', 'unit.flaky'))
        self.assertEqual((event.state, event.error_message), ('failed', 'boom'))
        event.action_reprocess()
        self.assertEqual(event.state, 'processed')

    def test_failed_events_are_retried_automatically_then_alerted_once(self):
        from unittest.mock import patch
        from ..models.push_event import EVENT_RETRY_MINUTES
        calls = {'n': 0}

        def flaky(rec):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise ValueError('boom')
        type(self.events)._handle_unit_retry = flaky
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_retry'))
        event, _ = self.events._receive(envelope('rt-1', 'unit.retry'))
        self.assertEqual((event.state, event.attempts), ('failed', 1))
        self.assertTrue(event.next_attempt_at and event.next_attempt_at > fields.Datetime.now())
        self.events._cron_reprocess_failed()
        self.assertEqual(calls['n'], 1, 'not due yet: the cron leaves it alone')
        event.next_attempt_at = fields.Datetime.now() - timedelta(minutes=1)
        self.events._cron_reprocess_failed()
        self.assertEqual((event.state, event.attempts, calls['n']), ('failed', 2, 2), 'second failure schedules a later retry')
        event.next_attempt_at = fields.Datetime.now() - timedelta(minutes=1)
        self.events._cron_reprocess_failed()
        self.assertEqual((event.state, bool(event.next_attempt_at), calls['n']), ('processed', False, 3), 'the third try succeeds')
        # không bao giờ thành công: hết lượt thì dừng tự thử lại và cảnh báo đúng một lần
        type(self.events)._handle_unit_never = lambda rec: (_ for _ in ()).throw(ValueError('always'))
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_never'))
        self.events.search([('state', '=', 'failed')]).write({'alerted': True})  # lỗi cũ có sẵn trong DB thử
        alert_type = self.env.ref('dac_zalo_assistant.event_type_system_alert')
        alert_type.channel_ids = [(5, 0, 0)]
        stuck, _ = self.events._receive(envelope('rt-2', 'unit.never'))
        for _ in EVENT_RETRY_MINUTES:
            stuck.next_attempt_at = fields.Datetime.now() - timedelta(minutes=1)
            self.events._cron_reprocess_failed()
        self.assertEqual((stuck.state, stuck.attempts, bool(stuck.next_attempt_at)), ('failed', len(EVENT_RETRY_MINUTES) + 1, False))
        self.assertFalse(stuck.alerted, 'nobody asked for alerts yet: stays pending')
        alert_channel = self.env['dac.zalo.channel'].create({
            'name': 'Quản trị', 'thread_id': 'U-push-admin', 'thread_type': 'user', 'event_type_ids': [(6, 0, alert_type.ids)]})
        self.events._cron_reprocess_failed()
        self.assertEqual(len(alert_channel.message_ids), 1)
        self.assertIn('1 dữ liệu từ website chưa xử lý được', alert_channel.message_ids.text)
        self.events._cron_reprocess_failed()
        self.assertEqual(len(alert_channel.message_ids), 1, 'reported once')
        self.assertTrue(stuck.alerted)

    def test_hostile_text_is_cleaned_and_truncated(self):
        event, _ = self.events._receive(envelope('h1', name='A\x00\x07B' + 'x' * 500, message='m' * 5000))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertNotIn('\x00', lead.customer_name)
        self.assertEqual((len(lead.customer_name), len(lead.message)), (200, 2000))


@tagged('post_install', '-at_install')
class TestPushController(HttpCase):
    def setUp(self):
        super().setUp()
        self.config = self.env['dac.push.config']._config()
        self.config.sudo().write({'active': True, 'secret': SECRET})

    def post(self, body, headers):
        return self.url_open('/dac_push/v1/events', data=body, headers=headers)

    def test_signed_request_is_accepted_once(self):
        body = json.dumps(envelope('c1')).encode()
        first = self.post(body, sign(body))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'ok': True, 'duplicate': False, 'state': 'processed'})
        self.assertTrue(self.post(body, sign(body)).json()['duplicate'])

    def test_bad_signature_and_stale_timestamp_are_rejected(self):
        body = json.dumps(envelope('c2')).encode()
        tampered = sign(body)
        tampered['X-DAC-Signature'] = '0' * 64
        self.assertEqual(self.post(body, tampered).status_code, 401)
        self.assertEqual(self.post(body, sign(body, ts=int(time.time()) - 1000)).status_code, 401)
        self.assertEqual(self.post(body, {'Content-Type': 'application/json'}).status_code, 401)
        self.assertFalse(self.env['dac.push.event'].search([('event_id', '=', 'c2')]))

    def test_closed_when_disabled_or_without_secret(self):
        body = json.dumps(envelope('c3')).encode()
        self.config.sudo().active = False
        self.assertEqual(self.post(body, sign(body)).status_code, 503)
        self.config.sudo().write({'active': True, 'secret': False})
        self.assertEqual(self.post(body, sign(body)).status_code, 503)

    def test_malformed_bodies_are_400(self):
        for raw in (b'not json', b'[1,2]', json.dumps({'event_type': 'x'}).encode()):
            self.assertEqual(self.post(raw, sign(raw)).status_code, 400)

    def test_short_lead_link_redirects_to_the_lead_form_after_login(self):
        body = json.dumps(envelope('link1')).encode()
        self.post(body, sign(body))
        lead = self.env['dac.web.lead'].search([], order='id desc', limit=1)
        anonymous = self.url_open('/lead/%d' % lead.id, allow_redirects=False)
        self.assertEqual(anonymous.status_code, 303)
        self.assertIn('/web/login', anonymous.headers['Location'], 'anonymous visitors are sent to the login page, never shown the lead')
        self.authenticate('admin', 'admin')
        response = self.url_open('/lead/%d' % lead.id, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers['Location'].split('?')[0].rsplit('/odoo/', 1)[-1], 'action-dac_push.web_lead_action/%d' % lead.id)


    def _lead_with_token(self, event_id):
        body = json.dumps(envelope(event_id, name='Chị Hoa', phone='0933939856', message='Gọi giúp em <b>gấp</b>')).encode()
        self.post(body, sign(body))
        return self.env['dac.web.lead'].search([], order='id desc', limit=1)

    def test_public_link_shows_only_the_lead_without_login(self):
        lead = self._lead_with_token('pub1')
        token = lead.sudo().access_token
        self.assertTrue(token and not token.isdigit())
        page = self.url_open('/lead/%s' % token)
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertTrue(html.startswith('<!DOCTYPE html>'))
        self.assertEqual(html.count('DOCTYPE'), 1)
        self.assertIn('0933939856', html)
        self.assertIn('Chị Hoa', html)
        self.assertIn('Đã liên hệ', html)
        self.assertIn('Liên hệ không được', html)
        self.assertNotIn('disabled="disabled"', html, 'undecided leads keep both buttons active')
        self.assertNotIn('<b>gấp</b>', html, 'customer text is escaped')
        self.assertNotIn('/web/assets', html, 'standalone page, no Odoo backend assets')
        self.assertIn('noindex', page.headers['X-Robots-Tag'])

    def test_public_buttons_set_contact_state(self):
        lead = self._lead_with_token('pub2')
        token = lead.sudo().access_token
        self.assertEqual(lead.contact_state, 'new')
        done = self.url_open('/lead/%s/contact' % token, data={'result': 'contacted'}, allow_redirects=False)
        self.assertEqual(done.status_code, 303)
        lead.invalidate_recordset()
        self.assertEqual((lead.contact_state, bool(lead.contact_state_at)), ('contacted', True))
        self.assertIn('Đã liên hệ —', self.url_open('/lead/%s' % token).text)

    def test_decided_lead_has_locked_buttons_and_ignores_a_second_click(self):
        lead = self._lead_with_token('pub2b')
        token = lead.sudo().access_token
        self.url_open('/lead/%s/contact' % token, data={'result': 'unreachable'})
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'unreachable')
        first_at = lead.contact_state_at
        html = self.url_open('/lead/%s' % token).text
        self.assertEqual(html.count('disabled="disabled"'), 2, 'both buttons are locked once someone decided')
        self.assertIn('Liên hệ không được —', html)
        # bấm đúp / gửi lại / mở link cũ rồi bấm: giữ nguyên quyết định đầu tiên
        self.url_open('/lead/%s/contact' % token, data={'result': 'contacted'})
        lead.invalidate_recordset()
        self.assertEqual((lead.contact_state, lead.contact_state_at), ('unreachable', first_at))

    def test_link_slug_starts_with_the_phone_and_old_token_only_links_still_work(self):
        lead = self._lead_with_token('slug1')
        token = lead.sudo().access_token
        self.assertEqual(lead._link_slug(), '0933939856-%s' % token)
        page = self.url_open('/lead/0933939856-%s' % token)
        self.assertEqual(page.status_code, 200)
        self.assertIn('/lead/0933939856-%s/contact' % token, page.text, 'the buttons post to the same link the visitor used')
        self.assertEqual(self.url_open('/lead/%s' % token).status_code, 200, 'links sent before the change keep working')
        short = self.url_open('/l/%s' % token)
        self.assertEqual(short.status_code, 200, 'the compact link used in summaries opens the same page')
        self.assertIn('Chị Hoa', short.text)
        self.assertEqual(self.url_open('/l/wrongtoken').status_code, 404)
        alone = self.url_open('/lead/0933939856', allow_redirects=False)  # chỉ có số = route cũ theo số thứ tự: đòi đăng nhập
        self.assertIn('/web/login', alone.headers.get('Location', ''), 'the phone alone never shows the lead')
        self.assertEqual(self.url_open('/lead/0933939856-wrongtoken').status_code, 404)
        done = self.url_open('/lead/0933939856-%s/contact' % token, data={'result': 'contacted'}, allow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertTrue(done.headers['Location'].endswith('/lead/0933939856-%s?done=1' % token))
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'contacted')

    def test_slug_normalises_phones_and_skips_unusable_ones(self):
        lead = self._lead_with_token('slug2')
        token = lead.sudo().access_token
        for phone, expected in (('+84933939856', '0933939856'), ('0933 939 856', '0933939856'), ('123', None), ('', None)):
            lead.sudo().phone = phone
            self.assertEqual(lead._link_slug(), '%s-%s' % (expected, token) if expected else token, phone)

    def test_public_link_rejects_unknown_tokens_and_bad_results(self):
        lead = self._lead_with_token('pub3')
        token = lead.sudo().access_token
        self.assertEqual(self.url_open('/lead/does-not-exist').status_code, 404)
        self.assertEqual(self.url_open('/lead/does-not-exist/contact', data={'result': 'contacted'}).status_code, 404)
        self.assertEqual(self.url_open('/lead/%s/contact' % token, data={'result': 'hack'}).status_code, 400)
        self.assertEqual(self.url_open('/lead/%s/contact' % token, data={'other': '1'}).status_code, 400)
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'new')

    def test_logged_in_staff_are_sent_to_the_full_form(self):
        lead = self._lead_with_token('pub4')
        self.authenticate('admin', 'admin')
        response = self.url_open('/lead/%s' % lead.sudo().access_token, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertTrue(response.headers['Location'].endswith('action-dac_push.web_lead_action/%d' % lead.id))

    def test_old_leads_without_token_get_one_on_demand_and_tokens_are_unique(self):
        lead = self._lead_with_token('pub5')
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = NULL WHERE id = %s', [lead.id])
        lead.invalidate_recordset()
        token = lead._get_access_token()
        self.assertTrue(token)
        self.assertEqual(lead._get_access_token(), token, 'stable once issued')
        other = self._lead_with_token('pub6')
        self.assertNotEqual(other.sudo().access_token, token)

    def test_migration_gives_every_old_lead_its_own_token(self):
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / 'migrations' / '18.0.1.2.1' / 'pre-migration.py'
        spec = importlib.util.spec_from_file_location('dac_push_pre_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first, second = self._lead_with_token('mig1'), self._lead_with_token('mig2')
        # DB thật 1.2.0 không có ràng buộc này (không tạo được); DDL của PostgreSQL rollback cùng giao dịch test
        self.env.cr.execute('ALTER TABLE dac_web_lead DROP CONSTRAINT IF EXISTS dac_web_lead_access_token_unique')
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = %s WHERE id IN %s', ['SHARED-TOKEN', (first.id, second.id)])
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = NULL WHERE id = %s', [self._lead_with_token('mig3').id])
        module.migrate(self.env.cr, '18.0.1.2.0')
        self.env.cr.execute('SELECT count(*), count(DISTINCT access_token), count(access_token) FROM dac_web_lead')
        total, distinct, filled = self.env.cr.fetchone()
        self.assertEqual((total, filled), (total, total), 'no lead is left without a token')
        self.assertEqual(distinct, total, 'every token is unique')
