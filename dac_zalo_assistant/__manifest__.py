{
    'name': 'Trợ lý DAC qua Zalo',
    'version': '18.0.2.14.0',
    'category': '1. Duy An ERP',
    'summary': 'Hội thoại, thông báo nghiệp vụ Zalo, đích nhận thông báo (nhóm/chat, nhắc tên @tag) và AI tương thích OpenAI',
    'license': 'LGPL-3',
    'depends': ['dac_erp'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/config.xml',
        'data/cron.xml',
        'data/channels.xml',
        'views/assistant_views.xml',
        'views/notification_views.xml',
        'views/channel_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dac_zalo_assistant/static/src/scss/zalo_config.scss',
            'dac_zalo_assistant/static/src/scss/zalo_ui.scss',
            'dac_zalo_assistant/static/src/js/member_tags.js',
            'dac_zalo_assistant/static/src/xml/member_tags.xml',
        ],
    },
    'application': True,
    'installable': True,
}
