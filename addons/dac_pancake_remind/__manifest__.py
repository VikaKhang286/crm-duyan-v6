{
    'name': 'Pancake: nhắc khách chưa trả lời + thống kê khách mới',
    'version': '18.0.4.1.0',
    'category': '1. Duy An ERP',
    'summary': 'Khách nhắn page Pancake mà quá hạn (giờ làm việc) chưa ai trả lời → bot gửi ảnh chụp hội thoại + tag tên vào Zalo',
    'description': """
Luồng Pancake riêng, KHÔNG dùng đồng bộ cũ của CRM_DAC: mỗi page một Page Access Token do quản trị viên tự nhập; cứ 10 phút lấy
hội thoại vừa cập nhật và tin gần nhất của các page đã chọn (chỉ đọc). Khách nhắn mà sau N phút làm việc (mặc định 15) vẫn chưa có nhân viên trả lời →
dựng ảnh giống ảnh chụp màn hình cuộc trò chuyện (avatar, tên khách, các tin gần nhất) và gửi vào các hội thoại Zalo đã
đăng ký loại thông báo "Khách Pancake chưa được trả lời" (Trợ lý Zalo → Loại thông báo), kèm tag tên và link mở thẳng hội thoại.
Thả cảm xúc lên tin nhắc KHÔNG làm dừng nhắc; chỉ khi có nhân viên trả lời trên Pancake mới dừng.
    """,
    'depends': ['dac_erp', 'dac_zalo_assistant', 'dac_push', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/remind_data.xml',
        'views/pancake_views.xml',
        'views/remind_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dac_pancake_remind/static/src/dashboard/contact_dashboard.js',
            'dac_pancake_remind/static/src/dashboard/contact_dashboard.xml',
            'dac_pancake_remind/static/src/dashboard/contact_dashboard.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
