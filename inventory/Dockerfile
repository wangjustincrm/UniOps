# Inventory frontend — production image.
#
#   docker build -f inventory/Dockerfile -t uniops-inventory-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

# Every VITE_* must be declared as BOTH ARG and ENV — ARG alone is not visible
# to the `vite build` process below, and a missing pair silently falls back to
# the localhost default baked into src/lib/*.ts (pending requests forever in
# prod). This has bitten the project three times already — see other module
# Dockerfiles for the same pattern.
# VITE_API_URL is inventory-api; VITE_MRP_API_URL is mrp-api, which still
# serves the WMS Inventory data (design §7.1).
ARG VITE_API_URL
ARG VITE_MRP_API_URL
ARG VITE_PORTAL_URL
ARG VITE_EPMS_API_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_MRP_API_URL=$VITE_MRP_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL

COPY inventory/package.json inventory/package-lock.json ./inventory/
COPY packages/shell ./packages/shell
RUN cd inventory && npm install --install-links --no-audit --no-fund

COPY inventory ./inventory
RUN cd inventory && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY inventory/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/inventory/dist /usr/share/nginx/html
EXPOSE 80
