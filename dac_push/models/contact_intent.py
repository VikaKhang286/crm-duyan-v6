"""Nhận ra câu trả lời kiểu "đã liên hệ", "da goi roi", "gọi rồi ạ"... của nhân viên (không phân biệt dấu, hoa/thường)."""
import re
import unicodedata

THANKS_TEXT = 'Em xin cảm ơn ạ'

# Từ phủ định: "chưa gọi", "không liên hệ được", "ko gọi được"... không phải đã liên hệ → bỏ qua
NEGATIONS = {'chua', 'khong', 'ko', 'k', 'chang', 'hok', 'hong'}
DONE_WORDS = {'da', 'roi', 'xong', 'rui', 'r'}
ACTION_WORDS = {'goi', 'call', 'lh', 'lienhe', 'lienlac', 'nt', 'alo'}
ACTION_PAIRS = {('lien', 'he'), ('lien', 'lac'), ('nhan', 'tin'), ('goi', 'dien'), ('goi', 'dt')}
MAX_WORDS = 25


def fold(text):
    """Bỏ dấu tiếng Việt, đ → d, chữ thường, mọi ký tự không phải chữ/số thành khoảng trắng."""
    text = unicodedata.normalize('NFD', (text or '').replace('đ', 'd').replace('Đ', 'D'))
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn').lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def means_contacted(text):
    """True nếu câu nói rằng đã liên hệ/gọi xong khách: có từ "đã/rồi/xong" và một hành động liên hệ, không có từ phủ định."""
    words = fold(text).split()
    if not words or len(words) > MAX_WORDS or NEGATIONS & set(words):
        return False
    if not DONE_WORDS & set(words):
        return False
    return bool(ACTION_WORDS & set(words)) or any(pair in ACTION_PAIRS for pair in zip(words, words[1:]))
