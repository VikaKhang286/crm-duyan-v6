"""Nhận diện khách nam hay nữ để chatbot xưng hô (anh / chị / anh-chị).

Nguồn: giới tính trong hồ sơ Pancake/Zalo (`page_customer.gender`, lưu khi kéo danh sách hội thoại), tên hiển thị và ảnh đại diện.
AI đọc cả ba MỘT lần cho mỗi hội thoại rồi lưu lại; chỉ coi là nam/nữ khi AI chắc chắn (cao/vừa), còn lại "Chưa rõ" → gọi "anh/chị".
Trong lúc chat, nếu khách tự xưng hoặc nhắc cách gọi khác (bot gọi "chị" mà khách xưng "anh"…), chatbot đổi theo khách và kết quả
được cập nhật ("Khách tự xưng trong chat")."""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ai_agent import fetch_image, vision_call

_logger = logging.getLogger(__name__)

GENDERS = [('male', 'Nam'), ('female', 'Nữ'), ('unknown', 'Chưa rõ')]
TITLES = {'male': 'anh', 'female': 'chị'}
GENDER_SYSTEM = ('Bạn giúp nhân viên bán hàng xưng hô đúng với khách nhắn tin (gọi "anh" với nam, "chị" với nữ). Từ tên hiển thị, giới '
                 'tính trong hồ sơ Zalo/Facebook (nếu có) và ảnh đại diện (nếu có), hãy gọi tool report_gender. Ảnh là logo, cửa hàng, '
                 'phong cảnh, con vật, em bé, ảnh nhóm, hoặc tên là tên cửa hàng/công ty, hoặc các nguồn mâu thuẫn nhau → confidence thấp. '
                 'Hồ sơ ghi giới tính khớp với ảnh/tên → confidence cao. Không chắc thì để unknown.')
GENDER_SCHEMA = {'type': 'object', 'properties': {
    'gender': {'type': 'string', 'enum': ['male', 'female', 'unknown']},
    'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
    'reason': {'type': 'string', 'description': 'Lý do ngắn (tối đa 15 từ).'},
}, 'required': ['gender', 'confidence']}


def gender_rule(gender, source=''):
    """Câu quy định xưng hô do code chèn vào system prompt."""
    follow = ('Nếu trong đoạn chat khách tự xưng hoặc nhắc cách gọi khác (anh, chị, cô, chú, em…) thì đổi theo khách ngay, kể cả khi '
              'đã lỡ gọi khác ở tin trước.')
    if gender in TITLES:
        return 'Xưng hô: gọi khách là "%s"%s. %s' % (TITLES[gender], ' (nhận diện: %s)' % source if source else '', follow)
    return 'Xưng hô: chưa rõ khách là nam hay nữ nên gọi "anh/chị". %s' % follow


class PancakeConversationGender(models.Model):
    _inherit = 'dac.pancake.conversation'

    pancake_gender = fields.Char(string='Giới tính (hồ sơ Pancake)', readonly=True)
    customer_gender = fields.Selection(GENDERS, string='Giới tính khách (để xưng hô)', default='unknown', readonly=True)
    gender_source = fields.Char(string='Nhận diện từ', readonly=True)
    gender_checked = fields.Boolean(string='Đã nhận diện giới tính', readonly=True)

    @api.model
    def _upsert(self, page, row):
        conversation, fresh = super()._upsert(page, row)
        customer = row.get('page_customer') if isinstance(row.get('page_customer'), dict) else {}
        gender = customer.get('gender')
        if gender in ('male', 'female', 'unknown') and conversation.pancake_gender != gender:
            conversation.pancake_gender = gender
        return conversation, fresh

    def _gender(self):
        """(male | female | unknown, nguồn). AI nhận diện một lần rồi lưu; lỗi AI → Chưa rõ (vẫn lưu để không gọi lại mãi)."""
        self.ensure_one()
        if self.gender_checked:
            return self.customer_gender or 'unknown', self.gender_source or ''
        url = self.avatar_url or ''
        avatar = None
        if url and not url.rstrip('/').endswith('/default'):
            try:
                avatar = fetch_image(url)
            except Exception:  # noqa: BLE001 — không tải được ảnh thì chỉ dựa vào tên và hồ sơ
                avatar = None
        text = 'Tên hiển thị: "%s"\nGiới tính trong hồ sơ Zalo/Facebook: %s\nẢnh đại diện: %s' % (
            self.customer_name or '', {'male': 'Nam', 'female': 'Nữ'}.get(self.pancake_gender, 'không có'), 'đính kèm' if avatar else 'không có')
        # Model nhanh của chatbot Chào hỏi (màn hình Model & thinking theo tác vụ)
        ai = self.env['dac.chat.task']._resolve('bot_greeting', None, True, use_config_model=False)
        try:
            data, _label, _sent, _usage = vision_call(ai.env, ai.model, ai.fallback, GENDER_SYSTEM, text,
                                                      [('Ảnh đại diện', avatar)] if avatar else [], GENDER_SCHEMA, 'report_gender',
                                                      'Báo giới tính khách.', (10, 60))
        except UserError as error:
            _logger.info('Không nhận diện được giới tính hội thoại %s: %s', self.id, error)
            data = {}
        sure = data.get('gender') in TITLES and data.get('confidence') in ('high', 'medium')
        gender = data['gender'] if sure else 'unknown'
        source = 'AI%s: %s' % ('' if sure else ' (chưa chắc)', str(data.get('reason') or 'không đủ thông tin')[:120])
        # sudo(): luồng nền (cron) ghi kết quả nhận diện vào hội thoại Pancake
        self.sudo().write({'customer_gender': gender, 'gender_source': source, 'gender_checked': True})
        return gender, source

    def _gender_from_chat(self, gender):
        """Khách tự xưng trong chat (chatbot báo `customer_gender`): cập nhật nếu khác."""
        self.ensure_one()
        if gender in TITLES and gender != self.customer_gender:
            self.sudo().write({'customer_gender': gender, 'gender_source': 'Khách tự xưng trong chat', 'gender_checked': True})
            return True
        return False
