{
    'name': 'Kết nối AI dùng chung',
    'version': '18.0.1.2.0',
    'category': '1. Duy An ERP',
    'summary': 'Một nơi nhập API AI (9router/OpenAI-compatible, Gemini) cho mọi module Odoo: thứ tự chính → dự phòng, bắt buộc gọi tool',
    'description': """
Quản trị viên thêm các kết nối AI (Cài đặt → Kết nối AI dùng chung), sắp thứ tự. Module khác gọi:

* ``env['dac.ai.connection']._json(system, text, schema, name=...)`` → (dict, loại kết nối): model PHẢI gọi đúng tool ``name``
  với tham số theo ``schema`` (JSON Schema hoặc khuôn kiểu Gemini). Kết nối đầu lỗi/quá tải → kết nối kế tiếp.
* ``env['dac.ai.connection']._chat(messages)`` → (câu trả lời, loại kết nối) cho trò chuyện thường.
* ``env['dac.ai.connection']._ready()`` → có kết nối nào dùng được không.
    """,
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_connection_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
