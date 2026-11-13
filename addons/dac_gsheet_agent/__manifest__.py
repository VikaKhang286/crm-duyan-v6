{
    'name': 'DAC Google Sheet AI Agent (Xe đẩy)',
    'version': '18.0.1.0.0',
    'category': '1. Duy An ERP',
    'summary': 'Kết nối Google Sheet, so khớp đơn xe đẩy và trợ lý AI tự rút kinh nghiệm',
    'description': """
Kết nối file Google Sheet (service account, chế độ chỉ xem / chỉnh sửa),
kiểm tra tự động chênh lệch giữa sheet và đơn hàng xe đẩy, khung chat với
AI Agent có ngữ cảnh đơn hàng + file sheet. Agent đề xuất thay đổi để người
dùng duyệt và tự ghi nhớ quy tắc làm việc theo mô hình playbook
(Agentic Context Engineering: generator → reflector → curator).
""",
    'author': 'Duy An',
    'depends': ['base', 'web', 'mail', 'sale', 'dac_erp'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/gsheet_source_views.xml',
        'views/agent_views.xml',
        'views/sale_order_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dac_gsheet_agent/static/src/agent_chat/*',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
