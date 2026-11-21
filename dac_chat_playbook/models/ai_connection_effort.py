"""Gắn mức thinking vào yêu cầu gửi tới kết nối kiểu OpenAI (9router…).

Mức thinking của tác vụ đi theo context `dac_ai_effort` (đặt bởi `dac.chat.task._resolve`) nên không phải đổi chữ ký các hàm gọi AI
của `dac_ai_core` dùng chung. Chỉ kết nối kiểu OpenAI nhận `reasoning_effort`; kết nối Gemini không đi qua `_openai` nên tự bỏ qua.
"""
from odoo import models

from .chat_task import EFFORTS


class AiConnectionEffort(models.Model):
    _inherit = 'dac.ai.connection'

    def _openai(self, payload, timeout, model):
        effort = self.env.context.get('dac_ai_effort')
        if effort in EFFORTS and 'reasoning_effort' not in payload:
            payload = dict(payload, reasoning_effort=effort)
        return super()._openai(payload, timeout, model)
