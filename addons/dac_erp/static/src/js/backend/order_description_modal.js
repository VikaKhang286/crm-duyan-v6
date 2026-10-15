/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const MODAL_ID = "dac-v6-order-description-modal";

function fieldInput(block, name) {
    const selector = `[name="${name}"] input, [name="${name}"] textarea, .o_field_widget[name="${name}"] input, .o_field_widget[name="${name}"] textarea`;
    return block.querySelector(selector)
        || block.closest(".o_form_view")?.querySelector(selector)
        || document.querySelector(selector);
}

function isDescriptionReadOnly(block, titleInput, summaryInput) {
    const roleMarker = block.querySelector(".dac-v6-description-readonly-role");
    const roleIsReadOnly = roleMarker && !roleMarker.classList.contains("d-none") && !roleMarker.classList.contains("o_invisible_modifier");
    const fieldIsReadOnly = [titleInput, summaryInput].some((input) =>
        input?.readOnly || input?.disabled || input?.closest(".o_readonly_modifier")
    );
    return Boolean(roleIsReadOnly || fieldIsReadOnly);
}

function setOdooValue(input, value) {
    if (!input) return;
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value");
    if (descriptor?.set) descriptor.set.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
}

function renderDescriptionPreview(block, title, summary, imageCount = 0) {
    let preview = block.querySelector(".dac-v6-description-preview");
    if (!preview) {
        preview = document.createElement("div");
        preview.className = "dac-v6-description-preview";
        block.appendChild(preview);
    }
    preview.innerHTML = `
      <div class="dac-v6-description-preview-head"><strong></strong><span>Soạn thảo</span></div>
      <p></p>
      <small></small>`;
    preview.querySelector("strong").textContent = title || "Mô tả đơn hàng";
    preview.querySelector("p").textContent = summary || "Chưa có mô tả. Bấm để bổ sung yêu cầu thiết kế.";
    preview.querySelector("small").textContent = `${imageCount} ảnh đính kèm`;
    block.dataset.descriptionImages = String(imageCount);
}

function showSavedToast() {
    document.querySelector(".dac-v6-description-toast")?.remove();
    const toast = document.createElement("div");
    toast.className = "dac-v6-description-toast";
    toast.innerHTML = '<i class="fa fa-check-circle" aria-hidden="true"></i><span>Đã lưu</span>';
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("show"));
    window.setTimeout(() => {
        toast.classList.remove("show");
        window.setTimeout(() => toast.remove(), 220);
    }, 2400);
}

function showImageLimitError() {
    document.querySelector(".dac-v6-description-toast")?.remove();
    const toast = document.createElement("div");
    toast.className = "dac-v6-description-toast dac-v6-description-toast--error";
    toast.innerHTML = '<i class="fa fa-exclamation-circle" aria-hidden="true"></i><span>Chỉ nhận 1 ảnh</span>';
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("show"));
    window.setTimeout(() => {
        toast.classList.remove("show");
        window.setTimeout(() => toast.remove(), 220);
    }, 2400);
}

function initializePreviews(root = document) {
    root.querySelectorAll?.(".dac-v6-order-description").forEach((block) => {
        if (block.querySelector(".dac-v6-description-preview")) return;
        const title = fieldInput(block, "order_title")?.value || "";
        const summary = fieldInput(block, "order_summary")?.value || "";
        const sampleImage = block.closest(".o_form_view")?.querySelector('.o_field_widget[name="cart_sample_image"] img');
        const hasImage = sampleImage?.src && !sampleImage.src.includes("placeholder");
        renderDescriptionPreview(block, title, summary, hasImage ? 1 : 0);
        const orderId = currentOrderId();
        if (orderId) {
            readStoredDescription(orderId).then((stored) => {
                if (!stored || !block.isConnected) return;
                renderDescriptionPreview(
                    block,
                    stored.order_title || "",
                    stored.order_summary || stored.cart_design_description || "",
                    stored.cart_sample_image ? 1 : 0,
                );
            });
        }
    });
}

function appendImageCard(image, gallery, editor, interactive = true) {
    const card = document.createElement(interactive ? "button" : "div");
    if (interactive) card.type = "button";
    card.className = "dac-v6-description-image";
    card.dataset.name = image.name || "Ảnh đính kèm";
    card.dataset.src = image.src;
    card.title = interactive ? "Chèn ảnh tại vị trí con trỏ" : card.dataset.name;
    card.innerHTML = '<img alt=""/><b></b><span>Ảnh đính kèm</span>';
    card.querySelector("img").src = image.src;
    card.querySelector("img").alt = card.dataset.name;
    card.querySelector("b").textContent = card.dataset.name;
    if (interactive) {
        card.addEventListener("click", () => {
            editor.focus();
            document.execCommand("insertImage", false, image.src);
        });
    }
    gallery.appendChild(card);
}

async function imagesAsComposite(cards) {
    const sources = cards.map((card) => card.dataset.src).filter((src) => src?.startsWith("data:image/"));
    if (!sources.length) return null;
    const images = await Promise.all(sources.map((src) => new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = src;
    })));
    const columns = images.length > 1 ? 2 : 1;
    const cellWidth = 800;
    const cellHeight = 600;
    const canvas = document.createElement("canvas");
    canvas.width = cellWidth * columns;
    canvas.height = cellHeight * Math.ceil(images.length / columns);
    const context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    images.forEach((image, index) => {
        const scale = Math.min(cellWidth / image.naturalWidth, cellHeight / image.naturalHeight);
        const width = image.naturalWidth * scale;
        const height = image.naturalHeight * scale;
        const x = (index % columns) * cellWidth + (cellWidth - width) / 2;
        const y = Math.floor(index / columns) * cellHeight + (cellHeight - height) / 2;
        context.drawImage(image, x, y, width, height);
    });
    return canvas.toDataURL("image/jpeg", 0.9).split(",")[1];
}

function currentOrderId() {
    const match = window.location.pathname.match(/sale\.order\/(\d+)(?:\/|$)/)
        || window.location.pathname.match(/action-\d+\/(\d+)(?:\/|$)/);
    return match ? Number(match[1]) : null;
}

function addImage(files, gallery, editor) {
    [...files].filter((file) => file.type.startsWith("image/")).forEach((file) => {
        const reader = new FileReader();
        reader.onload = () => {
            appendImageCard({ name: file.name, src: reader.result }, gallery, editor);
        };
        reader.readAsDataURL(file);
    });
}

async function readStoredDescription(orderId) {
    if (!orderId) return null;
    try {
        const rows = await rpc("/web/dataset/call_kw/sale.order/read", {
            model: "sale.order",
            method: "read",
            args: [[orderId], ["order_title", "order_summary", "cart_design_description", "cart_sample_image"]],
            kwargs: { context: { bin_size: false } },
        });
        return rows[0] || null;
    } catch (error) {
        console.error("Cannot read order description", error);
        return null;
    }
}

async function openDescriptionModal(block, forceReadOnly = false) {
    document.getElementById(MODAL_ID)?.remove();
    const orderId = currentOrderId();
    const stored = await readStoredDescription(orderId);
    const titleInput = fieldInput(block, "order_title");
    const summaryInput = fieldInput(block, "order_summary");
    const designDescriptionInput = fieldInput(block, "cart_design_description");
    const readOnly = forceReadOnly || isDescriptionReadOnly(block, titleInput, summaryInput);
    const orderName = document.querySelector(".dac-control-panel-title h1, .dac-control-panel-title")?.textContent?.trim() || "Đơn mới";
    const customer = document.querySelector('.o_field_widget[name="partner_id"] input')?.value || "Chưa chọn khách hàng";

    const modal = document.createElement("div");
    modal.id = MODAL_ID;
    modal.className = "dac-v6-description-modal-backdrop";
    modal.innerHTML = `
      <section class="dac-v6-description-modal" role="dialog" aria-modal="true" aria-label="Mô tả đơn hàng">
        <header><div><h2>Mô tả đơn hàng</h2><p></p></div><button type="button" class="dac-v6-modal-close" aria-label="Đóng">×</button></header>
        <main>
          <div class="dac-v6-description-tools">
            <button type="button" class="dac-v6-bold" title="In đậm"><b>B</b></button>
            <label class="dac-v6-upload">Tải ảnh lên<input type="file" accept="image/*" hidden></label>
            <span>Ctrl+V để dán ảnh tại con trỏ</span>
          </div>
          <input class="dac-v6-description-title" placeholder="Tiêu đề đơn hàng"/>
          <div class="dac-v6-description-editor" contenteditable="true" role="textbox" aria-multiline="true"></div>
          <div class="dac-v6-attachments-head"><h3>Ảnh đính kèm</h3><span>Đặt con trỏ trong mô tả rồi chọn ảnh để chèn nhãn</span></div>
          <div class="dac-v6-description-gallery"></div>
        </main>
        <footer><span class="dac-v6-save-state">Đã lưu</span><div><button type="button" class="dac-v6-modal-cancel">Đóng</button><button type="button" class="dac-v6-modal-save">Lưu mô tả</button></div></footer>
      </section>`;

    modal.querySelector("header p").textContent = `${orderName} · ${customer}`;
    const title = modal.querySelector(".dac-v6-description-title");
    const editor = modal.querySelector(".dac-v6-description-editor");
    const gallery = modal.querySelector(".dac-v6-description-gallery");
    title.value = stored?.order_title || titleInput?.value || "";
    editor.textContent = stored?.order_summary || stored?.cart_design_description || summaryInput?.value || "";
    const sampleImage = block.closest(".o_form_view")?.querySelector('.o_field_widget[name="cart_sample_image"] img');
    const storedImageUrl = stored?.cart_sample_image && orderId
        ? `/web/image/sale.order/${orderId}/cart_sample_image?unique=${Date.now()}`
        : null;
    const sampleImageUrl = storedImageUrl || (sampleImage?.src && !sampleImage.src.includes("placeholder") ? sampleImage.src : null);
    if (sampleImageUrl) {
        appendImageCard({ name: "Hình ảnh mẫu xe", src: sampleImageUrl }, gallery, editor, !readOnly);
    }

    if (readOnly) {
        title.readOnly = true;
        editor.contentEditable = "false";
        modal.querySelector(".dac-v6-description-tools").remove();
        modal.querySelector(".dac-v6-modal-save").remove();
        modal.querySelector(".dac-v6-save-state").textContent = "Chỉ xem";
    }

    const close = () => modal.remove();
    modal.querySelector(".dac-v6-modal-close").addEventListener("click", close);
    modal.querySelector(".dac-v6-modal-cancel").addEventListener("click", close);
    modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
    if (!readOnly) {
        modal.querySelector(".dac-v6-bold").addEventListener("click", () => { editor.focus(); document.execCommand("bold"); });
        modal.querySelector('input[type="file"]').addEventListener("change", (event) => {
            if (!event.target.files.length) return;
            gallery.replaceChildren();
            addImage([event.target.files[0]], gallery, editor);
        });
        editor.addEventListener("paste", (event) => {
            const images = [...event.clipboardData.files].filter((file) => file.type.startsWith("image/"));
            if (images.length >= 2) {
                event.preventDefault();
                showImageLimitError();
                return;
            }
            if (images.length === 1) {
                event.preventDefault();
                gallery.replaceChildren();
                addImage(images, gallery, editor);
            }
        });
        modal.querySelector(".dac-v6-modal-save").addEventListener("click", async () => {
            const nextTitle = title.value.trim();
            const nextSummary = editor.innerText.trim();
            const cards = [...gallery.querySelectorAll(".dac-v6-description-image")];
            const saveButton = modal.querySelector(".dac-v6-modal-save");
            saveButton.disabled = true;
            saveButton.textContent = "Đang lưu...";
            setOdooValue(titleInput, nextTitle);
            setOdooValue(summaryInput, nextSummary);
            setOdooValue(designDescriptionInput, nextSummary);
            try {
                if (orderId) {
                    const values = {
                        order_title: nextTitle,
                        order_summary: nextSummary,
                        cart_design_description: nextSummary,
                    };
                    const composite = await imagesAsComposite(cards);
                    if (composite) values.cart_sample_image = composite;
                    await rpc("/web/dataset/call_kw/sale.order/write", {
                        model: "sale.order",
                        method: "write",
                        args: [[orderId], values],
                        kwargs: {},
                    });
                }
                renderDescriptionPreview(block, nextTitle, nextSummary, cards.length);
                close();
                showSavedToast();
            } catch (error) {
                saveButton.disabled = false;
                saveButton.textContent = "Lưu mô tả";
                modal.querySelector(".dac-v6-save-state").textContent = "Lưu thất bại";
                console.error("Cannot save order description", error);
            }
        });
    }
    document.body.appendChild(modal);
    requestAnimationFrame(() => editor.focus());
}

document.addEventListener("click", (event) => {
    if (event.target.closest(".dac-v6-task-description-btn")) {
        event.preventDefault();
        event.stopPropagation();
        const descriptionBlock = document.querySelector(".dac-v6-order-description");
        if (descriptionBlock) openDescriptionModal(descriptionBlock, true);
        return;
    }
    const block = event.target.closest(".dac-v6-order-description");
    if (!block || event.target.closest("button, input, textarea, a, .o_field_widget")) return;
    openDescriptionModal(block);
});

function startPreviewObserver() {
    const previewObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE) initializePreviews(node.matches?.(".dac-v6-order-description") ? node.parentElement : node);
            }
        }
    });
    previewObserver.observe(document.body, { childList: true, subtree: true });
    initializePreviews();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startPreviewObserver, { once: true });
else startPreviewObserver();
