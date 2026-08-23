# MRP frontend — production image.
#
#   docker build -f mrp/Dockerfile -t uniops-mrp-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

# Every VITE_* must be declared as BOTH ARG and ENV — ARG alone is not visible
# to the `vite build` process below, and a missing pair silently falls back to
# the localhost default baked into src/lib/*.ts (pending requests forever in
# prod). This has bitten the project three times already — see other module
# Dockerfiles for the same pattern.
ARG VITE_API_URL
ARG VITE_PORTAL_URL
ARG VITE_EPMS_API_URL
ARG VITE_MDM_API_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_MDM_API_URL=$VITE_MDM_API_URL

COPY mrp/package.json mrp/package-lock.json ./mrp/
COPY packages/shell ./packages/shell
RUN cd mrp && npm install --install-links --no-audit --no-fund

COPY mrp ./mrp
RUN cd mrp && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY mrp/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/mrp/dist /usr/share/nginx/html
EXPOSE 80
