"""Link rút gọn cho tin Zalo.

* /p/<3 ký tự>: hội thoại Pancake (trang đích tự bắt đăng nhập Pancake nên mã ngắn không lộ gì).
* /r/<4 ký tự>: lời nhắc (phiếu hẹn) và lượt khách liên hệ (trang lead website) — trang công khai nên mã dài hơn, và
  route chặn IP dò mã (xem controllers/main.py).
"""
import hashlib
import secrets
from urllib.parse import urlsplit

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ALPHABET = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # bỏ 0 O 1 l I cho dễ đọc
KINDS = [('p', 'Pancake (/p/)'), ('r', 'Lời nhắc / khách liên hệ (/r/)')]
MIN_LENGTH = {'p': 3, 'r': 4}
TRIES_PER_LENGTH = 12   # trùng mã liên tiếp nhiều lần = kho mã ngắn gần đầy → dùng mã dài hơn một ký tự
MAX_URL = 2000


def url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()


class ShortLink(models.Model):
    _name = 'dac.short.link'
    _description = 'Link rút gọn'
    _order = 'id desc'
    _rec_name = 'code'
    _sql_constraints = [('code_unique', 'unique(code)', 'Mã rút gọn đã tồn tại.'),
                        ('kind_hash_unique', 'unique(kind, url_hash)', 'Link gốc này đã có mã rút gọn.')]

    kind = fields.Selection(KINDS, string='Loại', required=True, default='p', readonly=True, index=True)
    code = fields.Char(string='Mã', required=True, readonly=True, index=True)
    url = fields.Char(string='Link gốc', required=True, readonly=True)
    url_hash = fields.Char(required=True, readonly=True)
    short_url = fields.Char(string='Link rút gọn', compute='_compute_short_url')
    hits = fields.Integer(string='Lượt bấm', readonly=True)
    last_hit_at = fields.Datetime(string='Bấm gần nhất', readonly=True)
    source = fields.Char(string='Tạo bởi', readonly=True, help='Module/tính năng tạo link.')

    @api.model
    def _host(self):
        # sudo(): đọc tham số hệ thống trong cron/webhook
        return urlsplit(self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')).netloc or 'crm.duyan.vn'

    def _compute_short_url(self):
        host = self._host()
        for link in self:
            link.short_url = '%s/%s/%s' % (host, link.kind, link.code)

    @api.constrains('url')
    def _check_url(self):
        for link in self:
            parsed = urlsplit(link.url or '')
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or len(link.url) > MAX_URL:
                raise ValidationError(_('Chỉ rút gọn được link http(s) hợp lệ.'))

    @api.model
    def _shorten(self, url, source=None, kind='p'):
        """Link gốc → "host/<p|r>/<mã>" (không scheme, Zalo tự nhận ra link). Link rỗng/không hợp lệ → trả lại nguyên văn."""
        parsed = urlsplit(url or '')
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or len(url) > MAX_URL or kind not in MIN_LENGTH:
            return url or ''
        return '%s/%s/%s' % (self._host(), kind, self._code_for(url, source, kind))

    @api.model
    def _code_for(self, url, source=None, kind='p'):
        # sudo(): luồng nền (cron gửi tin Zalo) tạo link; người dùng thường không có quyền trên bảng này
        Link = self.sudo()
        digest = url_hash(url)
        existing = Link.search([('kind', '=', kind), ('url_hash', '=', digest)], limit=1)
        if existing:
            return existing.code
        length = MIN_LENGTH[kind]
        while True:
            for _try in range(TRIES_PER_LENGTH):
                code = ''.join(secrets.choice(ALPHABET) for _i in range(length))
                if Link.search_count([('code', '=', code)]):
                    continue
                try:
                    with self.env.cr.savepoint():
                        return Link.create({'kind': kind, 'code': code, 'url': url, 'url_hash': digest, 'source': source}).code
                except IntegrityError:
                    found = Link.search([('kind', '=', kind), ('url_hash', '=', digest)], limit=1)  # luồng khác vừa tạo cùng link
                    if found:
                        return found.code
            length += 1

    @api.model
    def _resolve(self, code, kind='p'):
        if not code or len(code) > 12 or set(code) - set(ALPHABET):
            return None
        # sudo(): người mở link không đăng nhập; chỉ tra đúng một mã
        link = self.sudo().search([('kind', '=', kind), ('code', '=', code)], limit=1)
        if not link and kind == 'r' and len(code) == MIN_LENGTH['p']:
            link = self.sudo().search([('kind', '=', 'p'), ('code', '=', code)], limit=1)  # link Pancake cũ đã gửi dạng /r/abc
        if not link:
            return None
        self.env.cr.execute('UPDATE dac_short_link SET hits = COALESCE(hits, 0) + 1, last_hit_at = %s WHERE id = %s',
                            (fields.Datetime.now(), link.id))
        return link.url
