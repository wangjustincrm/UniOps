# Booking frontend — production image.
#
# IMPORTANT: build with the repo ROOT as context so the @uniops/shell workspace
# package is available:
#
#   docker build -f booking/Dockerfile -t uniops-booking-web:<tag> .
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

# Install booking deps + the shared package first (cached unless manifests change).
# --install-links copies @uniops/shell into booking/node_modules as a real package,
# so its transitive deps (zustand, clsx…) resolve from booking/node_modules.
COPY booking/package.json booking/package-lock.json ./booking/
COPY packages/shell ./packages/shell
RUN cd booking && npm install --install-links --no-audit --no-fund

# Build the app (vite build only — typecheck runs separately in CI, and the
# project's `tsc -b` trips on a TS6 baseUrl deprecation).
COPY booking ./booking
RUN cd booking && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY booking/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/booking/dist /usr/share/nginx/html
EXPOSE 80
