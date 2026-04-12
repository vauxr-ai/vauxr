FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

FROM node:22-alpine AS web-build
WORKDIR /app/web-client
COPY web-client/package.json web-client/package-lock.json* ./
RUN npm install
COPY web-client/ ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN addgroup -S vauxr && adduser -S vauxr -G vauxr
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist
COPY --from=web-build /app/web-client/dist ./web-client/dist
RUN mkdir -p /data && chown vauxr:vauxr /data
USER vauxr
EXPOSE 8765
CMD ["node", "dist/server.js"]
