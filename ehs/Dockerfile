# Safety frontend — production image.
#
#   docker build -f ehs/Dockerfile -t uniops-safety-web:<tag> .   (context = repo root)

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /repo

# Every VITE_* must be declared as BOTH ARG and ENV — ARG alone is not visible
# to the `vite build` below, and a missing pair silently falls back to the
# localhost default baked into src/lib/api.ts, which in production means every
# request pends forever. This has bitten the project four times.
ARG VITE_API_URL
ARG VITE_PORTAL_URL
ARG VITE_EPMS_API_URL
ARG VITE_MDM_API_URL
ARG VITE_FILE_API_URL
ENV VITE_API_URL=$VITE_API_URL \
    VITE_PORTAL_URL=$VITE_PORTAL_URL \
    VITE_EPMS_API_URL=$VITE_EPMS_API_URL \
    VITE_MDM_API_URL=$VITE_MDM_API_URL \
    VITE_FILE_API_URL=$VITE_FILE_API_URL

COPY ehs/package.json ehs/package-lock.json ./ehs/
COPY packages/shell ./packages/shell
RUN cd ehs && npm install --install-links --no-audit --no-fund

COPY ehs ./ehs
RUN cd ehs && npx vite build

# ── Serve stage ───────────────────────────────────────────────────────────────
FROM nginx:alpine AS serve
COPY ehs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/ehs/dist /usr/share/nginx/html
EXPOSE 80
