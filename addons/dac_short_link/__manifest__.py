{
    'name': 'Link rút gọn',
    'version': '18.0.1.1.0',
    'category': '1. Duy An ERP',
    'summary': 'Rút gọn link cho tin Zalo: /p/abc (Pancake, 3 ký tự) và /r/abcd (lời nhắc, khách liên hệ; 4 ký tự, chặn dò mã)',
    'description': """
Module khác gọi ``env['dac.short.link']._shorten(url)`` → "crm.duyan.vn/r/abc". Một link gốc luôn cho cùng một mã;
mở /r/<mã> chuyển thẳng tới link gốc và đếm lượt bấm. Mã 3 ký tự (bỏ ký tự dễ nhầm), kho mã gần đầy thì tự dùng 4 ký tự.

Mã ngắn đoán được nên CHỈ dùng cho link mà bản thân trang đích đã bắt đăng nhập (vd hội thoại Pancake), không dùng cho
link công khai có mã bí mật (phiếu hẹn, trang lead).
    """,
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/short_link_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
