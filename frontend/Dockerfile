# Stage 1: build the static production bundle
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Baked in at build time — Vite inlines VITE_* vars into the bundle, they
# can't be changed at container-start like a normal runtime env var.
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE
ARG VITE_API_KEY=
ENV VITE_API_KEY=$VITE_API_KEY
RUN npm run build

# Stage 2: serve the built assets — no Node toolchain, no dev server, no
# source in the final image, just the static output.
FROM node:20-alpine AS runtime
WORKDIR /app
RUN npm install -g serve@14 \
    && chown -R node:node /app
# Run as non-root. serve binds 5173 (>1024) so it needs no privileges, and this
# matches the backend image. Only the copied dist tree is owned by node, so a
# compromise of the static server has nothing else to reach.
USER node
COPY --from=build --chown=node:node /app/dist ./dist
EXPOSE 5173
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:5173/ >/dev/null 2>&1 || exit 1
CMD ["serve", "-s", "dist", "-l", "5173"]
