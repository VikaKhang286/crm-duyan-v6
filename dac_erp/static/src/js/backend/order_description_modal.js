/** @odoo-module **/

const MODAL_ID = "dac-v6-order-description-modal";

function fieldInput(block, name) {
    return block.querySelector(`[name="${name}"] input, [name="${name}"] textarea, .o_field_widget[name="${name}"] input, .o_field_widget[name="${name}"] textarea`);
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

function initializePreviews(root = document) {
    root.querySelectorAll?.(".dac-v6-order-description").forEach((block) => {
        if (block.querySelector(".dac-v6-description-preview")) return;
        const title = fieldInput(block, "order_title")?.value || "";
        const summary = fieldInput(block, "order_summary")?.value || "";
        renderDescriptionPreview(block, title, summary, Number(block.dataset.descriptionImages || 0));
    });
}

function addImage(files, gallery, editor) {
    [...files].filter((file) => file.type.startsWith("image/")).forEach((file) => {
        const reader = new FileReader();
        reader.onload = () => {
            const card = document.createElement("button");
            card.type = "button";
            card.className = "dac-v6-description-image";
            card.title = "Chèn ảnh tại vị trí con trỏ";
            card.innerHTML = `<img alt="${file.name}"/><b></b><span>Tải ảnh</span>`;
            card.querySelector("img").src = reader.result;
            card.querySelector("b").textContent = file.name;
            card.addEventListener("click", () => {
                editor.focus();
                document.execCommand("insertImage", false, reader.result);
            });
            gallery.appendChild(card);
        };
        reader.readAsDataURL(file);
    });
}

function openDescriptionModal(block) {
    document.getElementById(MODAL_ID)?.remove();
    const titleInput = fieldInput(block, "order_title");
    const summaryInput = fieldInput(block, "order_summary");
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
            <label class="dac-v6-upload">Tải ảnh lên<input type="file" accept="image/*" multiple hidden></label>
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
    title.value = titleInput?.value || "";
    editor.textContent = summaryInput?.value || "";

    const close = () => modal.remove();
    modal.querySelector(".dac-v6-modal-close").addEventListener("click", close);
    modal.querySelector(".dac-v6-modal-cancel").addEventListener("click", close);
    modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
    modal.querySelector(".dac-v6-bold").addEventListener("click", () => { editor.focus(); document.execCommand("bold"); });
    modal.querySelector('input[type="file"]').addEventListener("change", (event) => addImage(event.target.files, gallery, editor));
    editor.addEventListener("paste", (event) => {
        const images = [...event.clipboardData.files].filter((file) => file.type.startsWith("image/"));
        if (images.length) addImage(images, gallery, editor);
    });
    modal.querySelector(".dac-v6-modal-save").addEventListener("click", () => {
        const nextTitle = title.value.trim();
        const nextSummary = editor.innerText.trim();
        const imageCount = gallery.querySelectorAll(".dac-v6-description-image").length;
        setOdooValue(titleInput, nextTitle);
        setOdooValue(summaryInput, nextSummary);
        renderDescriptionPreview(block, nextTitle, nextSummary, imageCount);
        close();
        showSavedToast();
    });
    document.body.appendChild(modal);
    requestAnimationFrame(() => editor.focus());
}

document.addEventListener("click", (event) => {
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
