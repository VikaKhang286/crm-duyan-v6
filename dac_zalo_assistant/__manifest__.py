{
    'name': 'Trợ lý DAC qua Zalo',
    'version': '18.0.1.0.0',
    'category': '1. Duy An ERP',
    'summary': 'Liên kết nhân viên, hội thoại Zalo và AI tương thích OpenAI',
    'license': 'LGPL-3',
    'depends': ['dac_erp'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/config.xml',
        'data/cron.xml',
        'views/assistant_views.xml',
    ],
    'application': True,
    'installable': True,
}
