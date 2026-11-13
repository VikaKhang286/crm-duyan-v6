"""Client tối giản cho Google Sheets API v4 bằng service account.

Không phụ thuộc google-auth/gspread (container Odoo không có): ký JWT RS256
bằng `cryptography` rồi đổi lấy access token qua OAuth2 jwt-bearer.
"""
import base64
import json
import logging
import re
import threading
import time
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_logger = logging.getLogger(__name__)

SCOPE_READ = 'https://www.googleapis.com/auth/spreadsheets.readonly'
SCOPE_WRITE = 'https://www.googleapis.com/auth/spreadsheets'
SHEETS_BASE = 'https://sheets.googleapis.com/v4/spreadsheets'
DEFAULT_TOKEN_URI = 'https://oauth2.googleapis.com/token'
TIMEOUT = (10, 30)

_TOKEN_CACHE = {}
_TOKEN_LOCK = threading.Lock()


class GSheetError(Exception):
    """Lỗi khi gọi Google Sheets (thông điệp thân thiện cho người dùng)."""


def parse_spreadsheet_id(url_or_id):
    """Lấy spreadsheet id từ link https://docs.google.com/spreadsheets/d/<id>/..."""
    if not url_or_id:
        return ''
    value = url_or_id.strip()
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', value)
    if match:
        return match.group(1)
    if re.fullmatch(r'[a-zA-Z0-9-_]{20,}', value):
        return value
    return ''


def parse_gid(url):
    match = re.search(r'[#&?]gid=(\d+)', url or '')
    return int(match.group(1)) if match else None


def column_letter(index):
    """0 → A, 25 → Z, 26 → AA."""
    index = int(index)
    letters = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def a1_range(sheet_name, cell_range=''):
    quoted = "'%s'" % (sheet_name or '').replace("'", "''")
    return '%s!%s' % (quoted, cell_range) if cell_range else quoted


def load_service_account(raw_json):
    if not raw_json:
        raise GSheetError('Chưa cấu hình Service Account JSON (Cài đặt → Trợ lý AI Sheet).')
    try:
        info = json.loads(raw_json)
    except ValueError as exc:
        raise GSheetError('Service Account JSON không hợp lệ: %s' % exc) from exc
    for key in ('client_email', 'private_key'):
        if not info.get(key):
            raise GSheetError('Service Account JSON thiếu trường "%s".' % key)
    return info


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def get_access_token(sa_info, scope):
    cache_key = (sa_info['client_email'], scope)
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] - 60 > time.time():
            return cached[0]

    token_uri = sa_info.get('token_uri') or DEFAULT_TOKEN_URI
    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if sa_info.get('private_key_id'):
        header['kid'] = sa_info['private_key_id']
    claims = {
        'iss': sa_info['client_email'],
        'scope': scope,
        'aud': token_uri,
        'iat': now,
        'exp': now + 3600,
    }
    signing_input = '%s.%s' % (
        _b64url(json.dumps(header, separators=(',', ':')).encode()),
        _b64url(json.dumps(claims, separators=(',', ':')).encode()),
    )
    try:
        key = serialization.load_pem_private_key(sa_info['private_key'].encode(), password=None)
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError) as exc:
        raise GSheetError('Không đọc được private_key của service account: %s' % exc) from exc

    assertion = '%s.%s' % (signing_input, _b64url(signature))
    try:
        resp = requests.post(token_uri, data={
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion,
        }, timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise GSheetError('Không kết nối được Google OAuth: %s' % exc) from exc
    if resp.status_code != 200:
        raise GSheetError('Google từ chối service account (%s): %s' % (resp.status_code, resp.text[:300]))
    data = resp.json()
    token = data['access_token']
    with _TOKEN_LOCK:
        _TOKEN_CACHE[cache_key] = (token, now + int(data.get('expires_in', 3600)))
    return token


class GSheetClient:
    """Một phiên làm việc với một spreadsheet."""

    def __init__(self, sa_info, spreadsheet_id, writable=False):
        if not spreadsheet_id:
            raise GSheetError('Link Google Sheet không hợp lệ.')
        self.sa_info = sa_info
        self.spreadsheet_id = spreadsheet_id
        self.writable = writable
        self.scope = SCOPE_WRITE if writable else SCOPE_READ

    # ------------------------------------------------------------------
    def _request(self, method, path, **kwargs):
        token = get_access_token(self.sa_info, self.scope)
        headers = {'Authorization': 'Bearer %s' % token}
        url = '%s/%s%s' % (SHEETS_BASE, self.spreadsheet_id, path)
        kwargs.setdefault('timeout', TIMEOUT)
        try:
            resp = requests.request(method, url, headers=headers, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise GSheetError('Không kết nối được Google Sheets: %s' % exc) from exc
        if resp.status_code == 403:
            raise GSheetError(
                'Không có quyền truy cập file. Hãy chia sẻ file cho %s với quyền %s.'
                % (self.sa_info['client_email'], 'Người chỉnh sửa' if self.writable else 'Người xem'))
        if resp.status_code == 404:
            raise GSheetError('Không tìm thấy file hoặc tab trên Google Sheet.')
        if resp.status_code >= 400:
            raise GSheetError('Google Sheets lỗi %s: %s' % (resp.status_code, resp.text[:300]))
        return resp.json()

    def _require_write(self):
        if not self.writable:
            raise GSheetError('File này đang ở chế độ CHỈ XEM — không được ghi.')

    # ------------------------------------------------------------------
    def get_metadata(self):
        data = self._request('GET', '', params={'fields': 'properties.title,sheets.properties'})
        return {
            'title': data.get('properties', {}).get('title', ''),
            'sheets': [
                {
                    'title': s['properties']['title'],
                    'sheet_id': s['properties']['sheetId'],
                    'rows': s['properties'].get('gridProperties', {}).get('rowCount'),
                }
                for s in data.get('sheets', [])
            ],
        }

    def get_values(self, sheet_name, cell_range=''):
        rng = quote(a1_range(sheet_name, cell_range), safe='')
        data = self._request('GET', '/values/%s' % rng, params={
            'valueRenderOption': 'FORMATTED_VALUE',
            'majorDimension': 'ROWS',
        })
        return data.get('values', [])

    def batch_update_cells(self, sheet_name, cells):
        """cells: list of (row_number 1-based, col_index 0-based, value)."""
        self._require_write()
        data = [
            {'range': a1_range(sheet_name, '%s%s' % (column_letter(col), row)), 'values': [[value]]}
            for row, col, value in cells
        ]
        if not data:
            return {}
        return self._request('POST', '/values:batchUpdate', json={
            'valueInputOption': 'USER_ENTERED',
            'data': data,
        })

    def append_row(self, sheet_name, values):
        self._require_write()
        rng = quote(a1_range(sheet_name, 'A1'), safe='')
        return self._request('POST', '/values/%s:append' % rng, params={
            'valueInputOption': 'USER_ENTERED',
            'insertDataOption': 'INSERT_ROWS',
        }, json={'values': [values]})
