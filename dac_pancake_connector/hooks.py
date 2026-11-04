def post_init_hook(env):
    # The historic POS settings are global. Assign them only to the main
    # company; never silently share shop credentials with other companies.
    company = env.ref('base.main_company').sudo()
    params = env['ir.config_parameter'].sudo()
    values = {}
    for field, key in [('pancake_pos_api_key', 'pancake.api_key'), ('pancake_pos_shop_id', 'pancake.shop_id')]:
        if not company[field] and params.get_param(key):
            values[field] = params.get_param(key)
    if values:
        company.write(values)
