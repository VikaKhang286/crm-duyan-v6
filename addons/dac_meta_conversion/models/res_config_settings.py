import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    meta_conversion_enabled = fields.Boolean(related='company_id.meta_conversion_enabled', readonly=False)
    meta_conversion_provider = fields.Selection(related='company_id.meta_conversion_provider', readonly=False)
    meta_conversion_setup_path = fields.Selection(related='company_id.meta_conversion_setup_path', readonly=False)
    meta_conversion_app_id = fields.Char(related='company_id.meta_conversion_app_id', readonly=False)
    meta_conversion_dataset_request = fields.Char(string='Đường dẫn thiết lập Dataset', compute='_compute_meta_dataset_request')
    meta_conversion_dataset_id = fields.Char(related='company_id.meta_conversion_dataset_id', readonly=False)
    meta_conversion_page_id = fields.Char(related='company_id.meta_conversion_page_id', readonly=False)
    meta_conversion_access_token = fields.Char(related='company_id.meta_conversion_access_token', readonly=False)
    meta_conversion_api_version = fields.Char(related='company_id.meta_conversion_api_version', readonly=False)
    meta_conversion_test_code = fields.Char(related='company_id.meta_conversion_test_code', readonly=False)
    meta_conversion_channel = fields.Selection(related='company_id.meta_conversion_channel', readonly=False)
    meta_conversion_checked_at = fields.Datetime(related='company_id.meta_conversion_checked_at', readonly=True)
    meta_conversion_check_message = fields.Text(related='company_id.meta_conversion_check_message', readonly=True)
    meta_conversion_check_status = fields.Selection([
        ('unchecked', 'Chưa kiểm tra'), ('ok', 'Đã xác nhận kết nối đọc'),
        ('partial', 'Cần kiểm tra thêm'), ('error', 'Chưa kết nối được'),
        ('stale', 'Thông tin đã đổi — cần kiểm tra lại'),
    ], compute='_compute_meta_check_status')

    @api.depends('company_id.meta_conversion_check_status', 'company_id.meta_conversion_check_fingerprint',
                 'meta_conversion_app_id', 'meta_conversion_dataset_id', 'meta_conversion_page_id',
                 'meta_conversion_access_token', 'meta_conversion_api_version', 'meta_conversion_channel')
    def _compute_meta_check_status(self):
        for settings in self:
            company = settings.company_id.sudo()
            status = company.meta_conversion_check_status or 'unchecked'
            if company.meta_conversion_checked_at and company.meta_conversion_check_fingerprint != company._meta_connection_fingerprint():
                status = 'stale'
            settings.meta_conversion_check_status = status

    @api.depends('meta_conversion_page_id')
    def _compute_meta_dataset_request(self):
        for settings in self:
            page_id = settings.meta_conversion_page_id or ''
            settings.meta_conversion_dataset_request = '/%s/dataset' % (
                page_id if re.fullmatch(r'[0-9]+', page_id) else '{PAGE_ID}')

    def action_meta_open_app_dashboard(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị viên được mở cấu hình ứng dụng Meta.'))
        app_id = self.meta_conversion_app_id or ''
        if not re.fullmatch(r'[0-9]+', app_id):
            raise UserError(_('Nhập Meta App ID dạng số ở bước 2 trước khi mở app đã chọn.'))
        return {'type': 'ir.actions.act_url', 'url': 'https://developers.facebook.com/apps/%s/' % app_id, 'target': 'new'}

    def set_values(self):
        super().set_values()
        self.company_id._check_meta_configuration()

    def action_meta_check_connection(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị viên được kiểm tra kết nối Meta.'))
        company = self.company_id.sudo()
        if company.meta_conversion_provider == 'pancake_pos':
            return self.action_pancake_connector_check_pos()
        try:
            company._check_meta_configuration(force=True)
        except ValidationError as exc:
            return self._meta_check_result(company, 'error', str(exc))
        Log = self.env['dac.meta.api.log']
        dataset = Log._call(company, 'dataset_check', 'GET', company.meta_conversion_dataset_id)
        if not dataset.get('ok') or str(dataset['data'].get('id') or '') != company.meta_conversion_dataset_id:
            return self._meta_check_result(company, 'error', _(
                'Chưa xác nhận được Dataset / token. Kiểm tra đúng Dataset ID và token có quyền truy cập. %(detail)s'
            ) % {'detail': dataset['log'].summary})
        message = _('Đã xác nhận: định dạng thông tin hợp lệ; token đọc được đúng Dataset đã nhập.')
        if company.meta_conversion_channel == 'messenger':
            linked = Log._call(company, 'page_dataset_check', 'GET', company.meta_conversion_page_id + '/dataset')
            if not linked.get('ok'):
                return self._meta_check_result(company, 'partial', message + '\n' + _(
                    'Chưa xác nhận được Page liên kết Dataset. Kiểm tra Page ID và quyền page_events của token trong Meta. %(detail)s'
                ) % {'detail': linked['log'].summary})
            data = linked['data']
            rows = data.get('data', [])
            ids = [str(row.get('id')) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            if data.get('id'):
                ids.append(str(data['id']))
            if company.meta_conversion_dataset_id not in ids:
                return self._meta_check_result(company, 'error' if ids else 'partial', message + '\n' + _(
                    'Page chưa trả về đúng Dataset đã nhập. Chọn Dataset đang liên kết với Page này trong Meta rồi kiểm tra lại.'
                ))
            message += '\n' + _('Đã xác nhận: Page đang liên kết đúng Dataset.')
        message += '\n' + _(
            'Kiểm tra này chưa gửi Purchase và chưa xác nhận quyền gửi hay phân bổ quảng cáo. '
            'Tiếp tục bước kiểm tra đơn và đối chiếu sự kiện trong Events Manager.'
        )
        return self._meta_check_result(company, 'ok', message)

    def _meta_check_result(self, company, status, message):
        company.write({
            'meta_conversion_checked_at': fields.Datetime.now(),
            'meta_conversion_check_status': status, 'meta_conversion_check_message': message,
            'meta_conversion_check_fingerprint': company._meta_connection_fingerprint(),
        })
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('Kết quả kiểm tra Chuyển đổi Meta'),
                'type': {'ok': 'success', 'partial': 'warning', 'error': 'danger'}[status],
                'message': message, 'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_meta_open_api_logs(self):
        self.ensure_one()
        action = self.env.ref('dac_meta_conversion.action_meta_api_logs').read()[0]
        action['domain'] = [('company_id', '=', self.company_id.id)]
        return action
