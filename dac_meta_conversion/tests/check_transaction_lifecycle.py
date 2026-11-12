"""Run with Odoo shell on an isolated dac_meta_test_* database only."""
from unittest.mock import Mock, patch

from odoo import api, registry, SUPERUSER_ID

MODULE = 'odoo.addons.dac_meta_conversion.models.meta_conversion_event'


def check_transaction_lifecycle(dbname):
    if not dbname.startswith('dac_meta_test_'):
        raise ValueError('This check only runs on a dedicated test database.')

    def make_env(cr):
        return api.Environment(cr, SUPERUSER_ID, {'tracking_disable': True})

    with registry(dbname).cursor() as cr:
        env = make_env(cr)
        company = env.company
        company.write({
            'meta_conversion_enabled': True, 'meta_conversion_channel': 'chat',
            'meta_conversion_dataset_id': '123456789',
            'meta_conversion_access_token': 'integration-test-token',
            'meta_conversion_api_version': 'v26.0', 'meta_conversion_test_code': False,
        })
        partner = env['res.partner'].create({'name': 'Meta transaction test', 'phone': '0909333444'})
        product = env['product.product'].create({'name': 'Meta transaction test', 'list_price': 100000})
        partner_id, product_id = partner.id, product.id
        company_id = company.id
        cr.commit()

    def order_values():
        return {'partner_id': partner_id, 'order_line': [(0, 0, {
            'product_id': product_id, 'product_uom_qty': 1, 'price_unit': 100000,
        })]}

    response = Mock(ok=True, status_code=200, json=Mock(return_value={'events_received': 1}))
    checks = []
    # All unexpected network traffic is blocked, including unrelated integrations.
    with patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network request')), \
            patch(MODULE + '.requests.post', return_value=response) as post:
        with registry(dbname).cursor() as cr:
            env = make_env(cr)
            order = env['sale.order'].create(order_values())
            event_id = order.meta_conversion_event_ids.id
            post.assert_not_called()

            def successful_send(*args, **kwargs):
                # A concurrent worker sees the committed sending claim and skips.
                with registry(dbname).cursor() as competing_cr:
                    competing_env = make_env(competing_cr)
                    logs = competing_env['dac.meta.api.log'].search([('event_id', '=', event_id)])
                    assert len(logs) == 1 and logs.state == 'started'
                    competing_env['dac.meta.conversion.event'].browse(event_id)._deliver()
                    competing_cr.commit()
                return response

            post.side_effect = successful_send
            cr.commit()
            assert post.call_count == 1
        with registry(dbname).cursor() as cr:
            event = make_env(cr)['dac.meta.conversion.event'].browse(event_id)
            assert event.state == 'sent'
            assert event.attempt_count == 1
            assert len(event.api_log_ids) == 1 and event.api_log_ids.state == 'success'
            event._deliver()
            cr.commit()
            assert post.call_count == 1
        checks.extend(['send_only_after_commit', 'accepted_state_persisted',
                       'concurrent_dispatch_does_not_duplicate', 'api_log_committed_before_network'])

        post.reset_mock()
        post.side_effect = None
        with registry(dbname).cursor() as cr:
            env = make_env(cr)
            order = env['sale.order'].create(order_values())
            rolled_back_event_id = order.meta_conversion_event_ids.id
            cr.rollback()
            post.assert_not_called()
        with registry(dbname).cursor() as cr:
            assert not make_env(cr)['dac.meta.conversion.event'].browse(rolled_back_event_id).exists()
            assert not make_env(cr)['dac.meta.api.log'].search_count([('event_id', '=', rolled_back_event_id)])
        checks.append('rollback_never_sends')

        with registry(dbname).cursor() as cr:
            env = make_env(cr)
            order = env['sale.order'].create({'partner_id': partner_id})
            waiting_order_id = order.id
            waiting_event_id = order.meta_conversion_event_ids.id
            cr.commit()
            post.assert_not_called()
        with registry(dbname).cursor() as cr:
            env = make_env(cr)
            assert env['dac.meta.conversion.event'].browse(waiting_event_id).state == 'waiting'
            env['sale.order'].browse(waiting_order_id).write({'order_line': order_values()['order_line']})
            cr.commit()
            assert post.call_count == 1
        with registry(dbname).cursor() as cr:
            assert make_env(cr)['dac.meta.conversion.event'].browse(waiting_event_id).state == 'sent'
        checks.append('incomplete_order_sends_after_data_is_added')

    with registry(dbname).cursor() as cr:
        env = make_env(cr)
        env['res.company'].browse(company_id).meta_conversion_enabled = False
        settings_arch = env['res.config.settings'].get_view(view_id=env.ref('base_setup.res_config_settings_view_form').id)['arch']
        assert 'name="dac_meta_conversion"' in settings_arch
        order_arch = env['sale.order'].get_view(view_id=env.ref('dac_erp.dac_sale_order_custom_view_form').id)['arch']
        assert 'meta_conversion_psid' in order_arch
        cr.commit()
    checks.append('settings_and_order_views_compile')
    return checks
