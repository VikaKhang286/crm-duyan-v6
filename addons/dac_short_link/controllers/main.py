import threading
import time

import werkzeug.utils

from odoo import http
from odoo.http import request

# Chặn dò mã: một IP mở sai mã quá MAX_MISSES lần trong WINDOW giây thì bị chặn BLOCK giây (bộ đếm theo từng tiến trình Odoo)
MAX_MISSES = 20
WINDOW = 600
BLOCK = 3600
_misses = {}
_lock = threading.Lock()


def _blocked(ip, now):
    with _lock:
        count, start, until = _misses.get(ip, (0, now, 0))
        return until > now


def _note_miss(ip, now):
    with _lock:
        if len(_misses) > 10000:  # dọn bớt để bộ nhớ không phình
            for key in [k for k, (_c, s, u) in _misses.items() if u < now and now - s > WINDOW]:
                _misses.pop(key, None)
        count, start, until = _misses.get(ip, (0, now, 0))
        if now - start > WINDOW:
            count, start = 0, now
        count += 1
        _misses[ip] = (count, start, now + BLOCK if count > MAX_MISSES else until)


class ShortLinkController(http.Controller):

    def _go(self, code, kind):
        """Chuyển tới link gốc. Chỉ link do hệ thống tạo mới có trong bảng (không phải chuyển hướng mở cho link bất kỳ)."""
        ip = request.httprequest.remote_addr or '?'
        now = time.monotonic()
        headers = [('Content-Type', 'text/plain; charset=utf-8'), ('X-Robots-Tag', 'noindex, nofollow'), ('Cache-Control', 'no-store')]
        if _blocked(ip, now):
            return request.make_response('Thử sai quá nhiều lần, vui lòng thử lại sau.', status=429, headers=headers)
        url = request.env['dac.short.link']._resolve(code, kind)
        if not url:
            _note_miss(ip, now)
            return request.make_response('Liên kết không tồn tại.', status=404, headers=headers)
        response = werkzeug.utils.redirect(url, code=302)
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @http.route('/p/<string:code>', type='http', auth='public', methods=['GET'], sitemap=False)
    def pancake_link(self, code, **kwargs):
        return self._go(code, 'p')

    @http.route('/r/<string:code>', type='http', auth='public', methods=['GET'], sitemap=False)
    def reminder_link(self, code, **kwargs):
        return self._go(code, 'r')
