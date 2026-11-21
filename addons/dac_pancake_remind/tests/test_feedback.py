from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.dac_pancake_remind.models.group_feedback import NO_CUSTOMER_TEXT, rule_intent

from .test_followup import Common
from .test_remind import T0

REACT = 'odoo.addons.dac_zalo_assistant.models.channels.ZaloChannel._react'


@tagged('post_install', '-at_install')
class TestGroupFeedback(Common, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_common()
        cls.env['dac.pancake.remind.scenario']._get('new_first').real_channels = True
        cls.env['dac.pancake.remind.scenario']._get('new_repeat').repeat_minutes = 30

    def reminded(self, name='Phạm Hằng', fm_id='c-fb'):
        """Khách mới được nhắc (ảnh) vào nhóm thật; trả về (hội thoại, lượt nhắc, tin ảnh đã gửi)."""
        conv = self.conversation(name=name, fm_id=fm_id)
        self.message(conv, T0, text='Cho chị hỏi giá biển')
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=11))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        image = wait.zalo_message_ids
        image.write({'state': 'sent', 'external_message_id': 'z-img-%s' % fm_id, 'sent_at': T0 + timedelta(minutes=11)})
        return conv, wait, image

    def process(self, now):
        with patch.object(fields.Datetime, 'now', return_value=now), patch(REACT, return_value=True) as react, \
             patch('odoo.addons.dac_pancake_remind.models.remind_wait.PancakeRemindWait._live_sync'):
            self.env['dac.pancake.feedback']._cron_process()
        return react

    def test_rules(self):
        texts = ('Đã xem', 'ok', 'Em đã rep khách rồi', 'Đã gọi', 'gọi rồi', 'để em xử lý', 'Chị ơi', 'Em lên đơn rồi ạ', 'đã chốt',
                 'Khách qua cửa hàng rồi', 'em nhắn zalo riêng rồi', 'em gọi điện cho khách rồi')
        self.assertEqual([rule_intent(t) for t in texts], ['seen', 'seen', 'replied', 'contacted', 'contacted', 'handling', 'other', 'order',
                                                         'order', 'contacted', 'contacted', 'contacted'])

    def test_seen_only_gets_a_heart_and_a_nudge_in_the_same_thread(self):
        conv, wait, image = self.reminded()
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9001', 'text': 'Đã xem'})
        feedback = self.env['dac.pancake.feedback'].search([('wait_id', '=', wait.id)])
        self.assertEqual((feedback.kind, feedback.state, feedback.sender_name, feedback.quoted_message_id), ('reply', 'pending', 'Đào Duy An', image))
        react = self.process(T0 + timedelta(minutes=15))
        react.assert_called_once_with('9001', 'heart')
        self.assertEqual((feedback.state, feedback.intent, feedback.reacted, feedback.pancake_answered), ('done', 'seen', True, False))
        bot = feedback.bot_message_id
        self.assertEqual(bot.text, 'Dạ vâng ạ, chị xem xong nhớ phản hồi khách Phạm Hằng ngay, đừng để khách chờ nhé!')
        self.assertEqual((bot.quote_message_id, bot.channel_id), ('9001', self.real), 'trả lời đúng tin nhân viên, trong nhóm thật')
        self.assertEqual(wait.state, 'waiting', 'chưa trả lời khách: vẫn kiểm tra lại theo lịch')
        self.assertEqual(feedback.conversation_id, conv)
        # cùng tin gửi lại (bridge thử lại) không ghi trùng
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9001', 'text': 'Đã xem'})
        self.assertEqual(self.env['dac.pancake.feedback'].search_count([('wait_id', '=', wait.id)]), 1)

    def test_mention_maps_to_the_customer_and_pancake_reply_closes_it(self):
        conv, wait, image = self.reminded()
        self.known('dao', self.u_bong, self.dao)
        self.message(conv, T0 + timedelta(minutes=13), 'staff', text='Dạ chị gửi kích thước giúp em', staff='Dao')
        with patch.object(fields.Datetime, 'now', return_value=T0 + timedelta(minutes=14)):
            self.real._on_mention({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9101', 'text': 'Đã xem'})
        feedback = self.env['dac.pancake.feedback'].search([('zalo_message_id', '=', '9101')])
        self.assertEqual((feedback.kind, feedback.wait_id, feedback.user_id), ('mention', wait, self.u_bong), 'tin "@Trợ lý" ghép với khách vừa nhắc')
        self.process(T0 + timedelta(minutes=15))
        self.assertEqual((wait.state, wait.answered_by, wait.due_at), ('answered', 'Dao', False))
        self.assertTrue(feedback.pancake_answered)
        self.assertEqual(feedback.bot_message_id.text, '✅ Khách Phạm Hằng đã được trả lời (Dao). Em ghi nhận ạ!')

    def test_staff_says_done_outside_pancake_stops_reminders_and_is_kept_as_evidence(self):
        conv, wait, image = self.reminded()
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '333', 'message_id': '9201', 'text': 'Em gọi rồi ạ'})
        feedback = self.env['dac.pancake.feedback'].search([('wait_id', '=', wait.id)])
        self.process(T0 + timedelta(minutes=16))
        self.assertEqual((feedback.intent, feedback.pancake_answered), ('contacted', False))
        self.assertEqual((wait.state, wait.answered_by, wait.due_at, wait.answer_source), ('answered', 'Tbăng báo đã liên hệ khách', False, 'staff'))
        self.assertEqual((wait.report_feedback_id, wait.report_status, wait.report_by, wait.report_text), (feedback, 'contacted', 'Tbăng', 'Em gọi rồi ạ'))
        self.assertIn('Tbăng báo đã liên hệ khách', feedback.bot_message_id.text)
        # thôi nhắc: tới hạn 30 phút không gửi thêm
        before = len(self.real.message_ids)
        self.scan(T0 + timedelta(minutes=45))
        self.assertEqual(len(self.real.message_ids), before)

    def test_mention_without_a_recent_customer_gets_a_hint_and_reactions_are_only_logged(self):
        with patch.object(fields.Datetime, 'now', return_value=T0):
            self.real._on_mention({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9301', 'text': 'Đã xem'})
        self.process(T0)
        feedback = self.env['dac.pancake.feedback'].search([('zalo_message_id', '=', '9301')])
        self.assertEqual((feedback.wait_id.id, feedback.bot_message_id.text), (False, NO_CUSTOMER_TEXT))
        conv, wait, image = self.reminded(fm_id='c-react')
        reaction = self.env['dac.zalo.channel.reaction'].create({'message_id': image.id, 'uid': '333', 'icon': '/-heart'})
        image._on_reaction(reaction)
        logged = self.env['dac.pancake.feedback'].search([('wait_id', '=', wait.id)])
        self.assertEqual((logged.kind, logged.state, logged.intent), ('reaction', 'logged', 'reaction'))

    def test_answered_at_the_30_minute_check_closes_the_thread(self):
        conv, wait, image = self.reminded()
        self.message(conv, T0 + timedelta(minutes=20), 'staff', text='Dạ chị cần làm bảng kích thước bao nhiêu ạ', staff='Tieu Bang')
        self.scan(T0 + timedelta(minutes=42))
        self.assertEqual((wait.state, wait.answered_by), ('answered', 'Tieu Bang'))
        closing = self.real.message_ids.filtered(lambda m: m.reply_to_id == image)
        self.assertEqual(closing.text, '✅ Khách Phạm Hằng đã được trả lời (Tieu Bang). Em ghi nhận ạ!')

    def test_partner_button_counts_feedback(self):
        conv, wait, image = self.reminded()
        partner = self.env['res.partner'].create({'name': 'Phạm Hằng'})
        conv.partner_id = partner
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9401', 'text': 'Đã xem'})
        self.assertEqual(partner.dac_pancake_feedback_count, 1)
        self.assertEqual(partner.action_open_pancake_feedback()['domain'], [('partner_id', '=', partner.id)])

    def test_order_reported_by_someone_without_odoo_account_is_linked_once_the_account_is_mapped(self):
        conv, wait, image = self.reminded(fm_id='c-an')
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '111', 'message_id': '9501', 'text': 'Khách lên đơn rồi nhé'})
        self.process(T0 + timedelta(minutes=15))
        feedback = wait.report_feedback_id
        self.assertEqual((wait.report_status, wait.report_by, wait.report_user_id.id, wait.answered_by), ('order', 'An', False, 'An báo đã lên đơn'))
        an_user = self.env['res.users'].with_context(no_reset_password=True).create({'name': 'Anh An', 'login': 'an-test'})
        member = self.env['dac.zalo.channel.member'].search([('uid', '=', '111')])
        member.user_id = an_user  # tạo tài khoản rồi ghép Zalo ở "Thành viên Zalo ↔ Odoo"
        self.assertEqual((feedback.user_id, wait.report_user_id), (an_user, an_user), 'xác nhận cũ gắn luôn tài khoản Odoo')

    def test_staff_photo_with_a_reaction_marker_counts_as_a_reply_and_friend_notices_are_not_customer_messages(self):
        import json
        from odoo.addons.dac_pancake_remind.models.remind_wait import classify
        conv = self.conversation(name='Thanh Tran', fm_id='c-photo')
        raw = {'id': 'mp1', 'type': 'INBOX', 'page_id': 'P', 'from': {'id': 'P', 'name': 'Duy An', 'admin_name': 'Tieu Bang'},
               'attachments': [{'type': 'reaction'}, {'type': 'photo', 'url': 'https://example.com/a.jpg'}]}
        Message = self.env['dac.pancake.message']
        self.assertEqual(Message._type_of(raw), 'photo')
        old_row = Message.create({'message_fm_id': 'mp1', 'conversation_id': conv.id, 'content_html': '<div></div>', 'type_content': 'reaction',
                                  'inserted_at': T0, 'raw_json': json.dumps(raw)})
        self.assertEqual(classify(old_row), 'staff', 'tin đã lưu loại "reaction" nhưng có ảnh vẫn là nhân viên trả lời')
        pure = Message.create({'message_fm_id': 'mp2', 'conversation_id': conv.id, 'content_html': '', 'type_content': 'reaction',
                               'inserted_at': T0, 'raw_json': json.dumps(dict(raw, id='mp2', attachments=[{'type': 'reaction'}]))})
        self.assertIsNone(classify(pure), 'chỉ thả cảm xúc: không phải trả lời')
        friend = self.message(conv, T0, text='Bạn vừa kết bạn với Thanh Tran', type_content='styled_message')
        self.assertIsNone(classify(friend))

    def test_reply_to_an_unlinked_reminder_or_to_the_assistant_answer_still_finds_the_customer(self):
        conv, wait, image = self.reminded(fm_id='c-key')
        wait.write({'zalo_message_ids': [(5,)]})  # liên kết tin ↔ lượt nhắc bị gỡ (vd chuyển gửi nhóm thật)
        self.assertEqual(image._pancake_wait(), wait, 'tìm theo mã tin pancake-wait:<id>')
        image._on_reply({'thread_id': 'G-real', 'sender_uid': '555', 'message_id': '9601', 'text': 'Đã xem'})
        self.process(T0 + timedelta(minutes=15))
        bot = self.env['dac.pancake.feedback'].search([('zalo_message_id', '=', '9601')]).bot_message_id
        bot.write({'state': 'sent', 'external_message_id': 'z-bot-1'})
        self.assertEqual(bot._pancake_wait(), wait, 'trả lời vào tin trợ lý vừa trả lời cũng ra đúng khách')

    def test_greeting_or_chatbot_is_not_a_staff_reply_and_chatbot_holds_the_group_until_handover(self):
        from odoo.addons.dac_pancake_remind.models.remind_wait import is_greeting
        self.assertTrue(is_greeting('Xin chào! Cảm ơn anh/chị đã liên hệ Duy An. Em có thể hỗ trợ gì cho anh chị ạ?'))
        self.assertTrue(is_greeting('Dạ em chào chị ạ'))
        self.assertFalse(is_greeting('Cảm ơn chị nhiều ạ'), 'cảm ơn sau khi khách gửi chuyển khoản là trả lời thật')
        self.assertFalse(is_greeting('Dạ em chào chị, chị cần làm biển gì ạ?'))
        # Lời chào tự động không làm khách thành "đã được trả lời"
        conv = self.conversation(name='Khách Chào', fm_id='c-greet')
        self.message(conv, T0, text='Shop ơi làm bảng hiệu')
        self.message(conv, T0 + timedelta(minutes=1), 'staff', text='Xin chào! Cảm ơn anh/chị đã liên hệ Duy An', staff='Auto')
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=11))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.remind_count), ('waiting', 1), 'vẫn nhắc: lời chào không phải trả lời')
        # Bật "Chờ chatbot bàn giao": chatbot đang hỏi thông tin → chưa nhắc nhóm; bàn giao → nhắc
        self.config.write({'chatbot_wait': True, 'handover_texts': 'em chuyển nhân viên hỗ trợ',
                           'greeting_texts': 'Anh chị cho em xin kích thước bảng hiệu'})
        bot = self.conversation(name='Khách Bot', fm_id='c-bot')
        self.message(bot, T0, text='Làm bảng hiệu')
        self.message(bot, T0 + timedelta(minutes=1), 'staff', text='Anh chị cho em xin kích thước bảng hiệu ạ', staff='Auto')
        self.message(bot, T0 + timedelta(minutes=3), text='3m x 1m')
        self.scan(T0 + timedelta(minutes=4))
        self.message(bot, T0 + timedelta(minutes=4), 'staff', text='Dạ em cảm ơn ạ', staff='Auto')  # chatbot vẫn đang trò chuyện
        self.scan(T0 + timedelta(minutes=11))
        held = self.Wait.search([('conversation_id', '=', bot.id)])
        self.assertEqual((held.remind_count, held.note), (0, 'Chatbot đang trò chuyện với khách: chờ bàn giao'))
        self.message(bot, T0 + timedelta(minutes=12), 'staff', text='Dạ em chuyển nhân viên hỗ trợ anh chị ngay ạ', staff='Auto')
        self.scan(T0 + timedelta(minutes=22))
        self.assertEqual(held.remind_count, 1, 'chatbot bàn giao → nhắc nhóm')
