/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { BurgerMenu } from "@web/webclient/burger_menu/burger_menu";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, useState } from "@odoo/owl";

const DAC_ROLE_LABELS = {
    manager: "Manager",
    sale_all: "Sale",
    sale: "Sale",
    design: "Thiết kế",
    production: "Sản xuất",
    design_production: "Thiết kế + Sản xuất",
    full_stack: "Full-stack",
};

patch(BurgerMenu.prototype, {
    setup() {
        super.setup(...arguments);
        const orm = useService("orm");
        this.dacIdentity = useState({ roleLabel: "" });
        onWillStart(async () => {
            try {
                const [currentUser] = await orm.read(
                    "res.users",
                    [this.user.userId],
                    ["dac_role"]
                );
                this.dacIdentity.roleLabel = DAC_ROLE_LABELS[currentUser?.dac_role] || "";
            } catch {
                this.dacIdentity.roleLabel = "";
            }
        });
    },
});

patch(NavBar.prototype, {
    toggleDacSidebar() {
        document.body.classList.toggle("dac-v6-sidebar-collapsed");
    },

    refreshDacPage() {
        window.location.reload();
    },

    filterDacMenu(event) {
        const query = event.target.value.trim().toLocaleLowerCase();
        const shell = event.target.closest(".dac_v6_shell");
        for (const item of shell?.querySelectorAll(".dac_v6_section_item") || []) {
            item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase().includes(query);
        }
        for (const group of shell?.querySelectorAll(".dac_v6_section_group") || []) {
            const children = [...group.querySelectorAll(".dac_v6_section_item")];
            group.hidden = Boolean(query) && children.length > 0 && children.every((item) => item.hidden);
        }
    },

    openDacMessages(event) {
        const shell = event.currentTarget.closest(".dac_v6_shell");
        const messageIcon = shell?.querySelector(".o_menu_systray .fa-comments");
        messageIcon?.closest("button, .dropdown-toggle")?.click();
    },

    openDacActivities(event) {
        const shell = event.currentTarget.closest(".dac_v6_shell");
        const activityIcon = shell?.querySelector(".o_menu_systray .fa-clock-o");
        activityIcon?.closest("button, .dropdown-toggle")?.click();
    },
});
