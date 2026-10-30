# syntax=docker/dockerfile:1
# ---- Сборка клиента ----
FROM node:22-bookworm-slim AS builder
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---- nginx: статика клиента и проксирование /api на backend ----
FROM nginx:1.27-alpine AS runtime
COPY --from=builder /app/dist /usr/share/nginx/html
COPY docs/manuals/user-guide.pdf /usr/share/nginx/html/user-guide.pdf
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=backend:5000
EXPOSE 80
# 127.0.0.1, а не localhost: при включённом в Docker IPv6 localhost — это ::1, а nginx слушает только IPv4
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:80/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
