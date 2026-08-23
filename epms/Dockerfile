# EPMS frontend — production image.
#
# Build with the repo ROOT as context (uniform with the other frontends; also
# lets this app adopt the @uniops/shell workspace package later with no change):
#
#   docker build -f epms/Dockerfile -t uniops-epms-web:<tag> .

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

ARG VITE_API_URL
ARG VITE_FILE_SERVER_URL
ARG VITE_OA_URL
ARG VITE_PORTAL_URL
ARG VITE_BUDGET_API_URL
ARG VITE_MDM_API_URL
ARG VITE_EXPENSE_API_URL
ARG VITE_FINANCE_API_URL
ARG VITE_FINANCE_URL
ARG VITE_VMS_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_FILE_SERVER_URL=$VITE_FILE_SERVER_URL \
    VITE_OA_URL=$VITE_OA_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_BUDGET_API_URL=$VITE_BUDGET_API_URL \
    VITE_MDM_API_URL=$VITE_MDM_API_URL \
    VITE_EXPENSE_API_URL=$VITE_EXPENSE_API_URL \
    VITE_FINANCE_API_URL=$VITE_FINANCE_API_URL \
    VITE_FINANCE_URL=$VITE_FINANCE_URL \
    VITE_VMS_URL=$VITE_VMS_URL

# Shared workspace package is copied so the app can depend on it (file: + the
# --install-links copy) without changing this Dockerfile. Harmless when unused.
COPY epms/package.json epms/package-lock.json ./epms/
COPY packages/shell ./packages/shell
RUN cd epms && npm install --install-links --no-audit --no-fund

COPY epms ./epms
RUN cd epms && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY epms/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/epms/dist /usr/share/nginx/html
EXPOSE 80
