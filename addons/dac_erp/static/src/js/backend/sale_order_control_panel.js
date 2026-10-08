/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";

const SLOT_CLASS = "dac-sale-order-control-panel-slot";
const ACTIVE_CLASS = "dac-sale-order-control-panel-active";
const FLOATING_MENU_CLASS = "dac-title-actions-menu--floating";
const STATE_CLASS_PREFIX = "dac-order-state-";

function isSaleOrderForm(controller) {
  return controller.props?.resModel === "sale.order";
}

function getControlPanel(controller) {
  const actionManager = getActionManager(controller);
  if (actionManager) {
    return actionManager.querySelector(".o_control_panel");
  }
  return document.querySelector(".o_control_panel");
}

function getActionManager(controller) {
  return controller.el?.closest(".o_action_manager") || document.querySelector(".o_action_manager");
}

function getSourceBar(controller) {
  return (
    controller.el?.querySelector(".dac-control-panel-bar") ||
    document.querySelector(".dac-sale-form .dac-control-panel-bar")
  );
}

function getCurrentOrderState(controller) {
  const sourceBar = getSourceBar(controller);
  const currentStep = sourceBar?.querySelector(".dac-control-panel-progress .o_arrow_button_current[data-value]");
  return currentStep?.dataset.value || null;
}

function syncStateClass(target, state) {
  if (!target) {
    return;
  }

  for (const className of Array.from(target.classList)) {
    if (className.startsWith(STATE_CLASS_PREFIX)) {
      target.classList.remove(className);
    }
  }

  if (state) {
    target.classList.add(`${STATE_CLASS_PREFIX}${state}`);
  }
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function resetActionMenuPosition(menu) {
  menu.classList.remove(FLOATING_MENU_CLASS);
  menu.style.position = "";
  menu.style.left = "";
  menu.style.top = "";
  menu.style.right = "";
  menu.style.bottom = "";
  menu.style.inset = "";
  menu.style.transform = "";
  menu.style.maxWidth = "";
}

function positionActionMenu(toggle, menu) {
  if (!toggle || !menu) {
    return;
  }

  menu.classList.add(FLOATING_MENU_CLASS);
  menu.style.position = "fixed";
  menu.style.inset = "auto";
  menu.style.transform = "none";
  menu.style.maxWidth = `${Math.max(window.innerWidth - 32, 220)}px`;

  const toggleRect = toggle.getBoundingClientRect();
  const menuWidth = menu.offsetWidth || menu.scrollWidth || 260;
  const left = clamp(toggleRect.right - menuWidth, 16, window.innerWidth - menuWidth - 16);
  const top = clamp(toggleRect.bottom + 8, 16, window.innerHeight - menu.offsetHeight - 16);

  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.style.right = "auto";
  menu.style.bottom = "auto";
}

function replacePrintFormControls(root) {
  for (const control of root.querySelectorAll("input, textarea, select")) {
    const value = control.tagName === "SELECT"
      ? control.options[control.selectedIndex]?.text || ""
      : control.value || "";
    const text = document.createElement("span");
    text.className = "dac-print-field-value";
    text.textContent = value;
    control.replaceWith(text);
  }
}

function openBrowserPrintPreview() {
  document.querySelector(".dac-browser-print-view")?.remove();

  const orderTitle = document.querySelector(".dac-control-panel-title")?.textContent?.trim() || "Đơn hàng";
  const sourceTable = document.querySelector(".dac-order-lines-panel .o_list_table");
  const rowCount = sourceTable?.querySelectorAll("tbody tr.o_data_row").length || 0;

  const printView = document.createElement("section");
  printView.className = "dac-browser-print-view";
  printView.innerHTML = `
    <h1></h1>
    <div class="dac-print-section-title"><h2>Sản phẩm</h2><b>${rowCount} dòng</b></div>
    <div class="dac-print-products"></div>
  `;
  printView.querySelector("h1").textContent = orderTitle;

  if (sourceTable) {
    const table = sourceTable.cloneNode(true);
    table.querySelectorAll(".o_list_record_selector, .o_list_record_remove, .o_handle_cell, .o_optional_columns_dropdown").forEach((el) => el.remove());
    replacePrintFormControls(table);
    printView.querySelector(".dac-print-products").appendChild(table);
  } else {
    printView.querySelector(".dac-print-products").textContent = "Chưa có sản phẩm.";
  }

  document.body.appendChild(printView);
  const cleanup = () => printView.remove();
  window.addEventListener("afterprint", cleanup, { once: true });
  window.requestAnimationFrame(() => window.print());
}

patch(FormController.prototype, {
  setup() {
    super.setup();

    if (!isSaleOrderForm(this)) {
      return;
    }

    this.__dacSaleControlPanelRetries = 0;

    onMounted(() => {
      this.mountSaleOrderControlPanel();
      this.syncSaleOrderDirtyState();
    });

    onPatched(() => {
      this.mountSaleOrderControlPanel();
      this.syncSaleOrderDirtyState();
    });

    onWillUnmount(() => {
      this.cleanupSaleOrderControlPanel();
    });
  },

  mountSaleOrderControlPanel() {
    const controlPanel = getControlPanel(this);
    const sourceBar = getSourceBar(this);
    if (!controlPanel || !sourceBar) {
      if (this.__dacSaleControlPanelRetries < 10) {
        this.__dacSaleControlPanelRetries += 1;
        window.setTimeout(() => this.mountSaleOrderControlPanel(), 100);
      }
      return;
    }

    this.__dacSaleControlPanelRetries = 0;

    let slot = controlPanel.querySelector(`.${SLOT_CLASS}`);
    if (!slot) {
      slot = document.createElement("div");
      slot.className = SLOT_CLASS;
      controlPanel.appendChild(slot);
    }

    if (sourceBar.parentElement !== slot) {
      slot.replaceChildren(sourceBar);
    }

    controlPanel.classList.add(ACTIVE_CLASS);
    this.syncSaleOrderStateClass();
    this.bindNewSaleOrderButton(sourceBar);
    this.bindSaleOrderActionMenu();
    this.syncInternalNoteBadge();
    this.syncSaleOrderDirtyState();
  },

  syncInternalNoteBadge() {
    const notebook = document.querySelector(".dac-v6-side-notebook");
    const count = document.querySelector(".dac-v6-internal-count")?.textContent?.trim() || "0";
    const tab = Array.from(notebook?.querySelectorAll(".nav-link") || []).find(
      (item) => item.textContent.trim().startsWith("Nội bộ")
    );
    if (!tab) {
      return;
    }
    let badge = tab.querySelector(".dac-v6-tab-count");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "dac-v6-tab-count";
      tab.appendChild(badge);
    }
    badge.textContent = count;
  },

  bindNewSaleOrderButton(sourceBar) {
    if (this.__dacNewOrderBar === sourceBar) {
      return;
    }
    this.__dacNewOrderCleanup?.();
    const onClick = (event) => {
      if (event.target.closest(".dac-print-quotation-btn")) {
        event.preventDefault();
        event.stopPropagation();
        openBrowserPrintPreview();
        return;
      }
      if (!event.target.closest(".dac-new-order-btn")) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const newTab = window.open("about:blank", "_blank");
      if (newTab) {
        newTab.opener = null;
        newTab.location.replace("/odoo/sales/new");
      }
    };
    sourceBar.addEventListener("click", onClick, true);
    this.__dacNewOrderBar = sourceBar;
    this.__dacNewOrderCleanup = () => sourceBar.removeEventListener("click", onClick, true);
  },

  cleanupSaleOrderControlPanel() {
    this.__dacNewOrderCleanup?.();
    this.__dacNewOrderCleanup = null;
    this.__dacNewOrderBar = null;
    this.unbindSaleOrderActionMenu();
    this.clearSaleOrderStateClass();

    const controlPanel = getControlPanel(this);
    if (!controlPanel) {
      return;
    }

    controlPanel.classList.remove(ACTIVE_CLASS);
    controlPanel.querySelector(`.${SLOT_CLASS}`)?.remove();
  },

  bindSaleOrderActionMenu() {
    const sourceBar = getSourceBar(this);
    const dropdown = sourceBar?.querySelector(".dac-control-panel-tools");
    const toggle = dropdown?.querySelector(".dac-title-actions-toggle");
    const menu = dropdown?.querySelector(".dac-title-actions-menu");

    if (!dropdown || !toggle || !menu) {
      return;
    }

    if (this.__dacSaleActionMenuDropdown === dropdown) {
      if (menu.classList.contains("show")) {
        positionActionMenu(toggle, menu);
      }
      return;
    }

    this.unbindSaleOrderActionMenu();

    const onShown = () => {
      window.requestAnimationFrame(() => positionActionMenu(toggle, menu));
    };
    const onHidden = () => {
      resetActionMenuPosition(menu);
    };
    const onResize = () => {
      if (menu.classList.contains("show")) {
        positionActionMenu(toggle, menu);
      }
    };

    dropdown.addEventListener("shown.bs.dropdown", onShown);
    dropdown.addEventListener("hidden.bs.dropdown", onHidden);
    window.addEventListener("resize", onResize);

    this.__dacSaleActionMenuDropdown = dropdown;
    this.__dacSaleActionMenuCleanup = () => {
      dropdown.removeEventListener("shown.bs.dropdown", onShown);
      dropdown.removeEventListener("hidden.bs.dropdown", onHidden);
      window.removeEventListener("resize", onResize);
      resetActionMenuPosition(menu);
    };
  },

  unbindSaleOrderActionMenu() {
    this.__dacSaleActionMenuCleanup?.();
    this.__dacSaleActionMenuCleanup = null;
    this.__dacSaleActionMenuDropdown = null;
  },

  syncSaleOrderDirtyState() {
    const isDirty = this.model?.root?.isDirty ?? false;
    const saveBtn = document.querySelector(
      ".dac-sale-order-control-panel-slot button[name='action_save_custom'], " +
      ".dac-control-panel-bar button[name='action_save_custom']"
    );
    if (saveBtn) {
      saveBtn.classList.toggle("dac-save-btn-dirty", isDirty);
    }
  },

  syncSaleOrderStateClass() {
    const state = getCurrentOrderState(this);
    syncStateClass(document.body, state);
    syncStateClass(document.querySelector(".o_web_client"), state);
    syncStateClass(document.querySelector(".dac-sale-form"), state);
    syncStateClass(this.el, state);
    syncStateClass(getActionManager(this), state);
    syncStateClass(getControlPanel(this), state);
  },

  clearSaleOrderStateClass() {
    syncStateClass(document.body, null);
    syncStateClass(document.querySelector(".o_web_client"), null);
    syncStateClass(document.querySelector(".dac-sale-form"), null);
    syncStateClass(this.el, null);
    syncStateClass(getActionManager(this), null);
    syncStateClass(getControlPanel(this), null);
  },
});
