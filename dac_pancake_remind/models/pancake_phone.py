import json

from odoo import api, fields, models
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn

from .pancake_api import match_partner


class PancakePhone(models.Model):
    """Số điện thoại khách để lại trong tin nhắn Pancake (Pancake tự nhận số trong tin: `phone_info`).

    Gom vào đây (có chỉ mục) để đối chiếu nhanh với lead điền form và tìm liên hệ Odoo của khách.
    """
    _name = 'dac.pancake.phone'
    _description = 'Số điện thoại khách để lại trên Pancake'

    conversation_id = fields.Many2one('dac.pancake.conversation', required=True, ondelete='cascade', index=True)
    phone = fields.Char(string='Số điện thoại (chuẩn hóa)', required=True, index=True)
    seen_at = fields.Datetime(string='Thấy lúc')
    _sql_constraints = [('conversation_phone_unique', 'unique(conversation_id, phone)', 'Số này đã ghi nhận cho hội thoại.')]

    @api.model
    def _numbers_in(self, message):
        """Các số điện thoại Pancake đã nhận ra trong một tin (chuẩn hóa 0XXXXXXXXX)."""
        try:
            raw = json.loads(message.raw_json or '{}')
        except ValueError:
            return []
        found = []
        for item in raw.get('phone_info') or [] if isinstance(raw, dict) else []:
            number = normalize_phone_vn(item.get('phone_number') or item.get('captured')) if isinstance(item, dict) else ''
            if number and number not in found:
                found.append(number)
        return found

    @api.model
    def _capture(self, customer_messages):
        """Ghi nhận số khách để lại trong các tin của KHÁCH (không lấy số trong tin nhân viên: đó là số của shop), và gắn
        hội thoại với liên hệ Odoo có cùng số (để biết khách đã có đơn hàng chưa)."""
        wanted = {}
        for message in customer_messages:
            for number in self._numbers_in(message):
                wanted.setdefault((message.conversation_id.id, number), message.inserted_at)
        if not wanted:
            return
        existing = {(r.conversation_id.id, r.phone) for r in self.search([('conversation_id', 'in', list({c for c, _ in wanted}))])}
        created = self.create([{'conversation_id': conv, 'phone': number, 'seen_at': at}
                               for (conv, number), at in wanted.items() if (conv, number) not in existing])
        for record in created:
            conversation = record.conversation_id
            if not conversation.partner_id:
                partner = match_partner(self.env, record.phone)
                if partner:
                    conversation.partner_id = partner

    @api.model
    def _conversations_for(self, phone, pages=None):
        """Hội thoại Pancake của khách có số `phone`: theo số khách để lại trong tin hoặc số của liên hệ gắn với hội thoại."""
        number = normalize_phone_vn(phone)
        Conversation = self.env['dac.pancake.conversation'].sudo()
        if not number:
            return Conversation
        page_domain = [('page_id', 'in', pages.ids)] if pages is not None else []
        found = Conversation.search(page_domain + [('id', 'in', self.sudo().search([('phone', '=', number)]).conversation_id.ids)])
        partner = match_partner(self.env, number)
        if partner:
            found |= Conversation.search(page_domain + [('partner_id', '=', partner.id)])
        return found

    @api.model
    def _known_phone(self, conversation):
        """Số điện thoại đã biết của hội thoại (ưu tiên số khách để lại trong tin), hoặc ''."""
        conversation = conversation.sudo()
        record = self.sudo().search([('conversation_id', '=', conversation.id)], order='id', limit=1)
        return record.phone or normalize_phone_vn(conversation.partner_id.phone) or normalize_phone_vn(conversation.partner_id.mobile) or ''
