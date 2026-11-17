# -*- coding: utf-8 -*-
"""Dựng ảnh giống ảnh chụp màn hình cuộc trò chuyện (avatar + tên khách + tin nhắn) từ HTML bằng wkhtmltoimage."""
import base64
import html
import os
import re
import subprocess
import tempfile

WIDTH = 460
MAX_MESSAGES = 8
MAX_TEXT = 600

# Emoji (và ký tự biểu tượng) — container Odoo không có font emoji màu nên bỏ đi thay vì hiện ô vuông
_EMOJI = re.compile('[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️\U000E0020-\U000E007F]')
_PALETTE = ['#e0634f', '#d98a2b', '#4b9d5f', '#3a8fb7', '#6c6fc7', '#b05fb0', '#c2577a']

CSS = """
body{margin:0;background:#e9edf3;font-family:'Roboto','DejaVu Sans',sans-serif;color:#1c1e21}
.card{width:%(w)dpx;background:#e9edf3}
.top{background:#ffffff;padding:12px 16px;border-bottom:1px solid #d6dbe3}
.top table{border-collapse:collapse}
.top td{vertical-align:middle;padding:0}
.name{font-size:17px;font-weight:bold;padding-left:10px !important}
.sub{font-size:12px;color:#7a828e;padding-left:10px !important}
.av{width:44px;height:44px;border-radius:22px}
.ini{width:44px;height:44px;border-radius:22px;color:#fff;font-size:19px;font-weight:bold;text-align:center;line-height:44px}
.chat{padding:12px 12px 6px 12px}
.row{margin:0 0 8px 0;overflow:hidden}
.row table{border-collapse:collapse}
.row td{vertical-align:bottom;padding:0}
.mini{width:30px;height:30px;border-radius:15px}
.mini-i{width:30px;height:30px;border-radius:15px;color:#fff;font-size:13px;font-weight:bold;text-align:center;line-height:30px}
.bub{max-width:300px;padding:8px 12px;border-radius:14px;font-size:15px;line-height:20px;word-wrap:break-word}
.in .bub{background:#ffffff;border:1px solid #dfe3ea}
.out .bub{background:#d6e8ff;border:1px solid #bcd8fb}
.out{text-align:right}
.out table{float:right}
.pic{display:block;max-width:240px;border-radius:8px;margin-bottom:4px}
.att{font-size:13px;color:#4a525d;font-style:italic}
.wait{margin:6px 12px 14px 12px;background:#fff4e5;border:1px solid #ffd9a0;color:#9a5b00;font-size:13px;padding:7px 10px;border-radius:8px;text-align:center}
""" % {'w': WIDTH}


def _text(raw):
    raw = re.sub(r'(?i)<br\s*/?>|</div>|</p>', '\n', raw or '')
    raw = html.unescape(re.sub(r'<[^>]+>', '', raw))
    raw = _EMOJI.sub('', raw)
    raw = re.sub(r'[ \t]+', ' ', raw)
    raw = re.sub(r'\n{3,}', '\n\n', raw).strip()
    return raw[:MAX_TEXT] + ('…' if len(raw) > MAX_TEXT else '')


def _data_uri(blob, mime='image/jpeg'):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(blob).decode())


def _initials(name):
    words = [w for w in re.split(r'\s+', (name or '').strip()) if w]
    return (words[-1][0] if words else '?').upper()


def _color(name):
    return _PALETTE[sum(map(ord, name or '?')) % len(_PALETTE)]


def build_html(customer_name, avatar, messages, platform='', page_name='', waiting_text=None):
    """messages: [{'side': 'in'|'out', 'text': str, 'image': bytes|None, 'note': str}] cũ → mới. Không vẽ giờ từng tin:
    chỉ dòng `waiting_text` cuối ảnh cho biết khách chờ từ lúc nào. avatar: bytes hoặc None."""
    name = html.escape(customer_name or 'Khách hàng')
    if avatar:
        head = '<img class="av" src="%s"/>' % _data_uri(avatar)
        mini = '<img class="mini" src="%s"/>' % _data_uri(avatar)
    else:
        head = '<div class="ini" style="background:%s">%s</div>' % (_color(customer_name), html.escape(_initials(customer_name)))
        mini = '<div class="mini-i" style="background:%s">%s</div>' % (_color(customer_name), html.escape(_initials(customer_name)))
    sub = ' · '.join(p for p in (platform, page_name) if p)
    out = ['<html><head><meta charset="utf-8"><style>%s</style></head><body><div class="card">' % CSS,
           '<div class="top"><table><tr><td>%s</td><td><div class="name">%s</div>%s</td></tr></table></div><div class="chat">' % (
               head, name, '<div class="sub">%s</div>' % html.escape(sub) if sub else '')]
    shown = messages[-MAX_MESSAGES:]
    for i, m in enumerate(shown):
        last_of_run = i + 1 == len(shown) or shown[i + 1]['side'] != m['side']
        body = ''
        if m.get('image'):
            body += '<img class="pic" src="%s"/>' % _data_uri(m['image'])
        if m.get('note'):
            body += '<div class="att">%s</div>' % html.escape(m['note'])
        if m.get('text'):
            body += html.escape(_text(m['text'])).replace('\n', '<br/>')
        if m['side'] == 'in':
            out.append('<div class="row in"><table><tr><td style="padding-right:8px">%s</td><td><div class="bub">%s</div></td></tr></table></div>' % (mini if last_of_run else '<div class="mini"></div>', body))
        else:
            out.append('<div class="row out"><table><tr><td><div class="bub">%s</div></td></tr></table></div>' % body)
    out.append('</div>')
    if waiting_text:
        out.append('<div class="wait">%s</div>' % html.escape(waiting_text))
    out.append('</div></body></html>')
    return ''.join(out)


def render_image(page_html, binary='wkhtmltoimage'):
    """HTML → ảnh JPEG bytes (HTML đưa qua stdin, tắt đọc file cục bộ và JavaScript). Ném RuntimeError nếu wkhtmltoimage lỗi."""
    proc = subprocess.run([binary, '--quiet', '--format', 'png', '--width', str(WIDTH), '--disable-javascript',
                           '--disable-local-file-access', '--encoding', 'utf-8', '--zoom', '2', '-', '-'],
                          input=page_html.encode('utf-8'), capture_output=True, timeout=45)
    if proc.returncode not in (0, 1) or not proc.stdout.startswith(b'\x89PNG'):
        raise RuntimeError('wkhtmltoimage lỗi: %s' % proc.stderr.decode('utf-8', 'replace')[:300])
    return _compact(proc.stdout)


def _compact(png):
    """wkhtmltoimage xuất PNG rất nặng (vài MB); nén lại thành JPEG vài trăm KB để gửi Zalo nhanh."""
    from PIL import Image
    import io
    image = Image.open(io.BytesIO(png)).convert('RGB')
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=88, optimize=True)
    return out.getvalue()
