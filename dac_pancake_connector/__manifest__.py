{
    'name': 'Kết nối Pancake',
    'version': '18.0.1.0.0',
    'category': '1. Duy An ERP',
    'summary': 'Quản lý kết nối Pancake Chat / POS và lịch sử API dùng chung',
    'depends': ['CRM_DAC', 'base_setup'],
    'data': ['security/ir.model.access.csv', 'security/rules.xml', 'views/connector_views.xml'],
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
    'license': 'LGPL-3',
}
