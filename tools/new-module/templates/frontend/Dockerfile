# %%label%% frontend — production image.
#
#   docker build -f %%frontend%%/Dockerfile -t uniops-%%frontend%%-web:<tag> .   (context = repo root)

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

COPY %%frontend%%/package.json %%frontend%%/package-lock.json ./%%frontend%%/
COPY packages/shell ./packages/shell
RUN cd %%frontend%% && npm ci --install-links --no-audit --no-fund

COPY %%frontend%% ./%%frontend%%
RUN cd %%frontend%% && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY %%frontend%%/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/%%frontend%%/dist /usr/share/nginx/html
EXPOSE 80
