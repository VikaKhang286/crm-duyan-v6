from odoo import fields, models


class PancakeConversation(models.Model):
    _inherit = 'page.fm.conversation'

    sender_fm_id = fields.Char(
        string='ID người gửi trên nền tảng', index=True, copy=False,
        help='from.id từ Pancake; với hội thoại Messenger đây là PSID, không phải UUID khách hàng Pancake.',
    )
    conversation_type_fm = fields.Char(
        string='Loại hội thoại Pancake', copy=False,
        help='INBOX, COMMENT hoặc LIVESTREAM từ API Pancake.',
    )
