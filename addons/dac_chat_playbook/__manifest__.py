{
    'name': 'Phân tích chat & quy tắc chatbot',
    'version': '18.0.1.8.1',
    'category': '1. Duy An ERP',
    'summary': 'Model AI được chỉ định đọc lại tin nhắn Pancake, thống kê cách nhân viên nhắn và đề xuất quy tắc cho chatbot chào khách',
    'description': """
Bước chuẩn bị cho chatbot chào khách mới:

* Thống kê TOÀN BỘ tin Pancake trong kỳ: mỗi lượt nhân viên gửi mấy tin, mỗi tin bao nhiêu ký tự, xưng hô/từ ngữ, câu mở đầu,
  thông tin hay xin và thứ tự hỏi, tốc độ trả lời, khách nhắn ngoài giờ; so với kết quả (khách nhắn lại, báo giá, chốt đơn, để SĐT).
* Model AI do quản lý chỉ định (Kết nối AI dùng chung) đọc hội thoại mẫu tốt/chưa tốt và đề xuất quy tắc kèm bằng chứng.
* Quy tắc chỉ có hiệu lực khi quản lý duyệt. Có sẵn các quy tắc an toàn: không báo giá, không cam kết, không nhận cọc,
  chuyển nhân viên khi cần, hẹn mốc làm việc kế tiếp khi khách nhắn ngoài giờ.
* Chạy nền bằng cron, mỗi lần một lô, an toàn với giới hạn thời gian của worker. Chỉ đọc dữ liệu Pancake, không gửi tin cho khách.
    """,
    'author': 'Duy An',
    'depends': ['dac_pancake_remind', 'dac_ai_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'data/rule_data.xml',
        'data/knowledge_data.xml',
        'views/chat_knowledge_views.xml',
        'views/chat_dashboard_views.xml',
        'views/chat_playbook_views.xml',
        'views/chat_task_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dac_chat_playbook/static/src/progress/*',
            'dac_chat_playbook/static/src/dashboard/*',
            'dac_chat_playbook/static/src/demo/*',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
