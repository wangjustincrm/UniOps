# Sales frontend — production image.
#
#   docker build -f sales/Dockerfile -t uniops-sales-web:<tag> .   (context = repo root)

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

COPY sales/package.json sales/package-lock.json ./sales/
COPY packages/shell ./packages/shell
RUN cd sales && npm ci --install-links --no-audit --no-fund

COPY sales ./sales
RUN cd sales && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY sales/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/sales/dist /usr/share/nginx/html
EXPOSE 80
