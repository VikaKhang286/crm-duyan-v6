/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useBus } from "@web/core/utils/hooks";
import { X2ManyFieldDialog } from "@web/views/fields/relational_utils";
import { executeButtonCallback } from "@web/views/view_button/view_button_hook";
import { onWillUnmount } from "@odoo/owl";

// A task card is rendered in the sale.order form through an inline One2many
// kanban.  Editing that task opens its own form model, so Odoo does not reload
// the parent form by itself after a successful save.  Notify the parent model
// explicitly so deadline, assignee and state shown on the card stay current.
const TASK_CARD_UPDATED_EVENT = "DAC-TASK-CARD:UPDATED";

function getMany2oneId(value) {
    if (Array.isArray(value)) {
        return value[0] || false;
    }
    return value?.resId || value?.id || false;
}

function getTaskOrderId(record) {
    return getMany2oneId(record?.data?.order_id);
}

function notifyParentOrder(env, taskRecord) {
    const orderId = getTaskOrderId(taskRecord);
    if (orderId) {
        env.bus.trigger(TASK_CARD_UPDATED_EVENT, { orderId });
    }
}

async function refreshTaskCards(controller, save) {
    const saved = await save();
    if (saved) {
        notifyParentOrder(controller.env, controller.model?.root);
    }
    return saved;
}

patch(FormController.prototype, {
    setup() {
        super.setup();

        if (this.props.resModel !== "sale.order") {
            return;
        }

        let refreshTimer;
        useBus(this.env.bus, TASK_CARD_UPDATED_EVENT, async ({ detail: { orderId } }) => {
            const root = this.model?.root;
            if (!root?.resId || Number(root.resId) !== Number(orderId)) {
                return;
            }

            // A reload would discard edits the user is currently making on the
            // sale order itself.  Its normal save flow will read the latest
            // task values afterwards.
            if (await root.isDirty()) {
                return;
            }

            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => this.model.load(), 0);
        });

        onWillUnmount(() => clearTimeout(refreshTimer));
    },

    async save(params) {
        if (this.props.resModel !== "dac.work.task") {
            return super.save(...arguments);
        }
        return refreshTaskCards(this, () => super.save(...arguments));
    },

    async beforeExecuteActionButton(clickParams) {
        if (this.props.resModel !== "dac.work.task") {
            return super.beforeExecuteActionButton(...arguments);
        }
        return refreshTaskCards(this, () => super.beforeExecuteActionButton(...arguments));
    },
});

patch(X2ManyFieldDialog.prototype, {
    async save({ saveAndNew } = {}) {
        // Odoo's default One2many dialog only validates changes back into the
        // parent sale.order draft.  Existing DAC tasks must instead be written
        // immediately, otherwise their deadline is not persisted until the
        // user also clicks the outer order's Save button.
        if (this.record?.resModel !== "dac.work.task" || !this.record.resId) {
            return super.save(...arguments);
        }

        return executeButtonCallback(this.modalRef.el, async () => {
            if (!(await this.record.checkValidity({ displayNotification: true }))) {
                return false;
            }

            const saved = await this.record.save({ reload: false });
            if (!saved) {
                return false;
            }

            this.record._restoreActiveFields();
            notifyParentOrder(this.env, this.record);

            if (saveAndNew) {
                await this.record.switchMode("readonly");
                this.record = await this.props.addNew();
            } else {
                this.props.close();
            }
            return true;
        });
    },
});
