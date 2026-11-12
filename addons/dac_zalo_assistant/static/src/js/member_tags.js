/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Many2ManyTagsField, many2ManyTagsField } from "@web/views/fields/many2many_tags/many2many_tags_field";

/**
 * Chọn thành viên nhóm Zalo để nhắc tên: giống many2many_tags nhưng danh sách thả xuống hiện đủ thành viên
 * (mặc định Odoo chỉ hiện 7 mục rồi bắt bấm "Tìm kiếm thêm").
 */
export class DacZaloMemberTagsField extends Many2ManyTagsField {
    static template = "dac_zalo_assistant.MemberTagsField";
}

export const dacZaloMemberTagsField = {
    ...many2ManyTagsField,
    component: DacZaloMemberTagsField,
    displayName: "Thành viên nhóm Zalo",
};

registry.category("fields").add("dac_zalo_member_tags", dacZaloMemberTagsField);
