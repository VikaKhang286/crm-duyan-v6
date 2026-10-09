/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const SLOT_CLASS = "dac-sale-order-control-panel-slot";
const ACTIVE_CLASS = "dac-sale-order-control-panel-active";
const FLOATING_MENU_CLASS = "dac-title-actions-menu--floating";
const STATE_CLASS_PREFIX = "dac-order-state-";
const REVIEW_CLASS = "dac-workflow-review-mode";

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
  return controller.model?.root?.data?.order_state_custom || null;
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

    this.notification = useService("notification");
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
    this.syncWorkflowReviewMode();
    this.bindNewSaleOrderButton(sourceBar);
    this.bindWorkflowReviewSteps(sourceBar);
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

  bindWorkflowReviewSteps(sourceBar) {
    const statusbar = sourceBar.querySelector(".dac-control-panel-progress .o_statusbar_status");
    if (!statusbar) {
      return;
    }

    const steps = Array.from(statusbar.querySelectorAll(".o_arrow_button[data-value]"));
    const currentStep = statusbar.querySelector(".o_arrow_button_current[data-value]");
    const availableValues = new Set(steps.map((step) => step.dataset.value));
    // The server action “Về bước hiện tại” updates workflow_review_state and
    // reloads the form.  Do not keep a previously clicked value in the
    // controller after that reload, otherwise the progress bar and the form
    // body can show two different steps.
    const persistedReviewedState = this.model?.root?.data?.workflow_review_state;
    const actualState = this.model?.root?.data?.order_state_custom;
    this.__dacReviewedWorkflowState = availableValues.has(persistedReviewedState)
      ? persistedReviewedState
      : currentStep?.dataset.value || null;

    const enableReviewSteps = () => {
      for (const step of statusbar.querySelectorAll(".o_arrow_button[data-value]")) {
        if (step.disabled || step.hasAttribute("disabled")) {
          step.disabled = false;
          step.removeAttribute("disabled");
        }
      }
    };
    enableReviewSteps();
    window.requestAnimationFrame(enableReviewSteps);

    for (const step of steps) {
      // The order workflow remains read-only.  These buttons only choose the
      // step being reviewed and must never trigger Odoo's state transition.
      step.disabled = false;
      step.removeAttribute("disabled");
      const isCurrentStep = step.dataset.value === actualState;
      const isReviewingHistory = this.__dacReviewedWorkflowState !== actualState;
      // The statusbar field is bound to workflow_review_state, so Odoo would
      // otherwise move this class to the historical step. Keep it anchored to
      // the real workflow state for both semantics and styling.
      step.classList.toggle("o_arrow_button_current", isCurrentStep);
      if (isCurrentStep) {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
      step.classList.toggle(
        "dac-workflow-step-selected",
        isReviewingHistory && step.dataset.value === this.__dacReviewedWorkflowState
      );
      step.setAttribute("aria-pressed", String(isReviewingHistory && step.dataset.value === this.__dacReviewedWorkflowState));
      step.setAttribute("title", `Xem bước: ${step.textContent.trim()}`);
    }

    if (this.__dacWorkflowReviewBar === statusbar) {
      return;
    }
    this.__dacWorkflowReviewCleanup?.();
    const observer = new MutationObserver(enableReviewSteps);
    observer.observe(statusbar, { attributes: true, attributeFilter: ["disabled"], subtree: true });
    const onClick = async (event) => {
      const step = event.target.closest(".o_arrow_button[data-value]");
      if (!step || !statusbar.contains(step)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();

      const currentState = this.model?.root?.data?.order_state_custom;
      const workflow = availableValues.has("installation")
        ? ["quotation", "deposit", "production", "installation", "completed"]
        : ["quotation", "deposit", "production", "delivery", "completed"];
      const currentIndex = workflow.indexOf(currentState);
      const targetIndex = workflow.indexOf(step.dataset.value);
      // Historical steps may be inspected. A later step is not available for
      // review until the actual workflow has reached it.
      if (currentIndex >= 0 && targetIndex > currentIndex) {
        this.notification.add(
          "Hoàn tất bước hiện tại trước khi mở bước này",
          { type: "warning" }
        );
        // The native statusbar widget may already have updated its local
        // display. Reload from the server after the toast is visible so it
        // cannot leave a future step displayed in the form.
        window.setTimeout(() => this.model?.root?.load(), 1200);
        return;
      }
      // Clicking the actual current step is a quick way to leave review mode.
      // Do nothing only when the form is already on that step.
      if (
        step.dataset.value === currentState &&
        this.__dacReviewedWorkflowState === currentState
      ) {
        return;
      }
      this.__dacReviewedWorkflowState = step.dataset.value;
      const isReviewingHistory = step.dataset.value !== currentState;
      for (const item of statusbar.querySelectorAll(".o_arrow_button[data-value]")) {
        const selected = isReviewingHistory && item === step;
        item.classList.toggle("o_arrow_button_current", item.dataset.value === currentState);
        item.classList.toggle("dac-workflow-step-selected", selected);
        item.setAttribute("aria-pressed", String(selected));
      }
      if (this.model?.root) {
        await this.model.root.update({ workflow_review_state: step.dataset.value });
        if (this.model.root.isDirty) {
          await this.save();
        }
        // Reload only the record data. This re-evaluates the server-side
        // invisible expressions without reloading the whole browser action.
        await this.model.root.load();
      }
    };
    statusbar.addEventListener("click", onClick, true);
    this.__dacWorkflowReviewBar = statusbar;
    this.__dacWorkflowReviewCleanup = () => {
      observer.disconnect();
      statusbar.removeEventListener("click", onClick, true);
    };
  },

  cleanupSaleOrderControlPanel() {
    this.__dacNewOrderCleanup?.();
    this.__dacNewOrderCleanup = null;
    this.__dacNewOrderBar = null;
    this.__dacWorkflowReviewCleanup?.();
    this.__dacWorkflowReviewCleanup = null;
    this.__dacWorkflowReviewBar = null;
    this.__dacReviewedWorkflowState = null;
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

  syncWorkflowReviewMode() {
    const data = this.model?.root?.data;
    const reviewedState = data?.workflow_review_state || this.__dacReviewedWorkflowState;
    const isReviewing = Boolean(
      reviewedState &&
      data?.order_state_custom &&
      reviewedState !== data.order_state_custom
    );
    this.el?.classList.toggle(REVIEW_CLASS, isReviewing);
    document.querySelector(".dac-sale-form")?.classList.toggle(REVIEW_CLASS, isReviewing);
  },

  clearSaleOrderStateClass() {
    syncStateClass(document.body, null);
    syncStateClass(document.querySelector(".o_web_client"), null);
    syncStateClass(document.querySelector(".dac-sale-form"), null);
    syncStateClass(this.el, null);
    syncStateClass(getActionManager(this), null);
    syncStateClass(getControlPanel(this), null);
    this.el?.classList.remove(REVIEW_CLASS);
    document.querySelector(".dac-sale-form")?.classList.remove(REVIEW_CLASS);
  },
});
