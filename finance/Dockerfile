# Finance frontend — production image.
#
#   docker build -f finance/Dockerfile -t uniops-finance-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

ARG VITE_PORTAL_URL
ARG VITE_FINANCE_API_URL
ARG VITE_BUDGET_API_URL
ARG VITE_EPMS_API_URL
ARG VITE_OA_API_URL
ARG VITE_MDM_API_URL
ARG VITE_EPMS_URL
ARG VITE_OA_URL
ARG VITE_VMS_URL
ENV VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_FINANCE_API_URL=$VITE_FINANCE_API_URL \
    VITE_BUDGET_API_URL=$VITE_BUDGET_API_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_OA_API_URL=$VITE_OA_API_URL \
    VITE_MDM_API_URL=$VITE_MDM_API_URL \
    VITE_EPMS_URL=$VITE_EPMS_URL \
    VITE_OA_URL=$VITE_OA_URL \
    VITE_VMS_URL=$VITE_VMS_URL

# package-lock.json is optional (finance has no standalone lockfile — the
# workspace root governs); the glob copies it only if present.
COPY finance/package*.json ./finance/
COPY packages/shell ./packages/shell
RUN cd finance && npm install --install-links --no-audit --no-fund

COPY finance ./finance
RUN cd finance && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY finance/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/finance/dist /usr/share/nginx/html
EXPOSE 80
