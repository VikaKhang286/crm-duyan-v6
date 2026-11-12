# Ghim đúng bản Node đã chạy ổn trên production (tránh tag trôi nổi tự kéo bản mới). Ngày 2026-11-12 image build với Node 24.22.3
# bị Zalo đóng kết nối ngay sau khi khởi động, còn 24.22.2 thì ổn; chưa xác minh được nguyên nhân (có thể do Node, có thể do
# một kết nối khác đang chiếm phiên lúc đó) nên chỉ nâng Node khi đã thử trên bridge thật.
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
