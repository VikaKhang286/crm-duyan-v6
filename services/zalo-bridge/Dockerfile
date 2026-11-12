# Ghim đúng bản Node đã chạy ổn trên production. Bản 24.22.3 (kéo tự động khi dùng tag trôi nổi node:24-bookworm-slim)
# làm kết nối Zalo bị đóng ngay sau khi khởi động (2026-11-12); chỉ nâng khi đã thử trên bridge thật.
FROM node:24.22.2-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY src ./src
RUN mkdir /data && chown node:node /data
USER node
ENV DATA_DIR=/data HOST=0.0.0.0 PORT=8095
EXPOSE 8095
CMD ["node", "src/server.js"]
