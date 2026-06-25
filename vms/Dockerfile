# VMS frontend — production image.
#
# IMPORTANT: build with the repo ROOT as context so the @uniops/shell workspace
# package is available:
#
#   docker build -f vms/Dockerfile -t uniops-vms-web:<tag> .
#
# Two stages: Node builds the static bundle (resolving the shared package), then
# a tiny nginx image serves it. Vite inlines VITE_* at build time, so pass the
# production API URLs as build args.

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

# API endpoints are baked in at build time (Vite static replacement).
ARG VITE_API_URL
ARG VITE_EPMS_API_URL
ARG VITE_PORTAL_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL

# Install vms deps + the shared package first (cached unless manifests change).
# --install-links copies @uniops/shell into vms/node_modules as a real package,
# so its transitive deps (zustand, clsx…) resolve from vms/node_modules.
COPY vms/package.json vms/package-lock.json ./vms/
COPY packages/shell ./packages/shell
RUN cd vms && npm install --install-links --no-audit --no-fund

# Build the app (vite build only — typecheck runs separately in CI, and the
# project's `tsc -b` trips on a TS6 baseUrl deprecation).
COPY vms ./vms
RUN cd vms && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY vms/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/vms/dist /usr/share/nginx/html
EXPOSE 80
