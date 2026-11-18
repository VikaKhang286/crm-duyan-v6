/** @odoo-module **/

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

export class DacAttendanceCameraDialog extends Component {
    static template = "dac_attendance.CameraDialog";
    static components = { Dialog };
    static props = {
        close: { type: Function },
        isCheckOut: { type: Boolean, optional: true },
        onPhotoTaken: { type: Function },
    };

    setup() {
        this.orm = useService("orm");
        this.videoRef = useRef("cameraVideo");
        this.canvasRef = useRef("cameraCanvas");
        this.state = useState({
            cameraReady: false,
            photoTaken: false,
            photoDataUrl: null,
            gpsStatus: "loading",
            gpsLat: null,
            gpsLng: null,
            addressStatus: "idle",
            locationAddress: null,
            capturedAt: null,
            capturing: false,
            errorMessage: null,
            submitting: false,
        });
        this.stream = null;
        this.photoBase64 = null;

        onMounted(() => this._initCamera());
        onWillUnmount(() => this._stopCamera());
    }

    async _initCamera() {
        try {
            // Request camera - facingMode 'user' for selfie, no gallery allowed
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: "user",
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                },
                audio: false,
            });
            const video = this.videoRef.el;
            if (video) {
                video.srcObject = this.stream;
                video.play();
                this.state.cameraReady = true;
            }
        } catch (e) {
            console.error("Camera error:", e);
            this.state.errorMessage = "Không thể mở camera. Vui lòng cho phép truy cập camera trong trình duyệt.";
        }

        // Get GPS position
        this._getGPS();
    }

    _getGPS() {
        if (!navigator.geolocation) {
            this.state.gpsStatus = "unavailable";
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                this.state.gpsLat = pos.coords.latitude;
                this.state.gpsLng = pos.coords.longitude;
                this.state.gpsStatus = "ready";
                this._reverseGeocode(pos.coords.latitude, pos.coords.longitude);
            },
            (err) => {
                console.warn("GPS error:", err.message);
                this.state.gpsStatus = "error";
            },
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
        );
    }

    async _reverseGeocode(latitude, longitude) {
        this.state.addressStatus = "loading";
        this.state.locationAddress = null;
        try {
            const result = await this.orm.call(
                "hr.attendance",
                "dac_reverse_geocode",
                [latitude, longitude]
            );
            if (result?.success && result.address) {
                this.state.locationAddress = result.address;
                this.state.addressStatus = "ready";
                return;
            }
            console.warn("Backend reverse geocoding failed:", result?.error);
        } catch (error) {
            console.warn("Backend reverse geocoding unavailable:", error);
        }

        await this._reverseGeocodeDirect(latitude, longitude);
    }

    async _reverseGeocodeDirect(latitude, longitude) {
        const params = new URLSearchParams({
            lat: String(latitude),
            lon: String(longitude),
            format: "jsonv2",
            addressdetails: "1",
            zoom: "18",
            "accept-language": "vi",
        });
        try {
            const response = await fetch(
                `https://nominatim.openstreetmap.org/reverse?${params.toString()}`,
                { headers: { Accept: "application/json" } }
            );
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const result = await response.json();
            this.state.locationAddress = result.display_name || null;
            this.state.addressStatus = this.state.locationAddress ? "ready" : "error";
        } catch (error) {
            console.warn("Direct reverse geocoding fallback failed:", error);
            this.state.addressStatus = "error";
        }
    }

    _stopCamera() {
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
    }

    async capturePhoto() {
        if (this.state.capturing) return;
        const video = this.videoRef.el;
        const canvas = this.canvasRef.el;
        if (!video || !canvas) return;
        this.state.capturing = true;

        const ctx = canvas.getContext("2d");
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 480;

        // Draw video frame
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        // Bake the current location map into the JPEG itself. Failure to load
        // a map tile must not prevent an employee from taking attendance.
        if (this.hasGps) {
            try {
                await this._drawMapInset(ctx, canvas);
            } catch (error) {
                console.warn("Unable to draw attendance map inset:", error);
            }
        }

        // Draw a Timemark-style attendance stamp into the stored photo.
        const now = new Date();
        this.state.capturedAt = now;
        const timeText = now.toLocaleTimeString("vi-VN", {
            hour: "2-digit", minute: "2-digit", hour12: false,
        });
        const dateText = now.toLocaleDateString("vi-VN", {
            weekday: "long", day: "2-digit", month: "2-digit", year: "numeric",
        });
        const actionText = this.props.isCheckOut ? "RA CA" : "VÀO CA";
        const locationText = this.capturedLocationText;
        const scale = Math.max(0.75, Math.min(canvas.width / 640, canvas.height / 480));
        const panelHeight = Math.max(
            Math.round(165 * scale),
            Math.round(canvas.height * 0.36),
        );
        const panelTop = canvas.height - panelHeight;
        const gradient = ctx.createLinearGradient(0, panelTop, 0, canvas.height);
        gradient.addColorStop(0, "rgba(0, 0, 0, 0)");
        gradient.addColorStop(0.28, "rgba(0, 0, 0, 0.66)");
        gradient.addColorStop(1, "rgba(0, 0, 0, 0.9)");
        ctx.fillStyle = gradient;
        ctx.fillRect(0, panelTop, canvas.width, panelHeight);

        const left = Math.round(18 * scale);
        // Keep the last text baseline inside the JPEG instead of placing it
        // exactly on canvas.height, where browsers clip the glyph descenders.
        const badgeY = Math.min(
            panelTop + Math.round(18 * scale),
            canvas.height - Math.round(148 * scale),
        );
        ctx.fillStyle = this.props.isCheckOut ? "#dc2626" : "#16a34a";
        ctx.fillRect(left, badgeY, Math.round(100 * scale), Math.round(38 * scale));
        ctx.fillStyle = "#fff";
        ctx.font = `bold ${Math.round(18 * scale)}px Arial, sans-serif`;
        ctx.fillText(actionText, left + Math.round(10 * scale), badgeY + Math.round(26 * scale));
        ctx.font = `bold ${Math.round(30 * scale)}px Arial, sans-serif`;
        ctx.fillText(timeText, left + Math.round(112 * scale), badgeY + Math.round(29 * scale));

        ctx.fillStyle = this.props.isCheckOut ? "#ef4444" : "#22c55e";
        ctx.fillRect(left, badgeY + Math.round(52 * scale), Math.round(4 * scale), Math.round(88 * scale));
        ctx.fillStyle = "#fff";
        ctx.font = `bold ${Math.round(17 * scale)}px Arial, sans-serif`;
        ctx.fillText(dateText, left + Math.round(14 * scale), badgeY + Math.round(70 * scale));
        ctx.font = `${Math.round(13 * scale)}px Arial, sans-serif`;
        // Match `.o_dac_captured_details { width: 75% }` in the HTML preview
        // so the address wraps at the same visual point in the stored JPEG.
        const maxDetailWidth = Math.max(
            Math.round(160 * scale),
            Math.round(canvas.width * 0.75) - left - Math.round(14 * scale),
        );
        const addressLines = this._wrapCanvasText(ctx, locationText, maxDetailWidth, 2);
        const detailX = left + Math.round(14 * scale);
        const firstAddressY = badgeY + Math.round(94 * scale);
        const lineHeight = Math.round(19 * scale);
        addressLines.forEach((line, index) => {
            ctx.fillText(line, detailX, firstAddressY + index * lineHeight);
        });
        ctx.fillStyle = "rgba(255, 255, 255, 0.72)";
        ctx.font = `${Math.round(11 * scale)}px Arial, sans-serif`;
        ctx.fillText("@OpenStreetMap contributors", detailX, badgeY + Math.round(139 * scale));

        // Get base64 after all persistent overlays have been drawn.
        const dataUrl = canvas.toDataURL("image/jpeg", 0.88);
        this.photoBase64 = dataUrl.split(",")[1];
        this.state.photoDataUrl = dataUrl;
        this.state.photoTaken = true;
        this.state.capturing = false;

        // Stop camera preview
        this._stopCamera();
    }

    _wrapCanvasText(ctx, text, maxWidth, maxLines) {
        const words = String(text || "").trim().split(/\s+/).filter(Boolean);
        if (!words.length) return [""];
        const lines = [];
        let currentLine = "";
        for (let index = 0; index < words.length; index++) {
            const word = words[index];
            const candidate = currentLine ? `${currentLine} ${word}` : word;
            if (!currentLine || ctx.measureText(candidate).width <= maxWidth) {
                currentLine = candidate;
                continue;
            }
            lines.push(currentLine);
            currentLine = word;
            if (lines.length === maxLines - 1) {
                currentLine = [currentLine, ...words.slice(index + 1)].join(" ");
                break;
            }
        }
        if (currentLine) lines.push(currentLine);
        const lastIndex = Math.min(lines.length, maxLines) - 1;
        if (lastIndex >= 0 && ctx.measureText(lines[lastIndex]).width > maxWidth) {
            let truncated = lines[lastIndex];
            while (truncated.length > 1 && ctx.measureText(`${truncated}…`).width > maxWidth) {
                truncated = truncated.slice(0, -1);
            }
            lines[lastIndex] = `${truncated.trim()}…`;
        }
        return lines.slice(0, maxLines);
    }

    _loadMapTile(url) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            const timeoutId = window.setTimeout(
                () => reject(new Error(`Hết thời gian tải tile bản đồ: ${url}`)),
                8000,
            );
            image.crossOrigin = "anonymous";
            image.onload = () => {
                window.clearTimeout(timeoutId);
                resolve(image);
            };
            image.onerror = () => {
                window.clearTimeout(timeoutId);
                reject(new Error(`Không tải được tile bản đồ: ${url}`));
            };
            image.src = url;
        });
    }

    async _drawMapInset(ctx, canvas) {
        const zoom = 16;
        const tileSize = 256;
        const scale = Math.max(0.75, Math.min(canvas.width / 640, canvas.height / 480));
        const insetWidth = Math.min(Math.round(190 * scale), Math.round(canvas.width * 0.38));
        const insetHeight = Math.round(insetWidth * 0.62);
        const insetX = Math.round(12 * scale);
        const insetY = Math.round(12 * scale);
        const worldSize = tileSize * (2 ** zoom);
        const longitude = this.state.gpsLng;
        const latitudeRad = this.state.gpsLat * Math.PI / 180;
        const centerX = ((longitude + 180) / 360) * worldSize;
        const centerY = (1 - Math.asinh(Math.tan(latitudeRad)) / Math.PI) / 2 * worldSize;
        const sourceWidth = insetWidth / scale;
        const sourceHeight = insetHeight / scale;
        const worldLeft = centerX - sourceWidth / 2;
        const worldTop = centerY - sourceHeight / 2;
        const firstTileX = Math.floor(worldLeft / tileSize);
        const lastTileX = Math.floor((worldLeft + sourceWidth) / tileSize);
        const firstTileY = Math.floor(worldTop / tileSize);
        const lastTileY = Math.floor((worldTop + sourceHeight) / tileSize);
        const tiles = [];

        for (let tileY = firstTileY; tileY <= lastTileY; tileY++) {
            for (let tileX = firstTileX; tileX <= lastTileX; tileX++) {
                const wrappedX = ((tileX % (2 ** zoom)) + (2 ** zoom)) % (2 ** zoom);
                tiles.push({
                    tileX,
                    tileY,
                    image: await this._loadMapTile(
                        `https://tile.openstreetmap.org/${zoom}/${wrappedX}/${tileY}.png`
                    ),
                });
            }
        }

        ctx.save();
        ctx.beginPath();
        ctx.roundRect(insetX, insetY, insetWidth, insetHeight, Math.round(9 * scale));
        ctx.clip();
        for (const tile of tiles) {
            ctx.drawImage(
                tile.image,
                insetX + (tile.tileX * tileSize - worldLeft) * scale,
                insetY + (tile.tileY * tileSize - worldTop) * scale,
                tileSize * scale,
                tileSize * scale,
            );
        }

        const markerX = insetX + insetWidth / 2;
        const markerY = insetY + insetHeight / 2;
        ctx.fillStyle = "#0ea5e9";
        ctx.beginPath();
        ctx.arc(markerX, markerY - Math.round(7 * scale), Math.round(8 * scale), 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(markerX - Math.round(5 * scale), markerY - Math.round(2 * scale));
        ctx.lineTo(markerX, markerY + Math.round(9 * scale));
        ctx.lineTo(markerX + Math.round(5 * scale), markerY - Math.round(2 * scale));
        ctx.fill();
        ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
        ctx.font = `${Math.max(8, Math.round(9 * scale))}px Arial, sans-serif`;
        ctx.fillText("© OpenStreetMap", insetX + Math.round(4 * scale),
            insetY + insetHeight - Math.round(5 * scale));
        ctx.restore();

        ctx.save();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = Math.max(2, Math.round(2 * scale));
        ctx.beginPath();
        ctx.roundRect(insetX, insetY, insetWidth, insetHeight, Math.round(9 * scale));
        ctx.stroke();
        ctx.restore();
    }

    get hasGps() {
        return Number.isFinite(this.state.gpsLat) && Number.isFinite(this.state.gpsLng);
    }

    get capturedTimeText() {
        if (!this.state.capturedAt) return "";
        return this.state.capturedAt.toLocaleTimeString("vi-VN", {
            hour: "2-digit", minute: "2-digit", hour12: false,
        });
    }

    get capturedDateText() {
        if (!this.state.capturedAt) return "";
        return this.state.capturedAt.toLocaleDateString("vi-VN", {
            weekday: "long", day: "2-digit", month: "2-digit", year: "numeric",
        });
    }

    get capturedLocationText() {
        if (this.state.locationAddress) return this.state.locationAddress;
        if (this.state.addressStatus === "loading") return "Đang xác định địa chỉ…";
        return "Không xác định được địa chỉ";
    }

    retakePhoto() {
        this.state.photoTaken = false;
        this.state.photoDataUrl = null;
        this.state.capturedAt = null;
        this.state.capturing = false;
        this.photoBase64 = null;
        this._initCamera();
    }

    async confirmPhoto() {
        if (!this.photoBase64 || this.state.submitting) return;
        this.state.submitting = true;

        const metadata = {
            timestamp: new Date().toISOString(),
            latitude: this.state.gpsLat,
            longitude: this.state.gpsLng,
            address: this.state.locationAddress,
        };

        try {
            await this.props.onPhotoTaken(this.photoBase64, metadata);
            this.props.close();
        } catch (e) {
            this.state.errorMessage = `Lỗi: ${e.message}`;
            this.state.submitting = false;
        }
    }

    get dialogTitle() {
        return this.props.isCheckOut ? "Chụp ảnh Check-out" : "Chụp ảnh Check-in";
    }

    get googleMapsEmbedUrl() {
        if (this.state.gpsStatus !== "ready"
            || !Number.isFinite(this.state.gpsLat)
            || !Number.isFinite(this.state.gpsLng)) {
            return false;
        }
        const coordinates = `${this.state.gpsLat},${this.state.gpsLng}`;
        return `https://maps.google.com/maps?q=${encodeURIComponent(coordinates)}&z=16&output=embed`;
    }
}
