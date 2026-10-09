from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WorkflowReviewRollbackWizard(models.TransientModel):
    _name = 'dac.workflow.review.rollback.wizard'
    _description = 'Xác nhận quay lại trạng thái đơn hàng'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    target_state = fields.Selection(
        selection=lambda self: self.env['sale.order']._fields['order_state_custom'].selection,
        required=True,
        readonly=True,
    )
    rollback_summary = fields.Char(compute='_compute_rollback_summary')
    reason = fields.Text(string='Lý do quay lại')

    @api.depends('order_id.name', 'order_id.order_state_custom', 'target_state')
    def _compute_rollback_summary(self):
        labels = dict(self.env['sale.order']._fields['order_state_custom'].selection)
        for wizard in self:
            current = labels.get(wizard.order_id.order_state_custom, '')
            target = labels.get(wizard.target_state, '')
            wizard.rollback_summary = '%s: %s → %s' % (
                wizard.order_id.name or '', current, target,
            )

    def action_confirm_rollback(self):
        self.ensure_one()
        before = self.order_id.order_state_custom
        self.order_id._rollback_to_reviewed_workflow_state(self.target_state)
        if self.reason:
            labels = dict(self.env['sale.order']._fields['order_state_custom'].selection)
            self.order_id.message_post(body=Markup(
                '<p><b>Quay lại trạng thái:</b> %s → %s</p><p><b>Lý do:</b> %s</p>'
            ) % (
                escape(labels.get(before, before)),
                escape(labels.get(self.target_state, self.target_state)),
                escape(self.reason),
            ))
        return {'type': 'ir.actions.client', 'tag': 'reload'}
