# Order Bridge frontend — production image.
#
#   docker build -f order-bridge/Dockerfile -t uniops-order-bridge-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

# Every VITE_* must be declared as BOTH ARG and ENV — ARG alone is not visible
# to the `vite build` process below (same pattern as every module Dockerfile).
ARG VITE_API_URL
ARG VITE_PORTAL_URL
ARG VITE_EPMS_API_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL

COPY order-bridge/package.json order-bridge/package-lock.json ./order-bridge/
COPY packages/shell ./packages/shell
RUN cd order-bridge && npm install --install-links --no-audit --no-fund

COPY order-bridge ./order-bridge
RUN cd order-bridge && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY order-bridge/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/order-bridge/dist /usr/share/nginx/html
EXPOSE 80
