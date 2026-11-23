/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ListDateTimeField, listDateRangeField } from "@web/views/fields/datetime/list_datetime_field";

// Keep the full picker while editing, but display a single day without an arrow.
export class HolidayDateRangeField extends ListDateTimeField {
    isSingleDay() {
        const [start, end] = this.values;
        return !end || (start && start.toISODate() === end.toISODate());
    }

    shouldShowSeparator() {
        return this.props.readonly && this.isSingleDay() ? false : super.shouldShowSeparator();
    }

    getFormattedValue(index) {
        if (this.props.readonly && index === 1 && this.isSingleDay()) {
            return "";
        }
        return super.getFormattedValue(index);
    }
}

registry.category("fields").add("list.holiday_daterange", {
    ...listDateRangeField,
    component: HolidayDateRangeField,
});
