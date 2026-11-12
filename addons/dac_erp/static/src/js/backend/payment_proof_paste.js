/** @odoo-module **/

function activeProofSection() {
    return [...document.querySelectorAll('.dac-payment-proof-section')].find(
        (section) => section.offsetParent !== null
    ) || null;
}

function imageInput(section) {
    return section.querySelector(
        '.dac-payment-proof-image input[type="file"], input.o_input_file[type="file"]'
    );
}

function setStatus(section, message, type = 'muted') {
    const status = section.querySelector('.dac-payment-proof-status');
    if (!status) {
        return;
    }
    status.textContent = message;
    status.classList.remove('text-muted', 'text-success', 'text-danger');
    status.classList.add(`text-${type}`);
}

function showImageActions(section, visible) {
    section.querySelector('.dac-payment-proof-card')?.classList.toggle('d-none', !visible);
}

function updateImageName(section, name) {
    const imageName = section.querySelector('.dac-payment-proof-name');
    if (imageName) {
        imageName.textContent = name || 'Hình ảnh chuyển khoản';
    }
}

function downloadImage(section) {
    const image = section.querySelector('.dac-payment-proof-image img');
    if (!image?.src || image.src.includes('/web/static/img/placeholder.png')) {
        return;
    }
    const link = document.createElement('a');
    link.href = image.src;
    link.download = section.querySelector('.dac-payment-proof-name')?.textContent || 'chung-tu-chuyen-khoan.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function putFileInImageField(section, file) {
    if (!file?.type?.startsWith('image/')) {
        return false;
    }
    const input = imageInput(section);
    if (!input) {
        return false;
    }
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    updateImageName(section, file.name);
    return true;
}

function imageFromPasteEvent(event) {
    const file = [...(event.clipboardData?.files || [])].find((candidate) =>
        candidate.type.startsWith('image/')
    );
    if (file) {
        return file;
    }
    const items = [...(event.clipboardData?.items || [])];
    return items.find((item) => item.kind === 'file' && item.type.startsWith('image/'))?.getAsFile();
}

async function imageFromClipboard() {
    if (!navigator.clipboard?.read) {
        return null;
    }
    const clipboardItems = await navigator.clipboard.read();
    for (const item of clipboardItems) {
        const imageType = item.types.find((type) => type.startsWith('image/'));
        if (imageType) {
            const blob = await item.getType(imageType);
            const extension = imageType.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
            return new File([blob], `chung-tu-chuyen-khoan.${extension}`, { type: imageType });
        }
    }
    return null;
}

document.addEventListener('paste', (event) => {
    const section = activeProofSection();
    const image = section && imageFromPasteEvent(event);
    if (image && putFileInImageField(section, image)) {
        event.preventDefault();
        setStatus(section, 'Đã dán ảnh từ clipboard.', 'success');
        showImageActions(section, true);
    }
});

document.addEventListener('change', (event) => {
    if (event.target.matches('.dac-payment-proof-image input[type="file"], input.o_input_file[type="file"]')) {
        const section = event.target.closest('.dac-payment-proof-section');
        if (section && event.target.files?.length) {
            setStatus(section, 'Đã chọn ảnh tải lên.', 'success');
            updateImageName(section, event.target.files[0].name);
            showImageActions(section, true);
        }
    }
}, true);

document.addEventListener('click', async (event) => {
    const button = event.target.closest('.dac-payment-proof-upload, .dac-payment-proof-paste, .dac-payment-proof-download, .dac-payment-proof-remove');
    if (!button) {
        return;
    }
    // Odoo's form button component handles clicks before bubble listeners.
    // Capture this UI-only button first so it is never treated as a view action.
    event.preventDefault();
    event.stopImmediatePropagation();
    const section = activeProofSection();
    if (!section) {
        return;
    }
    if (button.classList.contains('dac-payment-proof-remove')) {
        section.querySelector('.o_clear_file_button')?.click();
        showImageActions(section, false);
        updateImageName(section, '');
        setStatus(section, 'Đã xoá hình.', 'muted');
        return;
    }
    if (button.classList.contains('dac-payment-proof-upload')) {
        imageInput(section)?.click();
        return;
    }
    if (button.classList.contains('dac-payment-proof-paste')) {
        setStatus(section, 'Đang đọc ảnh từ clipboard...');
        try {
            const image = await imageFromClipboard();
            if (putFileInImageField(section, image)) {
                showImageActions(section, true);
                setStatus(section, 'Đã dán ảnh từ clipboard.', 'success');
            } else {
                setStatus(section, 'Clipboard không có ảnh. Bạn có thể dùng Cmd+V để dán.', 'danger');
            }
        } catch {
            setStatus(section, 'Chrome không cho phép đọc ảnh. Bạn có thể dùng Cmd+V để dán.', 'danger');
        }
        return;
    }
    downloadImage(section);
}, true);
