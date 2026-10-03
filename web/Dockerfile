FROM node:22-bookworm-slim AS base

ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH

RUN corepack enable

FROM base AS build

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY patches ./patches
RUN pnpm install --frozen-lockfile

COPY . .

# The release shown on the page, e.g. web/v0.9.0.
ARG AI_DETECTOR_WEB_VERSION=dev
ENV AI_DETECTOR_WEB_VERSION=$AI_DETECTOR_WEB_VERSION
RUN pnpm exec svelte-kit sync && pnpm build && pnpm prune --prod --ignore-scripts

FROM base AS runtime

WORKDIR /app

ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3000

COPY --from=build --chown=node:node /app/build ./build
COPY --from=build --chown=node:node /app/package.json ./package.json
COPY --from=build --chown=node:node /app/node_modules ./node_modules

RUN mkdir -p /data && chown node:node /data

USER node
WORKDIR /data

EXPOSE 3000

CMD ["node", "/app/build"]
