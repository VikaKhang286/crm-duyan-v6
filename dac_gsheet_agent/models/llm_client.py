"""Lớp gọi LLM có tool use, hỗ trợ 2 loại endpoint:

* ``anthropic``: Claude Messages API gốc (raw HTTP vì container Odoo không
  cài SDK ``anthropic``; thêm pip dependency vào image production là rủi ro).
* ``openai_compat``: chuẩn /chat/completions — dùng cho 9router.duyan.vn,
  Gemini OpenAI-compat hoặc gateway nội bộ khác.

Hai backend trả cùng một kết quả chuẩn hoá để agent_session dùng chung.
Lịch sử hội thoại được lưu ở dạng "native" của từng provider để có thể gửi
lại nguyên vẹn (Claude yêu cầu giữ nguyên thinking block giữa các vòng tool).
"""
import json
import logging
import re
import time

import requests

_logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = '2026-10-21'
ANTHROPIC_FALLBACK_BETA = 'server-side-fallback-2026-07-01'
# Các model hỗ trợ tham số `fallbacks` server-side khi bị safety classifier từ chối.
FALLBACK_MODELS = ('claude-fable-5-2', 'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5')
TIMEOUT = (10, 120)
MAX_TOOL_RESULT_CHARS = 12000


class LLMError(Exception):
    pass


def _truncate(text, limit=MAX_TOOL_RESULT_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit] + '\n…[đã cắt bớt %s ký tự]' % (len(text) - limit)


def extract_json(text):
    """Lấy object JSON đầu tiên trong text (model đôi khi bọc ```json)."""
    if not text:
        return None
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        return None


class LLMClient:
    """config: dict(provider, base_url, api_key, model, effort, use_fallbacks)."""

    def __init__(self, config):
        self.provider = config.get('provider') or 'anthropic'
        self.base_url = (config.get('base_url') or '').rstrip('/')
        self.api_key = config.get('api_key') or ''
        self.model = config.get('model') or 'claude-opus-5-5'
        self.effort = config.get('effort') or 'medium'
        self.use_fallbacks = config.get('use_fallbacks', True)
        if not self.api_key:
            raise LLMError('Chưa cấu hình API key cho AI (Cài đặt → Trợ lý AI Sheet).')
        if not self.base_url:
            self.base_url = 'https://api.anthropic.com' if self.provider == 'anthropic' else ''
        if not self.base_url:
            raise LLMError('Chưa cấu hình Base URL cho endpoint OpenAI-compatible.')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def user_message(self, text):
        return {'role': 'user', 'content': text}

    def assistant_text_message(self, text):
        return {'role': 'assistant', 'content': text}

    def run(self, system, messages, tools, executor, max_rounds=8, deadline=None):
        """Chạy vòng tool-use đến khi model trả lời xong.

        messages: lịch sử native (đã gồm tin nhắn user mới nhất).
        executor(name, args) -> str : thực thi tool, trả chuỗi kết quả.
        Trả dict(text, new_messages, tool_calls, stop_reason).
        """
        if self.provider == 'anthropic':
            return self._run_anthropic(system, messages, tools, executor, max_rounds, deadline)
        return self._run_openai(system, messages, tools, executor, max_rounds, deadline)

    def complete(self, system, prompt):
        """Một lượt hỏi-đáp không tool (dùng cho reflection / tóm tắt)."""
        result = self.run(system, [self.user_message(prompt)], [], lambda n, a: '', max_rounds=1)
        return result['text']

    # ------------------------------------------------------------------
    # Anthropic Messages API
    # ------------------------------------------------------------------
    def _anthropic_post(self, body):
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type': 'application/json',
        }
        if self.use_fallbacks and self.model in FALLBACK_MODELS:
            headers['anthropic-beta'] = ANTHROPIC_FALLBACK_BETA
            body['fallbacks'] = 'default'
        url = self.base_url if self.base_url.endswith('/v1/messages') else self.base_url + '/v1/messages'
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise LLMError('Không kết nối được Claude API: %s' % exc) from exc
        if resp.status_code >= 400:
            raise LLMError('Claude API lỗi %s: %s' % (resp.status_code, resp.text[:500]))
        return resp.json()

    def _run_anthropic(self, system, messages, tools, executor, max_rounds, deadline):
        convo = list(messages)
        new_messages = []
        tool_calls = []
        api_tools = [
            {'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']}
            for t in tools
        ]
        stop_reason = None
        final_text = ''
        for _round in range(max_rounds):
            body = {
                'model': self.model,
                'max_tokens': 16000,
                'system': system,
                'messages': convo,
                'output_config': {'effort': self.effort},
            }
            if api_tools:
                body['tools'] = api_tools
            data = self._anthropic_post(body)
            stop_reason = data.get('stop_reason')
            content = data.get('content') or []
            # Giữ nguyên toàn bộ content (kể cả thinking block) để gửi lại.
            assistant_msg = {'role': 'assistant', 'content': content}
            convo.append(assistant_msg)
            new_messages.append(assistant_msg)
            final_text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text').strip()

            if stop_reason == 'refusal':
                final_text = final_text or 'Yêu cầu này bị bộ lọc an toàn từ chối. Hãy diễn đạt lại.'
                break
            if stop_reason != 'tool_use':
                break
            if deadline and time.time() > deadline:
                final_text = (final_text + '\n\n' if final_text else '') + \
                    '⏱ Đã hết thời gian xử lý cho lượt này — hãy nhắn "tiếp tục" để làm tiếp.'
                # Phải trả tool_result cho mọi tool_use để lịch sử hợp lệ.
                results = [
                    {'type': 'tool_result', 'tool_use_id': b['id'], 'content': 'Bị dừng do hết thời gian.', 'is_error': True}
                    for b in content if b.get('type') == 'tool_use'
                ]
                tail = {'role': 'user', 'content': results}
                new_messages.append(tail)
                new_messages.append({'role': 'assistant', 'content': [{'type': 'text', 'text': final_text}]})
                break

            results = []
            for block in content:
                if block.get('type') != 'tool_use':
                    continue
                name, args = block['name'], block.get('input') or {}
                output, is_error = self._safe_execute(executor, name, args)
                tool_calls.append({'name': name, 'args': args, 'output': output, 'is_error': is_error})
                item = {'type': 'tool_result', 'tool_use_id': block['id'], 'content': output}
                if is_error:
                    item['is_error'] = True
                results.append(item)
            # Mọi tool_result của một lượt phải nằm trong MỘT user message.
            tool_msg = {'role': 'user', 'content': results}
            convo.append(tool_msg)
            new_messages.append(tool_msg)
        else:
            final_text = final_text or 'Đã đạt giới hạn số bước xử lý cho một lượt.'
        return {'text': final_text, 'new_messages': new_messages, 'tool_calls': tool_calls, 'stop_reason': stop_reason}

    # ------------------------------------------------------------------
    # OpenAI-compatible /chat/completions
    # ------------------------------------------------------------------
    def _openai_post(self, body):
        url = self.base_url if self.base_url.endswith('/chat/completions') else self.base_url + '/chat/completions'
        headers = {'Authorization': 'Bearer %s' % self.api_key, 'Content-Type': 'application/json'}
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise LLMError('Không kết nối được AI endpoint: %s' % exc) from exc
        if resp.status_code >= 400:
            raise LLMError('AI endpoint lỗi %s: %s' % (resp.status_code, resp.text[:500]))
        return resp.json()

    def _run_openai(self, system, messages, tools, executor, max_rounds, deadline):
        convo = [{'role': 'system', 'content': system}] + list(messages)
        new_messages = []
        tool_calls = []
        api_tools = [{'type': 'function', 'function': t} for t in tools]
        final_text = ''
        stop_reason = None
        for _round in range(max_rounds):
            body = {'model': self.model, 'messages': convo}
            if api_tools:
                body['tools'] = api_tools
            data = self._openai_post(body)
            choice = (data.get('choices') or [{}])[0]
            msg = choice.get('message') or {}
            stop_reason = choice.get('finish_reason')
            assistant_msg = {'role': 'assistant', 'content': msg.get('content') or ''}
            if msg.get('tool_calls'):
                assistant_msg['tool_calls'] = msg['tool_calls']
            convo.append(assistant_msg)
            new_messages.append(assistant_msg)
            final_text = (msg.get('content') or '').strip()
            if not msg.get('tool_calls'):
                break
            timed_out = bool(deadline and time.time() > deadline)
            for call in msg['tool_calls']:
                fn = call.get('function') or {}
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except ValueError:
                    args = {}
                if timed_out:
                    output, is_error = 'Bị dừng do hết thời gian.', True
                else:
                    output, is_error = self._safe_execute(executor, fn.get('name'), args)
                    tool_calls.append({'name': fn.get('name'), 'args': args, 'output': output, 'is_error': is_error})
                tool_msg = {'role': 'tool', 'tool_call_id': call.get('id'), 'content': output}
                convo.append(tool_msg)
                new_messages.append(tool_msg)
            if timed_out:
                final_text = '⏱ Đã hết thời gian xử lý cho lượt này — hãy nhắn "tiếp tục" để làm tiếp.'
                new_messages.append({'role': 'assistant', 'content': final_text})
                break
        else:
            final_text = final_text or 'Đã đạt giới hạn số bước xử lý cho một lượt.'
        return {'text': final_text, 'new_messages': new_messages, 'tool_calls': tool_calls, 'stop_reason': stop_reason}

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_execute(executor, name, args):
        try:
            output = executor(name, args)
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, default=str)
            return _truncate(output), False
        except Exception as exc:  # trả lỗi cho model tự xử lý, không làm hỏng cả lượt
            _logger.info('Tool %s lỗi: %s', name, exc)
            return 'LỖI: %s' % exc, True
