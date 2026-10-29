/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

const SLOT_CLASS = "dac-sale-order-control-panel-slot";
const ACTIVE_CLASS = "dac-sale-order-control-panel-active";
const FLOATING_MENU_CLASS = "dac-title-actions-menu--floating";
const STATE_CLASS_PREFIX = "dac-order-state-";
const REVIEW_CLASS = "dac-workflow-review-mode";
const SALE_ORDER_PHONE_SELECTOR = '.o_field_widget[name="phone"] input';
let activeSaleOrderController = null;

registry.category("actions").add("dac_soft_reload_sale_order", async () => {
  await activeSaleOrderController?.model?.root?.load();
});

patch(Many2XAutocomplete.prototype, {
  async loadOptionsSource(request) {
    const options = await super.loadOptionsSource(request);
    if (this.props.resModel !== "res.partner") {
      return options;
    }

    const partnerOptions = options.filter((option) => Number.isInteger(option.value));
    if (!partnerOptions.length) {
      return options;
    }

    const partners = await this.orm.read(
      "res.partner",
      partnerOptions.map((option) => option.value),
      ["phone", "mobile"]
    );
    const phoneById = new Map(
      partners.map((partner) => [partner.id, partner.phone || partner.mobile || "Chưa có số điện thoại"])
    );
    for (const option of partnerOptions) {
      option.label = `${option.label}   ☎ ${phoneById.get(option.value) || "Chưa có số điện thoại"}`;
    }
    return options;
  },
});

function configureSaleOrderPhoneInput(input) {
  input.inputMode = "numeric";
  input.maxLength = 10;
  input.setAttribute("pattern", "[0-9]{10}");
  input.setAttribute("autocomplete", "tel-national");
  const digits = input.value.replace(/[^0-9]/g, "");
  if (digits.length === 10 && input.value !== digits) {
    input.value = digits;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

document.addEventListener("focusin", (event) => {
  const input = event.target.closest?.(SALE_ORDER_PHONE_SELECTOR);
  if (input) {
    configureSaleOrderPhoneInput(input);
    activeSaleOrderController?.bindCustomerPhoneAutocomplete(input);
    activeSaleOrderController?.__dacPhoneAutocompleteLoad?.();
  }
}, true);

document.addEventListener("beforeinput", (event) => {
  const input = event.target;
  if (!input.matches?.(SALE_ORDER_PHONE_SELECTOR) || !event.inputType?.startsWith("insert")) {
    return;
  }
  const inserted = event.data ?? event.dataTransfer?.getData("text/plain");
  if (inserted == null) {
    return;
  }
  const selectedLength = (input.selectionEnd || 0) - (input.selectionStart || 0);
  if (/[^0-9]/.test(inserted) || input.value.length - selectedLength + inserted.length > 10) {
    event.preventDefault();
  }
}, true);

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!input.matches?.(SALE_ORDER_PHONE_SELECTOR)) {
    return;
  }
  const normalized = input.value.replace(/[^0-9]/g, "").slice(0, 10);
  if (input.value !== normalized) {
    input.value = normalized;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  activeSaleOrderController?.__dacPhoneAutocompleteSchedule?.();
}, true);

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
    this.orm = useService("orm");
    this.dialog = useService("dialog");
    activeSaleOrderController = this;
    this.__dacSaleControlPanelRetries = 0;

    onMounted(() => {
      this.mountSaleOrderControlPanel();
      this.bindSaleOrderPageTitle();
      this.syncSaleOrderPageTitle();
      this.syncSaleOrderDirtyState();
    });

    onPatched(() => {
      this.mountSaleOrderControlPanel();
      this.syncSaleOrderPageTitle();
      this.syncSaleOrderDirtyState();
      this.syncSaleOrderEditControls();
    });

    onWillUnmount(() => {
      if (activeSaleOrderController === this) {
        activeSaleOrderController = null;
      }
      this.__dacTaskStatusSavePromptCleanup?.();
      this.__dacSaleOrderPageTitleCleanup?.();
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
    this.bindCustomerPhoneAutocomplete();
    this.bindSaleOrderEditControls(sourceBar);
    this.bindTaskStatusSavePrompt();
    this.bindWorkflowReviewSteps(sourceBar);
    this.bindSaleOrderActionMenu();
    this.syncWorkflowNextStep(sourceBar);
    this.syncInternalNoteBadge();
    this.syncSaleOrderDirtyState();
    this.syncSaleOrderEditControls();
  },

  syncSaleOrderPageTitle() {
    const displayedTitle = document.querySelector(".dac-control-panel-title")?.textContent || "";
    const orderCode = String(this.model?.root?.data?.order_number || displayedTitle.split("·")[0] || "").trim();
    if (orderCode && document.title !== orderCode) {
      document.title = orderCode;
    }
  },

  bindSaleOrderPageTitle() {
    if (this.__dacSaleOrderPageTitleCleanup) {
      return;
    }
    const titleElement = document.querySelector("title");
    if (!titleElement) {
      return;
    }
    const observer = new MutationObserver(() => this.syncSaleOrderPageTitle());
    observer.observe(titleElement, { childList: true, characterData: true, subtree: true });
    const timers = [0, 80, 250, 600, 1200].map((delay) =>
      window.setTimeout(() => this.syncSaleOrderPageTitle(), delay)
    );
    this.__dacSaleOrderPageTitleCleanup = () => {
      observer.disconnect();
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  },

  bindSaleOrderEditControls(sourceBar) {
    if (this.__dacSaleOrderEditBar === sourceBar) {
      return;
    }
    this.__dacSaleOrderEditCleanup?.();

    const onClick = async (event) => {
      const editButton = event.target.closest(".dac-edit-order-btn");
      const saveButton = event.target.closest(".dac-save-btn");
      if (!editButton && !saveButton) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation?.();

      const root = this.model?.root;
      if (!root) {
        return;
      }
      if (editButton) {
        if (!root.isInEdition && this.canEdit) {
          await root.switchMode("edit");
        }
      } else if (root.isInEdition) {
        // Persist the current changes without closing edit mode.  The custom
        // control panel is meant for consecutive updates to an order; forcing
        // readonly here made users reload the page before they could continue.
        await this.saveButtonClicked();
      }
      this.syncSaleOrderEditControls();
    };

    sourceBar.addEventListener("click", onClick, true);
    this.__dacSaleOrderEditBar = sourceBar;
    this.__dacSaleOrderEditCleanup = () => sourceBar.removeEventListener("click", onClick, true);
  },

  bindTaskStatusSavePrompt() {
    if (this.__dacTaskStatusSavePromptBound || !this.el) {
      return;
    }

    const showSaveButton = () => {
      window.setTimeout(async () => {
        const root = this.model?.root;
        if (!root || root.isInEdition || !this.canEdit) {
          return;
        }
        await root.switchMode("edit");
        this.syncSaleOrderEditControls();
      }, 0);
    };

    const onChange = (event) => {
      if (event.target.matches(".dac-box-document .dac-task-state-select select")) {
        showSaveButton();
      }
    };
    const onClick = (event) => {
      if (event.target.closest(".dac-box-document button[name='action_set_card_state']")) {
        showSaveButton();
      }
    };

    this.el.addEventListener("change", onChange, true);
    this.el.addEventListener("click", onClick, true);
    this.__dacTaskStatusSavePromptBound = true;
    this.__dacTaskStatusSavePromptCleanup = () => {
      this.el?.removeEventListener("change", onChange, true);
      this.el?.removeEventListener("click", onClick, true);
    };
  },

  bindCustomerPhoneAutocomplete(targetInput = null) {
    const input = targetInput || Array.from(
      this.el?.querySelectorAll(SALE_ORDER_PHONE_SELECTOR) || []
    ).find((candidate) => candidate.offsetParent !== null);
    if (!input || this.__dacPhoneAutocompleteInput === input) {
      return;
    }
    this.__dacPhoneAutocompleteCleanup?.();

    const widget = input.closest('.o_field_widget[name="phone"]');
    if (!widget) {
      return;
    }
    widget.classList.add("dac-phone-autocomplete-anchor");
    const menu = document.createElement("div");
    menu.className = "dropdown-menu dac-phone-customer-dropdown";
    widget.appendChild(menu);
    let requestId = 0;
    let timer;

    const close = () => menu.classList.remove("show");
    const appendDropdownActions = () => {
      const searchMore = document.createElement("button");
      searchMore.type = "button";
      searchMore.className = "dropdown-item dac-phone-customer-action dac-phone-search-more";
      searchMore.textContent = "Tìm kiếm thêm...";
      searchMore.addEventListener("mousedown", (event) => event.preventDefault());
      searchMore.addEventListener("click", () => {
        close();
        this.dialog.add(SelectCreateDialog, {
          title: "Chọn khách hàng",
          resModel: "res.partner",
          multiSelect: false,
          onSelected: async ([partnerId]) => {
            const [partner] = await this.orm.read(
              "res.partner",
              [partnerId],
              ["display_name", "phone", "mobile"]
            );
            if (!partner) {
              return;
            }
            await this.model.root.update({
              partner_id: [partner.id, partner.display_name],
            });
            if (!partner.phone && partner.mobile) {
              const mobileDigits = partner.mobile.replace(/[^0-9]/g, "");
              if (mobileDigits.length === 10) {
                await this.model.root.update({ phone: mobileDigits });
              }
            }
          },
        });
      });

      const startTyping = document.createElement("button");
      startTyping.type = "button";
      startTyping.className = "dropdown-item dac-phone-customer-action dac-phone-start-typing";
      startTyping.textContent = "Nhập thông tin...";
      startTyping.addEventListener("mousedown", (event) => event.preventDefault());
      startTyping.addEventListener("click", () => {
        close();
        input.focus();
        input.select();
      });

      menu.append(searchMore, startTyping);
    };
    const load = async () => {
      const currentRequest = ++requestId;
      const query = input.value.trim();
      const domain = query
        ? ["|", ["phone", "ilike", query], ["mobile", "ilike", query]]
        : [];
      menu.innerHTML = '<div class="dropdown-item disabled">Đang tìm khách hàng...</div>';
      menu.classList.add("show");
      let partners;
      try {
        partners = await this.orm.searchRead(
          "res.partner",
          domain,
          ["display_name", "phone", "mobile"],
          { limit: 8 }
        );
      } catch (error) {
        if (currentRequest === requestId) {
          menu.innerHTML = '<div class="dropdown-item disabled">Không thể tải danh sách khách hàng</div>';
        }
        return;
      }
      if (currentRequest !== requestId || document.activeElement !== input) {
        return;
      }
      menu.replaceChildren();
      for (const partner of partners) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "dropdown-item dac-phone-customer-option";
        const phone = partner.phone || partner.mobile || "Chưa có số điện thoại";
        item.textContent = `${partner.display_name}   ☎ ${phone}`;
        item.addEventListener("mousedown", (event) => event.preventDefault());
        item.addEventListener("click", async () => {
          await this.model.root.update({
            partner_id: [partner.id, partner.display_name],
          });
          if (!partner.phone && partner.mobile) {
            const mobileDigits = partner.mobile.replace(/[^0-9]/g, "");
            if (mobileDigits.length === 10) {
              await this.model.root.update({ phone: mobileDigits });
            }
          }
          close();
        });
        menu.appendChild(item);
      }
      if (!partners.length) {
        const empty = document.createElement("div");
        empty.className = "dropdown-item disabled";
        empty.textContent = "Không tìm thấy khách hàng";
        menu.appendChild(empty);
      }
      appendDropdownActions();
    };
    const scheduleLoad = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(load, 120);
    };
    const onMouseDown = () => {
      if (document.activeElement !== input && input.value) {
        window.requestAnimationFrame(() => input.select());
      }
    };
    const onFocus = () => {
      if (input.value) {
        window.requestAnimationFrame(() => input.select());
      }
      load();
    };
    const onInput = () => scheduleLoad();
    const onBlur = () => window.setTimeout(close, 150);
    input.addEventListener("mousedown", onMouseDown);
    input.addEventListener("focus", onFocus);
    input.addEventListener("input", onInput);
    input.addEventListener("blur", onBlur);

    this.__dacPhoneAutocompleteInput = input;
    this.__dacPhoneAutocompleteLoad = load;
    this.__dacPhoneAutocompleteSchedule = scheduleLoad;
    this.__dacPhoneAutocompleteCleanup = () => {
      window.clearTimeout(timer);
      input.removeEventListener("mousedown", onMouseDown);
      input.removeEventListener("focus", onFocus);
      input.removeEventListener("input", onInput);
      input.removeEventListener("blur", onBlur);
      menu.remove();
    };
  },

  syncSaleOrderEditControls() {
    const root = this.model?.root;
    const sourceBar = getSourceBar(this);
    const editButton = sourceBar?.querySelector(".dac-edit-order-btn");
    const saveButton = sourceBar?.querySelector(".dac-save-btn");
    if (!root || !sourceBar) {
      return;
    }
    const editing = root.isInEdition;
    if (editButton) {
      const blocked = ["cancel", "completed"].includes(root.data?.order_state_custom);
      editButton.hidden = editing || !root.resId || !this.canEdit || blocked;
    }
    if (saveButton) {
      const blocked = ["cancel", "completed"].includes(root.data?.order_state_custom);
      saveButton.hidden = !editing || blocked;
    }
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

  syncWorkflowNextStep(sourceBar) {
    const target = sourceBar.querySelector(".dac-v6-control-next-step");
    if (!target) {
      return;
    }
    const selector = [
      ".dac-v6-quotation-next-step",
      ".dac-v6-design-next-step",
      ".dac-v6-production-next-step",
      ".dac-v6-delivery-next-step",
      ".dac-v6-installation-next-step",
    ].join(",");
    const renderedBlocks = Array.from(document.querySelectorAll(selector)).filter(
      (block) => !target.contains(block)
    );
    if (renderedBlocks.length) {
      for (const block of renderedBlocks) {
        const message = block.querySelector(":scope > div");
        if (message) {
          const messageText = message.innerText.replace(/\s+/g, " ").trim();
          message.classList.add("dac-v6-next-step-info");
          message.title = messageText;
          message.setAttribute("aria-label", messageText);
          message.setAttribute("role", "note");
        }
      }
      target.replaceChildren(...renderedBlocks);
    }
  },

  bindNewSaleOrderButton(sourceBar) {
    const eventRoot = this.el || sourceBar;
    if (this.__dacNewOrderBar === eventRoot) {
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
        newTab.location.replace("/odoo/cart-sales/new");
      }
    };
    eventRoot.addEventListener("click", onClick, true);
    this.__dacNewOrderBar = eventRoot;
    this.__dacNewOrderCleanup = () => eventRoot.removeEventListener("click", onClick, true);
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
    this.__dacPhoneAutocompleteCleanup?.();
    this.__dacPhoneAutocompleteCleanup = null;
    this.__dacPhoneAutocompleteInput = null;
    this.__dacPhoneAutocompleteLoad = null;
    this.__dacPhoneAutocompleteSchedule = null;
    this.__dacWorkflowReviewCleanup?.();
    this.__dacWorkflowReviewCleanup = null;
    this.__dacWorkflowReviewBar = null;
    this.__dacSaleOrderEditCleanup?.();
    this.__dacSaleOrderEditCleanup = null;
    this.__dacSaleOrderEditBar = null;
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
