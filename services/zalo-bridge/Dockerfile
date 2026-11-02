FROM node:24-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY src ./src
RUN mkdir /data && chown node:node /data
USER node
ENV DATA_DIR=/data HOST=0.0.0.0 PORT=8095
EXPOSE 8095
CMD ["node", "src/server.js"]
