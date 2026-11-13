"""Kết nối Google Gemini: nhập API key, lấy danh sách model khả dụng, và gọi model trả về JSON theo khuôn cho trước.

Addon khác (vd. dac_push) dùng `config._gemini_json(...)` để nhờ AI hiểu câu nhân viên trả lời; nghiệp vụ nằm ở addon đó.
"""
import json
import logging
import re

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta'
MODEL_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')  # tên model đi vào đường dẫn URL nên chỉ nhận ký tự an toàn
# Model có generateContent nhưng không dùng để phân loại văn bản (giọng nói, ảnh, nhúng, thời gian thực...)
NOT_FOR_TEXT = re.compile(r'(tts|image|imagen|veo|audio|live|embedding|aqa|robotics|computer-use|native)', re.IGNORECASE)


class ZaloGeminiModel(models.Model):
    _name = 'dac.zalo.gemini.model'
    _description = 'Model Gemini khả dụng'
    _order = 'name'
    _sql_constraints = [('name_unique', 'unique(name)', 'Model này đã có trong danh sách.')]

    name = fields.Char(string='Mã model', required=True, readonly=True)
    display_name_text = fields.Char(string='Tên hiển thị', readonly=True)

    @api.depends('name', 'display_name_text')
    def _compute_display_name(self):
        for model in self:
            model.display_name = model.name if not model.display_name_text or model.display_name_text == model.name \
                else '%s — %s' % (model.name, model.display_name_text)


class ZaloConfigGemini(models.Model):
    _inherit = 'dac.zalo.config'

    gemini_enabled = fields.Boolean(string='Bật Gemini duyệt phản hồi', default=False)
    gemini_api_key = fields.Char(string='Gemini API key', copy=False, groups='base.group_system')
    gemini_model_id = fields.Many2one('dac.zalo.gemini.model', string='Model Gemini', ondelete='set null', groups='base.group_system')
    gemini_models_at = fields.Datetime(string='Lấy danh sách model lúc', readonly=True, groups='base.group_system')

    @api.constrains('gemini_enabled', 'gemini_api_key', 'gemini_model_id')
    def _validate_gemini(self):
        for config in self:
            if config.gemini_enabled and not (config.gemini_api_key and config.gemini_model_id):
                raise ValidationError(_('Nhập Gemini API key và chọn model trước khi bật Gemini.'))

    # ── Gọi API ───────────────────────────────────────────────────────────────

    def _gemini_call(self, method, path, payload=None, timeout=(5, 15)):
        """Gọi Gemini; key đi trong header (không nằm trong URL nên không lọt vào log). Lỗi → UserError không kèm key hay nội dung."""
        self.ensure_one()
        key = self.sudo().gemini_api_key  # sudo(): trường key chỉ system đọc được; luồng bridge/cron không có user
        if not key:
            raise UserError(_('Chưa nhập Gemini API key.'))
        try:
            response = requests.request(method, GEMINI_API + path, headers={'x-goog-api-key': key},
                                        json=payload, timeout=timeout, allow_redirects=False)
        except requests.exceptions.RequestException:
            raise UserError(_('Không kết nối được Gemini. Kiểm tra mạng của máy chủ rồi thử lại.')) from None
        if response.status_code in (400, 401, 403):
            raise UserError(_('Gemini từ chối yêu cầu (HTTP %s). Kiểm tra API key và quyền dùng model.') % response.status_code)
        if response.status_code == 429:
            raise UserError(_('Gemini báo vượt hạn mức (HTTP 429). Thử lại sau.'))
        if response.status_code != 200:
            raise UserError(_('Gemini chưa xử lý được yêu cầu (HTTP %s).') % response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise UserError(_('Gemini trả dữ liệu không hợp lệ.')) from None
        if not isinstance(result, dict):
            raise UserError(_('Gemini trả dữ liệu không hợp lệ.'))
        return result

    def _gemini_json(self, system, text, schema):
        """Đưa `text` cho model đã chọn, bắt trả JSON đúng `schema`; trả về dict. Lỗi bất kỳ → UserError."""
        self.ensure_one()
        model = self.sudo().gemini_model_id.name  # sudo(): xem _gemini_call
        if not model or not MODEL_NAME.match(model):
            raise UserError(_('Chưa chọn model Gemini.'))
        result = self._gemini_call('POST', '/models/%s:generateContent' % model, {
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': text}]}],
            'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json', 'responseSchema': schema},
        })
        try:
            parts = result['candidates'][0]['content']['parts']
            answer = ''.join(part.get('text', '') for part in parts if isinstance(part, dict))
            data = json.loads(answer)
        except (KeyError, IndexError, TypeError, ValueError):
            raise UserError(_('Gemini không trả kết quả đọc được (có thể nội dung bị chặn).')) from None
        if not isinstance(data, dict):
            raise UserError(_('Gemini không trả kết quả đọc được.'))
        return data

    # ── Giao diện cấu hình ────────────────────────────────────────────────────

    def action_gemini_load_models(self):
        """Hỏi Gemini danh sách model dùng được cho văn bản, cập nhật bảng chọn (giữ model đang chọn)."""
        self.ensure_one()
        self._require_admin()
        found, token = {}, None
        for _page in range(10):
            path = '/models?pageSize=200' + ('&pageToken=' + requests.utils.quote(token, safe='') if token else '')
            result = self._gemini_call('GET', path, timeout=(5, 20))
            for item in result.get('models') or []:
                name = str(item.get('name') or '').removeprefix('models/')
                if (MODEL_NAME.match(name) and 'generateContent' in (item.get('supportedGenerationMethods') or [])
                        and not NOT_FOR_TEXT.search(name)):
                    found[name] = str(item.get('displayName') or '')[:120]
            token = result.get('nextPageToken')
            if not token:
                break
        if not found:
            raise UserError(_('Gemini không trả model nào dùng được. Kiểm tra lại API key.'))
        Model = self.env['dac.zalo.gemini.model']
        existing = {model.name: model for model in Model.search([])}
        for name, label in found.items():
            if name in existing:
                existing[name].write({'display_name_text': label})
        Model.create([{'name': name, 'display_name_text': label} for name, label in found.items() if name not in existing])
        # Model Gemini đã gỡ khỏi danh sách thì xóa, trừ model đang được chọn (để cấu hình không đổi ngầm)
        Model.search([('name', 'not in', list(found)), ('id', '!=', self.gemini_model_id.id)]).unlink()
        self.write({'gemini_models_at': fields.Datetime.now()})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Gemini'), 'message': _('Đã lấy %d model. Chọn một model trong ô “Model Gemini”.') % len(found),
            'type': 'success', 'sticky': False}}

    def action_gemini_test(self):
        self.ensure_one()
        self._require_admin()
        data = self._gemini_json('Trả JSON {"ok": true}.', 'ping', {'type': 'OBJECT', 'properties': {'ok': {'type': 'BOOLEAN'}}})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Gemini'), 'message': _('Kết nối thành công, model %s trả lời được.') % self.gemini_model_id.name
            if data else _('Gemini phản hồi nhưng không có nội dung.'), 'type': 'success', 'sticky': False}}
