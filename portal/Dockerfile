# Portal frontend — production image.
#
#   docker build -f portal/Dockerfile -t uniops-portal-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

ARG VITE_EPMS_URL
ARG VITE_OA_URL
ARG VITE_VMS_URL
ARG VITE_EPMS_API_URL
ARG VITE_OA_API_URL
ARG VITE_VMS_API_URL
ARG VITE_BUDGET_API_URL
ARG VITE_MDM_API_URL
ENV VITE_EPMS_URL=$VITE_EPMS_URL \
    VITE_OA_URL=$VITE_OA_URL \
    VITE_VMS_URL=$VITE_VMS_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_OA_API_URL=$VITE_OA_API_URL \
    VITE_VMS_API_URL=$VITE_VMS_API_URL \
    VITE_BUDGET_API_URL=$VITE_BUDGET_API_URL \
    VITE_MDM_API_URL=$VITE_MDM_API_URL

COPY portal/package.json portal/package-lock.json ./portal/
COPY packages/shell ./packages/shell
RUN cd portal && npm install --install-links --no-audit --no-fund

COPY portal ./portal
RUN cd portal && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY portal/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/portal/dist /usr/share/nginx/html
EXPOSE 80
