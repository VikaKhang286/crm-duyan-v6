import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from odoo.addons.dac_pancake_remind.models import zalo_out
from odoo.addons.dac_pancake_remind.models.staff import name_score

from .test_remind import CUSTOMER_ID, FETCH, PAGE_ID, T0, jpeg, reset_scenarios

AI = 'odoo.addons.dac_ai_core.models.ai_connection.AiConnection._json'
AI_READY = 'odoo.addons.dac_ai_core.models.ai_connection.AiConnection._ready'
RENDER = 'odoo.addons.dac_pancake_remind.models.remind_wait.PancakeRemindWait._render_image'


def no_ai(*args, **kwargs):
    raise UserError('AI tắt')


class Common:
    @classmethod
    def setup_common(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env['dac.ai.connection'].search([]).write({'active': False})  # test không gọi AI thật (dùng luật hoặc giả lập)
        cls.page = cls.env['dac.pancake.page'].create({'name': 'Page thử', 'page_fm_id': PAGE_ID})
        cls.config = cls.env['dac.pancake.remind.config']._get()
        cls.config.write({'active': True, 'remind_real_channels': False, 'page_ids': [(6, 0, cls.page.ids)]})
        reset_scenarios(cls.env)
        cls.config.activated_at = T0 - timedelta(days=3)
        cls.event_type = cls.env.ref('dac_pancake_remind.event_type_pancake_unanswered')
        Channel, Member = cls.env['dac.zalo.channel'], cls.env['dac.zalo.channel.member']
        cls.real = Channel.create({'name': 'DAC.Kinh Doanh', 'thread_id': 'G-real', 'thread_type': 'group', 'event_type_ids': [(6, 0, cls.event_type.ids)]})
        cls.test = Channel.create({'name': 'Beework Test', 'thread_id': 'G-test', 'thread_type': 'group', 'is_test': True,
                                   'event_type_ids': [(6, 0, cls.event_type.ids)]})
        cls.tbang = Member.create({'channel_id': cls.real.id, 'uid': '333', 'name': 'Tbăng'})
        cls.dao = Member.create({'channel_id': cls.real.id, 'uid': '555', 'name': 'Đào Duy An'})
        an = Member.create({'channel_id': cls.real.id, 'uid': '111', 'name': 'An'})
        kiet = Member.create({'channel_id': cls.test.id, 'uid': '444', 'name': 'Kiệt'})
        Mention = cls.env['dac.zalo.channel.mention']
        Mention.create({'channel_id': cls.real.id, 'event_type_id': cls.event_type.id, 'member_ids': [(6, 0, an.ids)]})
        Mention.create({'channel_id': cls.test.id, 'event_type_id': cls.event_type.id, 'member_ids': [(6, 0, kiet.ids)]})
        cls.Wait = cls.env['dac.pancake.remind.wait']
        cls.Appointment = cls.env['dac.pancake.appointment']

    def short(self, fm_id):
        """Link rút gọn của hội thoại (một link gốc luôn cho cùng một mã)."""
        return self.env['dac.short.link']._shorten('https://pancake.vn/%s?c_id=%s' % (PAGE_ID, fm_id), kind='p')

    def conversation(self, name='Nguyễn Hoa', fm_id='c1'):
        return self.env['dac.pancake.conversation'].create({
            'conversation_fm_id': fm_id, 'page_id': self.page.id, 'customer_name': name, 'platform': 'Zalo'})

    def message(self, conversation, at, who='customer', text='Cho mình hỏi giá', staff='Tiểu Băng', type_content='text', attachments=None):
        sender = {'id': CUSTOMER_ID, 'name': conversation.customer_name}
        if who == 'staff':
            sender = {'id': PAGE_ID, 'name': 'Duy An', 'admin_id': 'a1', 'admin_name': staff}
        self._seq = getattr(self, '_seq', 0) + 1
        raw = {'id': 'm%s-%s' % (at.timestamp(), self._seq), 'type': 'INBOX', 'page_id': PAGE_ID, 'from': sender, 'attachments': attachments or []}
        return self.env['dac.pancake.message'].create({
            'message_fm_id': raw['id'], 'conversation_id': conversation.id, 'content_html': '<div>%s</div>' % text, 'type_content': type_content,
            'inserted_at': at, 'raw_json': json.dumps(raw), 'sender_name': sender.get('admin_name') or sender.get('name')})

    def scan(self, now, ai=no_ai):
        with patch.object(fields.Datetime, 'now', return_value=now), patch(RENDER, return_value=jpeg()), patch(FETCH, return_value=True), \
             patch(AI, side_effect=ai), patch(AI_READY, return_value=True):
            self.Wait._cron_scan()

    def followup(self, now, ai=no_ai):
        with patch.object(fields.Datetime, 'now', return_value=now), patch(FETCH, return_value=True), patch(AI, side_effect=ai) as mock:
            self.Appointment._cron_followup()
        return mock


@tagged('post_install', '-at_install')
class TestFollowup(Common, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_common()

    # ── Giờ làm việc, mức gấp ────────────────────────────────────────────────

    def test_working_time_helpers(self):
        self.assertEqual(zalo_out.working_minutes_between(T0, T0 + timedelta(hours=2)), 120)
        self.assertEqual(zalo_out.working_minutes_between(T0 + timedelta(hours=1), T0 + timedelta(hours=4)), 90, '11:00 → 14:00 trừ nghỉ trưa')
        overnight = datetime(2026, 11, 9, 15, 0)  # 22:00 thứ hai
        self.assertEqual(zalo_out.due_after(overnight, 10), datetime(2026, 11, 10, 1, 30), 'nhắn đêm: nhắc riêng từ 8:30')
        self.assertEqual(zalo_out.due_after(datetime(2026, 11, 10, 0, 55), 30), datetime(2026, 11, 10, 1, 30), '7:55 + 30 phút làm việc = 8:30')
        self.assertEqual(zalo_out.due_after(datetime(2026, 11, 10, 0, 0), 60), datetime(2026, 11, 10, 2, 0), 'không kéo lùi hạn đã sau 8:30')
        self.assertEqual(zalo_out.due_after(datetime(2026, 11, 10, 5, 10), 10), datetime(2026, 11, 10, 6, 40), 'nghỉ trưa không áp 8:30')
        self.assertEqual([zalo_out.escalate('normal', 61), zalo_out.escalate('high', 125), zalo_out.escalate('none', 500), zalo_out.escalate('urgent', 0)],
                         ['high', 'urgent', 'none', 'urgent'])
        self.assertEqual([zalo_out.waited_text(m) for m in (5, 60, 75)], ['5 phút', '1 giờ', '1 giờ 15 phút'])

    # ── Ghép nhân viên Pancake ↔ Zalo ────────────────────────────────────────

    def test_name_matching(self):
        self.assertEqual(name_score('Dao', 'Đào Duy An'), 3)
        self.assertEqual(name_score('Tieu Bang', 'Tbăng'), 2)
        self.assertEqual(name_score('Tiểu Băng', 'Tiểu Băng'), 4)
        self.assertEqual(name_score('Tieu Bang', 'Đào Duy An'), 0)

    def returning_customer(self, staff='Tieu Bang', text='Cho anh hỏi giá in 200 cái'):
        conv = self.conversation(name='Long Nguyễn', fm_id='c-old')
        self.message(conv, T0 - timedelta(hours=30), 'staff', text='Dạ em gửi giá anh', staff=staff)
        self.message(conv, T0, text=text)
        return conv

    def test_refresh_suggests_zalo_members_and_digest_tags_the_staff_who_chatted(self):
        self.config.remind_real_channels = True
        conv = self.returning_customer()
        Staff = self.env['dac.pancake.staff']
        Staff.action_refresh()
        tieu_bang = Staff.search([('key', '=', 'tieu bang')])
        self.assertEqual((tieu_bang.member_id, tieu_bang.zalo_uid, tieu_bang.suggested), (self.tbang, '333', True))
        tieu_bang.action_confirm()
        self.assertFalse(tieu_bang.suggested)
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=31))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.customer_kind, wait.staff_id, wait.state), ('old', tieu_bang, 'done'))
        digest = self.real.message_ids
        self.assertEqual(digest.text.splitlines()[:2], ['🗂 1 khách đang chờ trả lời', '@Tbăng'])
        self.assertEqual(digest.mentions, [{'pos': len('🗂 1 khách đang chờ trả lời\n') + 1, 'len': 6, 'uid': '333'}])
        self.assertFalse(self.test.message_ids)

    def test_trial_mode_writes_staff_names_without_tagging_people_outside_the_test_group(self):
        conv = self.returning_customer()
        self.env['dac.pancake.staff'].action_refresh()
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=31))
        self.assertFalse(self.real.message_ids, 'chưa duyệt: không gửi nhóm thật')
        digest = self.test.message_ids
        self.assertIn('\n@Tbăng\n', digest.text)
        self.assertFalse(digest.mentions, 'Tbăng không ở nhóm thử: chỉ ghi tên, không tag')
        self.assertIn(self.short('c-old'), digest.text)
        self.assertTrue(conv)

    def test_urgent_old_customer_gets_its_own_image_reminder(self):
        self.config.remind_real_channels = True
        conv = self.returning_customer(text='Anh chuyển khoản rồi nhé, xác nhận giúp anh')
        self.env['dac.pancake.staff'].action_refresh()

        def ai(system, text, schema, name='report', **kwargs):
            self.assertEqual(name, 'report_wait')
            self.assertIn('KHÁCH CŨ', text)
            return {'need_reply': True, 'level': 'urgent', 'summary': 'Đã chuyển khoản, chờ xác nhận', 'reason': 'Chờ xác nhận tiền'}, 'openai'
        self.scan(T0 + timedelta(minutes=2), ai)
        self.scan(T0 + timedelta(minutes=31), ai)
        message = self.real.message_ids
        self.assertEqual(len(message), 1)
        self.assertTrue(message.image)
        self.assertEqual(message.text, '@Tbăng 🚨 KHẨN · Khách cũ chờ 31 phút\nĐã chuyển khoản, chờ xác nhận\n' + self.short('c-old'))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.level, wait.ai_source, wait.remind_count), ('done', 'urgent', 'openai', 1))

    def test_customers_left_from_yesterday_go_into_the_digest_not_one_image_each(self):
        self.config.remind_real_channels = True
        conv = self.conversation(name='Khách tối qua', fm_id='c-night')
        self.message(conv, T0 - timedelta(days=2), 'staff', text='Dạ em gửi giá anh')
        self.message(conv, T0 - timedelta(hours=14), text='Mẫu này làm được không em')  # 20:00 hôm trước
        self.scan(T0 - timedelta(hours=14) + timedelta(minutes=5))
        self.scan(T0 + timedelta(minutes=5))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.level), ('done', 'high'))
        message = self.real.message_ids
        self.assertEqual(len(message), 1)
        self.assertFalse(message.image, 'chờ lâu chỉ làm icon thành 🚨 trong tin gom, không tách ảnh riêng')
        self.assertIn('🚨 Khách tối qua: Mẫu này làm được không em (Tiểu Băng) · chờ 2 giờ 5 phút', message.text)

    def test_scenario_templates_toggles_and_event_log(self):
        from odoo.exceptions import ValidationError
        self.config.remind_real_channels = True
        first = self.env.ref('dac_pancake_remind.scenario_new_first')
        with self.assertRaises(ValidationError):
            first.template = '{icon} {khong_co_bien_nay}'
        with self.assertRaises(ValidationError):
            first.template = '{icon chưa đóng'
        first.template = '{icon} Khách {ten_khach} chờ {cho}\n{link}'
        self.assertIn('@Tbăng ⚠️ Khách Nguyễn Hoa chờ 12 phút', first.preview)
        conv = self.conversation(name='Khách Mẫu', fm_id='c-tpl')
        self.message(conv, T0, text='Shop ơi')
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=11))
        message = self.real.message_ids
        self.assertEqual(message.text, '@An ⚠️ Khách Khách Mẫu chờ 11 phút\n' + self.short('c-tpl'))
        event = self.env['dac.pancake.remind.event'].search([('conversation_id', '=', conv.id)])
        self.assertEqual((event.scenario_id, event.level, event.channel_names, event.message_ids), (first, 'high', 'DAC.Kinh Doanh', message))
        self.assertEqual(first.sent_count, 1)
        first.action_reset_template()
        self.assertEqual(first.template, first.default_template)
        # Tắt tin gom: khách cũ không khẩn không được nhắc
        self.env.ref('dac_pancake_remind.scenario_digest').enabled = False
        old = self.returning_customer()
        self.scan(T0 + timedelta(minutes=12))
        self.scan(T0 + timedelta(minutes=31))
        self.assertEqual(self.Wait.search([('conversation_id', '=', old.id)]).state, 'ignored')
        self.assertEqual(len(self.real.message_ids), 1)
        # Quản lý chỉ được bật/tắt, không sửa mẫu tin
        manager = self.env['res.users'].create({'name': 'QL', 'login': 'ql-scenario', 'groups_id': [(6, 0, [
            self.env.ref('base.group_user').id, self.env.ref('dac_erp.group_dac_erp_manager').id])]})
        first.with_user(manager).enabled = False
        with self.assertRaises(UserError):
            first.with_user(manager).template = 'x'

    def test_ai_not_needed_and_rules_fallback_levels(self):
        from odoo.addons.dac_pancake_remind.models import reply_check
        self.assertEqual(reply_check.rule_assess([('customer', 'Sao chưa giao hàng cho chị vậy', '')], 'old')['level'], 'urgent')
        self.assertEqual(reply_check.rule_assess([('customer', 'Xin chào', '')], 'new')['level'], 'high')
        self.assertEqual(reply_check.rule_assess([('customer', 'Cuối tuần shop mở cửa không', '')], 'old')['level'], 'normal')
        self.assertEqual(reply_check.rule_assess([('staff', 'gửi giá', ''), ('customer', 'ok em', '')], 'old')['level'], 'none')
        result = reply_check.assess(self.env, [('customer', 'Mua ngay', '')], 'new', use_ai=False)
        self.assertEqual(result['source'], 'rules')

    # ── Lịch hẹn ─────────────────────────────────────────────────────────────

    def promise_ai(self, task='Gửi báo giá bạt 180×80', precision='time', due='2026-11-10 15:00', line=1, fulfilled=False, check=False):
        calls = []

        def ai(system, text, schema, name='report', **kwargs):
            calls.append(name)
            if name == 'report_promises':
                return {'promises': [{'line': line, 'task': task, 'precision': precision, 'due': due, 'fulfilled': fulfilled}]}, 'openai'
            if name == 'report_promise_check':
                return {'fulfilled': check, 'reason': 'Đã gửi ảnh báo giá' if check else 'Chưa thấy'}, 'openai'
            raise UserError('không dùng')
        ai.calls = calls
        return ai

    def test_live_promise_creates_ticket_reminds_before_and_checks_after(self):
        self.followup(T0 - timedelta(hours=1))  # lần đầu: chỉ đặt mốc, không đọc lịch sử
        conv = self.conversation()
        self.message(conv, T0, text='Báo giá giúp anh bạt 180x80')
        self.message(conv, T0 + timedelta(minutes=1), 'staff', text='Dạ vâng ạ')  # không có dấu hiệu hẹn
        self.message(conv, T0 + timedelta(minutes=2), 'staff', text='Dạ 15h em gửi báo giá anh nhé')
        ai = self.promise_ai(line=2)
        self.followup(T0 + timedelta(minutes=5), ai)
        self.assertEqual(ai.calls, ['report_promises'])
        appointment = self.Appointment.search([('conversation_id', '=', conv.id)])
        self.assertEqual((appointment.task, appointment.precision, appointment.due_at, appointment.check_at, appointment.state, appointment.source),
                         ('Gửi báo giá bạt 180×80', 'time', datetime(2026, 11, 10, 8, 0), datetime(2026, 11, 10, 8, 30), 'open', 'live'))
        self.assertEqual((appointment.staff_id.name, appointment.quote), ('Tiểu Băng', 'Dạ 15h em gửi báo giá anh nhé'))
        self.followup(T0 + timedelta(minutes=15), ai)
        self.assertEqual(self.Appointment.search_count([('conversation_id', '=', conv.id)]), 1, 'không tạo trùng')

        self.followup(datetime(2026, 11, 10, 7, 31), ai)  # 14:31: nhắc trước 30 phút
        pre = self.test.message_ids
        self.assertFalse(self.real.message_ids, 'chạy thử: chỉ nhóm thử nhận')
        self.assertEqual(pre.text, '@Kiệt ⏰ 15:00 hẹn khách Nguyễn Hoa: Gửi báo giá bạt 180×80\n%s' % appointment._short_ticket())
        self.assertEqual([m['uid'] for m in pre.mentions], ['444'], 'chưa ghép nhân viên với Zalo: tag người được cài')
        pre.write({'state': 'sent', 'external_message_id': 'z-pre'})
        self.followup(datetime(2026, 11, 10, 7, 45), ai)
        self.assertEqual(len(self.test.message_ids), 1, 'chỉ nhắc trước giờ hẹn một lần')

        self.followup(datetime(2026, 11, 10, 8, 31), ai)  # 15:31: chưa có tin nào của nhân viên → chưa làm, không cần hỏi AI
        post = self.test.message_ids - pre
        self.assertEqual(post.reply_to_id, pre)
        self.assertEqual(post.text, '@Kiệt ⏰ Quá hẹn 31 phút: Nguyễn Hoa — Gửi báo giá bạt 180×80\nĐã xử lý thì bấm xác nhận: %s' % appointment._short_ticket())
        self.assertEqual((appointment.followup_count, appointment.check_at, appointment.state), (1, datetime(2026, 11, 10, 9, 31), 'open'))
        self.assertNotIn('report_promise_check', ai.calls)

        self.message(conv, datetime(2026, 11, 10, 8, 40), 'staff', text='', type_content='photo',
                     attachments=[{'type': 'photo', 'url': 'https://example.com/bao-gia.jpg'}])
        done_ai = self.promise_ai(check=True)
        self.followup(datetime(2026, 11, 10, 9, 32), done_ai)
        self.assertEqual((appointment.state, appointment.done_by, appointment.check_note), ('auto_done', 'AI', 'Đã gửi ảnh báo giá'))
        self.assertEqual(len(self.test.message_ids), 2)

    def test_reminders_stop_after_three_and_after_staff_confirms(self):
        conv = self.conversation()
        staff_msg = self.message(conv, T0, 'staff', text='Chiều em gọi lại anh nhé')
        appointment = self.Appointment.create({'conversation_id': conv.id, 'task': 'Gọi lại khách', 'promised_at': T0,
                                               'message_fm_id': staff_msg.message_fm_id, 'due_at': T0 + timedelta(hours=1),
                                               'check_at': T0 + timedelta(hours=1, minutes=30), 'precision': 'part'})
        for hours in (2, 3, 4, 5, 6):
            self.followup(T0 + timedelta(hours=hours))
        self.assertEqual((appointment.followup_count, appointment.state), (3, 'overdue'))
        self.assertEqual(len(self.test.message_ids), 3)
        other = self.Appointment.create({'conversation_id': conv.id, 'task': 'Gửi mẫu', 'promised_at': T0, 'due_at': T0,
                                         'check_at': T0 + timedelta(minutes=30)})
        other._close('done', 'Qua phiếu hẹn — Bảo')
        self.followup(T0 + timedelta(hours=7))
        self.assertEqual((other.state, other.done_by, len(self.test.message_ids)), ('done', 'Qua phiếu hẹn — Bảo', 3))

    def test_ai_due_parsing(self):
        promised = T0
        self.assertEqual(self.Appointment._schedule('time', '2026-11-10 15:00', promised)[:2], ('time', datetime(2026, 11, 10, 8, 0)))
        self.assertEqual(self.Appointment._schedule('day', '2026-11-11', promised)[:2], ('day', datetime(2026, 11, 11, 9, 0)), 'trong ngày: 16:00')
        self.assertEqual(self.Appointment._schedule('time', '2026-11-11', promised)[0], 'day', 'AI nói có giờ nhưng chỉ đưa ngày')
        vague = self.Appointment._schedule('vague', '', promised)
        self.assertEqual(vague[:2], ('vague', T0 + timedelta(hours=2)), 'không rõ giờ: 2 giờ làm việc')
        self.assertEqual(self.Appointment._schedule('time', '2027-06-01 10:00', promised)[0], 'vague', 'hạn quá 30 ngày: không tin')
        self.assertEqual(vague[2], datetime(2026, 11, 10, 7, 0), 'hạn 12:00 + 30 phút làm việc (qua nghỉ trưa) = 14:00')

    def test_morning_review_reads_yesterday_and_sends_one_list_at_8(self):
        monday = datetime(2026, 11, 9, 9, 0)  # 16:00 thứ hai
        conv = self.conversation(name='Khách B', fm_id='c-b')
        self.message(conv, monday - timedelta(minutes=5), text='Em cần mẫu bảng hiệu')
        self.message(conv, monday, 'staff', text='Dạ mai em gửi mẫu thiết kế cho chị')
        customer_only = self.conversation(name='Chỉ khách nhắn', fm_id='c-only')
        self.message(customer_only, monday, text='alo')
        ai = self.promise_ai(task='Gửi mẫu thiết kế', precision='day', due='2026-11-10')
        self.followup(datetime(2026, 11, 10, 0, 0), ai)  # 7:00 thứ ba: đọc lại hôm qua
        self.assertEqual(ai.calls, ['report_promises'], 'chỉ đọc hội thoại có nhân viên nhắn')
        appointment = self.Appointment.search([('conversation_id', '=', conv.id)])
        self.assertEqual((appointment.source, appointment.precision, appointment.due_at), ('morning', 'day', datetime(2026, 11, 10, 9, 0)))
        self.assertTrue(self.env['dac.pancake.followup.state']._get().review_done)
        self.assertFalse(self.test.message_ids, 'chưa tới 8:00')
        self.followup(datetime(2026, 11, 10, 1, 1), ai)  # 8:01
        message = self.test.message_ids
        self.assertEqual(message.text.splitlines(), [
            '📋 Nhắc việc từ hôm qua (10/11)', 'Chưa rõ người phụ trách: @Kiệt',
            '🗓 16:00 Khách B: Gửi mẫu thiết kế (Tiểu Băng) — %s' % appointment._short_ticket()])
        self.assertTrue(appointment.in_morning_list)
        self.followup(datetime(2026, 11, 10, 1, 11), ai)
        self.assertEqual(len(self.test.message_ids), 1, 'mỗi ngày một tin')


@tagged('post_install', '-at_install')
class TestAppointmentTicket(Common, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_common()

    def test_ticket_page_and_done_button(self):
        conv = self.conversation()
        appointment = self.Appointment.create({'conversation_id': conv.id, 'task': 'Gửi báo giá <b>bạt</b>', 'promised_at': T0, 'due_at': T0})
        response = self.url_open('/hen/%s' % appointment.access_token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Gửi báo giá &lt;b&gt;bạt&lt;/b&gt;', response.text)
        self.assertIn('Đã xử lý xong', response.text)
        self.assertEqual(self.url_open('/hen/khong-co').status_code, 404)
        response = self.url_open('/hen/%s/done' % appointment.access_token, data={'result': 'done', 'by': 'Bảo'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Đã lưu', response.text)
        self.assertEqual((appointment.state, appointment.done_by), ('done', 'Qua phiếu hẹn — Bảo'))
        self.assertEqual(self.url_open('/hen/%s/done' % appointment.access_token, data={'result': 'xoa'}).status_code, 400)
