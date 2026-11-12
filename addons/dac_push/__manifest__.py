{
    'name': 'Lead Website — Push DAC Odoo',
    'version': '18.0.1.3.0',
    'category': '1. Duy An ERP',
    'summary': 'Nhận dữ liệu đẩy từ duyan.vn (plugin Push DAC Odoo) và báo vào Zalo',
    'description': """
duyan.vn (plugin WordPress "Push DAC Odoo") → POST ký HMAC → /dac_push/v1/events
→ lưu dac.push.event (chống trùng) → xử lý theo event_type (hiện có: web_lead.created)
→ gửi thông báo vào hội thoại Zalo đã chọn trong Trợ lý Zalo.

Thêm loại dữ liệu mới: kế thừa dac.push.event và viết method _handle_<event_type> (dấu chấm → gạch dưới),
đăng ký dac.zalo.event.type nếu muốn có thông báo Zalo.
    """,
    'depends': ['dac_erp', 'dac_zalo_assistant'],
    'data': [
        'security/ir.model.access.csv',
        'data/push_data.xml',
        'views/push_views.xml',
        'views/lead_public_templates.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
