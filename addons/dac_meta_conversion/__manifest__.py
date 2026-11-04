{
    'name': 'Chuyển đổi Meta',
    'version': '18.0.1.2.0',
    'category': '1. Duy An ERP',
    'summary': 'Gửi Purchase về Meta khi tạo đơn hàng trong CRM',
    'depends': ['CRM_DAC', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'security/meta_conversion_rules.xml',
        'data/ir_cron.xml',
        'views/meta_api_log_views.xml',
        'views/meta_conversion_views.xml',
        'views/res_config_settings_views.xml',
        'views/sale_order_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'assets': {
        'web.assets_backend': [
            'dac_meta_conversion/static/src/scss/meta_setup.scss',
        ],
    },
    'license': 'LGPL-3',
}
