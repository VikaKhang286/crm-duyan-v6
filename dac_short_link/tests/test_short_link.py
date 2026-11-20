from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.dac_short_link.controllers import main as controller
from odoo.addons.dac_short_link.models import short_link

LONG = 'https://pancake.vn/pzl_84915555018?c_id=pzl_u_1571138013671979826_5603914452991454010'
TICKET = 'https://crm.duyan.vn/hen/AbCdEfGhIjKlMnOp'


@tagged('post_install', '-at_install')
class TestShortLink(HttpCase):

    def setUp(self):
        super().setUp()
        controller._misses.clear()

    def test_pancake_p_three_chars_and_reminder_r_four_chars(self):
        Link = self.env['dac.short.link']
        pancake = Link._shorten(LONG, source='test', kind='p')
        reminder = Link._shorten(TICKET, source='test', kind='r')
        self.assertRegex(pancake, r'/p/\w{3}$')
        self.assertRegex(reminder, r'/r/\w{4}$')
        self.assertFalse(set(pancake[-3:] + reminder[-4:]) - set(short_link.ALPHABET))
        self.assertEqual(Link._shorten(LONG, kind='p'), pancake, 'một link gốc một mã')
        response = self.url_open('/p/' + pancake[-3:], allow_redirects=False)
        self.assertEqual((response.status_code, response.headers['Location']), (302, LONG))
        response = self.url_open('/r/' + reminder[-4:], allow_redirects=False)
        self.assertEqual((response.status_code, response.headers['Location']), (302, TICKET))
        self.assertEqual(self.url_open('/p/' + reminder[-4:], allow_redirects=False).status_code, 404, 'mã /r/ không mở qua /p/')
        self.assertEqual(Link.search([('code', '=', pancake[-3:])]).hits, 1)
        legacy = self.url_open('/r/' + pancake[-3:], allow_redirects=False)
        self.assertEqual(legacy.headers.get('Location'), LONG, 'link Pancake cũ đã gửi dạng /r/abc vẫn mở được')

    def test_guessing_codes_gets_the_ip_blocked(self):
        Link = self.env['dac.short.link']
        good = Link._shorten(TICKET, kind='r')[-4:]
        for i in range(controller.MAX_MISSES + 1):
            self.assertEqual(self.url_open('/r/zz%02d' % i, allow_redirects=False).status_code, 404)
        self.assertEqual(self.url_open('/r/' + good, allow_redirects=False).status_code, 429, 'IP dò mã bị chặn, kể cả mã đúng')

    def test_invalid_urls_are_returned_unchanged_and_full_space_grows_the_code(self):
        Link = self.env['dac.short.link']
        self.assertEqual(Link._shorten(''), '')
        self.assertEqual(Link._shorten('javascript:alert(1)'), 'javascript:alert(1)')
        Link.create({'code': 'abc', 'url': 'https://x.example/1', 'url_hash': short_link.url_hash('https://x.example/1')})
        choices = iter(['a', 'b', 'c'] * short_link.TRIES_PER_LENGTH + ['d', 'e', 'f', 'g'])
        with patch.object(short_link.secrets, 'choice', side_effect=lambda alphabet: next(choices)):
            code = Link._code_for('https://x.example/2')
        self.assertEqual(code, 'defg', 'mã 3 ký tự trùng mãi thì dùng 4 ký tự')
