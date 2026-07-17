# OA frontend — production image.
#
#   docker build -f oa/Dockerfile -t uniops-oa-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

ARG VITE_API_URL
ARG VITE_PORTAL_URL
ARG VITE_EPMS_URL
ARG VITE_EPMS_API_URL
ARG VITE_BUDGET_API_URL
ARG VITE_FINANCE_API_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_EPMS_URL=$VITE_EPMS_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_BUDGET_API_URL=$VITE_BUDGET_API_URL \
    VITE_FINANCE_API_URL=$VITE_FINANCE_API_URL

COPY oa/package.json oa/package-lock.json ./oa/
COPY packages/shell ./packages/shell
RUN cd oa && npm install --install-links --no-audit --no-fund

COPY oa ./oa
RUN cd oa && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY oa/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/oa/dist /usr/share/nginx/html
EXPOSE 80
