{
    'name': 'Trợ lý DAC qua Zalo',
    'version': '18.0.2.3.0',
    'category': '1. Duy An ERP',
    'summary': 'Hội thoại, thông báo nghiệp vụ Zalo và AI tương thích OpenAI',
    'license': 'LGPL-3',
    'depends': ['dac_erp'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/config.xml',
        'data/cron.xml',
        'views/assistant_views.xml',
        'views/notification_views.xml',
    ],
    'application': True,
    'installable': True,
}
