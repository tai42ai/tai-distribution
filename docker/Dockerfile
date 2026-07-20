# syntax=docker/dockerfile:1.7-labs
# COPY --exclude is a labs-channel feature; the plain docker/dockerfile:1.7
# frontend fails to parse it. Keep the -labs suffix.

# One image, two source modes, selected by --build-arg SOURCE=<local|pypi>:
#   local  — dev build from sibling checkouts (studio built from the local
#            tai-studio tree; tai42-* installed from local package trees). Runs on
#            a LOCAL builder only: the `siblings` named build context is the
#            parent directory of every checkout and carries untracked secrets.
#   pypi   — release build (studio built from tai42ai/tai-studio at STUDIO_REF;
#            tai42-* installed from PyPI at pinned ==${VERSION}, binary wheels only).
ARG SOURCE=pypi
ARG VERSION=0.1.0
ARG STUDIO_REF

# ---------------------------------------------------------------------------
# Studio build — produces the tai-studio SPA dist. Splits on SOURCE because a
# single stage cannot conditionally COPY-from-siblings vs git-fetch inline.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS studio-base
# node:22-alpine ships no git, but git is needed both to fetch the release
# source (studio-pypi) and by tai-studio's own `prepare` hook (lefthook
# install) that runs on every `pnpm install`.
RUN apk add --no-cache git
# corepack resolves the pnpm version pinned in tai-studio's packageManager
# field; disable its first-run download prompt so the build is non-interactive.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /studio

# Dev: copy the ENTIRE local tai-studio tree. apps/studio alone cannot build —
# it pulls workspace:* siblings and catalog: ranges from the root
# pnpm-workspace.yaml. Excludes keep build artifacts and any live secrets
# (sibling checkouts may hold real .env files) out of the layer.
FROM studio-base AS studio-local
COPY --from=siblings \
     --exclude=.git \
     --exclude=.venv \
     --exclude=node_modules \
     --exclude=**/node_modules \
     --exclude=**/__pycache__ \
     --exclude=.env \
     --exclude=**/.env \
     --exclude=.env.* \
     --exclude=**/.env.* \
     tai-studio/ /studio/
# tai-studio's install lifecycle is git-based (lefthook's postinstall and the
# root `prepare` hook both run `lefthook install`, which aborts without a repo).
# The COPY excludes .git, so init an empty repo; the hooks it installs are inert
# files never executed during the build.
RUN git init -q
# Build recursively (tai-studio's own CI recipe): @tai42/studio-app's tsc build
# references 17 workspace packages' emitting configs, so they must be built
# first. `pnpm --filter @tai42/studio-app build` alone leaves them unbuilt and
# tsc fails to resolve them. The app build emits apps/studio/dist.
RUN pnpm install --frozen-lockfile \
 && pnpm -r build

# Release: fetch tai42ai/tai-studio at the pinned commit SHA. node:22-alpine
# ships no git; a bare SHA needs init + fetch + checkout FETCH_HEAD (not a
# shallow clone).
FROM studio-base AS studio-pypi
ARG STUDIO_REF
RUN test -n "$STUDIO_REF" || (echo "STUDIO_REF build-arg is required for SOURCE=pypi" >&2 && exit 1)
RUN git init \
 && git remote add origin https://github.com/tai42ai/tai-studio.git \
 && git fetch origin "$STUDIO_REF" \
 && git checkout FETCH_HEAD
# Build recursively (tai-studio's own CI recipe): @tai42/studio-app's tsc build
# references 17 workspace packages' emitting configs, so they must be built
# first. The app build emits apps/studio/dist.
RUN pnpm install --frozen-lockfile \
 && pnpm -r build

FROM studio-${SOURCE} AS studio

# ---------------------------------------------------------------------------
# venv build — installs the full first-party plugin set into /opt/tai/venv.
# Splits on SOURCE for the same reason as studio: venv-local COPYs the sibling
# package trees and installs from them; venv-pypi installs pinned wheels and
# never references the siblings context (which does not exist on CI).
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS uv-base
# Pinned uv binary from the official distroless image; matches the local uv.
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV VIRTUAL_ENV=/opt/tai/venv \
    PATH=/opt/tai/venv/bin:$PATH \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN uv venv "$VIRTUAL_ENV"

# Dev install: COPY each package's FULL directory (build backends need
# README/LICENSE/NOTICE named in license-files — pyproject+src alone fails).
# Excludes drop stale build artifacts: a leftover setuptools `build/lib` is
# reused instead of `src/`, packaging stale code; `dist/` and `*.egg-info`
# carry stale wheels/metadata. They must not seed the build.
# then ONE uv pip install so the resolver satisfies the >=0.1,<0.2 first-party
# ranges from the local trees alone, without reaching PyPI for them — which is
# what makes this stage work when those packages are unpublished. --no-sources is
# required: several packages' pyproject `[tool.uv.sources]` mark first-party
# deps `editable = true`, which uv honors under `uv pip install` and would
# install them as .pth links into /src — a path that does not exist in the
# final stage. --no-sources ignores those tables so every package is a regular
# wheel install that survives the venv copy.
FROM uv-base AS venv-local
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-contract/               /src/tai-contract/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-kit/                    /src/tai-kit/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-toolbox/                /src/tai-toolbox/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-skeleton/               /src/tai-skeleton/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-agents/                 /src/tai-agents/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-config-k8s/             /src/tai-config-k8s/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-connector-atlassian/    /src/tai-connector-atlassian/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-connector-google/       /src/tai-connector-google/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-webhook-verifier-github/ /src/tai-webhook-verifier-github/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-storage-github/         /src/tai-storage-github/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-storage-local/          /src/tai-storage-local/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-storage-s3/             /src/tai-storage-s3/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-identity-redis/         /src/tai-identity-redis/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-dynamic-postgres-mcp/   /src/tai-dynamic-postgres-mcp/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-backend-arq/            /src/tai-backend-arq/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-backend-celery/         /src/tai-backend-celery/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-backend-rq/             /src/tai-backend-rq/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-channel-slack/          /src/tai-channel-slack/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-channel-telegram/       /src/tai-channel-telegram/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-channel-twilio/         /src/tai-channel-twilio/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-monitoring-langfuse/    /src/tai-monitoring-langfuse/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-accounts-oidc/       /src/tai-accounts-oidc/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-accounts-postgres/       /src/tai-accounts-postgres/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai-identity-oidc/       /src/tai-identity-oidc/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-sources \
      /src/tai-contract \
      /src/tai-kit \
      /src/tai-toolbox \
      "/src/tai-skeleton[toolbox,files]" \
      /src/tai-agents \
      /src/tai-config-k8s \
      /src/tai-connector-atlassian \
      /src/tai-connector-google \
      /src/tai-webhook-verifier-github \
      /src/tai-storage-github \
      /src/tai-storage-local \
      /src/tai-storage-s3 \
      /src/tai-identity-redis \
      /src/tai-dynamic-postgres-mcp \
      /src/tai-backend-arq \
      /src/tai-backend-celery \
      /src/tai-backend-rq \
      /src/tai-channel-slack \
      /src/tai-channel-telegram \
      /src/tai-channel-twilio \
      /src/tai-monitoring-langfuse \
      /src/tai-accounts-oidc \
      /src/tai-accounts-postgres \
      /src/tai-identity-oidc

# Release install: every tai42-* package pinned to the image's own VERSION, binary
# wheels only. The slim final image has no compiler — a dependency missing an
# arm64/amd64 wheel MUST fail the build loudly, never fall into a source build.
# The pins track VERSION so a tagged image can never ship a different package set
# than its tag claims.
FROM uv-base AS venv-pypi
ARG VERSION
RUN --mount=type=cache,target=/root/.cache/uv \
    test -n "$VERSION" || (echo "VERSION build-arg is required for SOURCE=pypi" >&2 && exit 1) && \
    uv pip install --only-binary=:all: \
      tai42-contract==${VERSION} \
      tai42-kit==${VERSION} \
      tai42-toolbox==${VERSION} \
      "tai42-skeleton[toolbox,files]==${VERSION}" \
      tai42-agents==${VERSION} \
      tai42-config-k8s==${VERSION} \
      tai42-connector-atlassian==${VERSION} \
      tai42-connector-google==${VERSION} \
      tai42-webhook-verifier-github==${VERSION} \
      tai42-storage-github==${VERSION} \
      tai42-storage-local==${VERSION} \
      tai42-storage-s3==${VERSION} \
      tai42-identity-redis==${VERSION} \
      tai42-dynamic-postgres-mcp==${VERSION} \
      tai42-backend-arq==${VERSION} \
      tai42-backend-celery==${VERSION} \
      tai42-backend-rq==${VERSION} \
      tai42-channel-slack==${VERSION} \
      tai42-channel-telegram==${VERSION} \
      tai42-channel-twilio==${VERSION} \
      tai42-monitoring-langfuse==${VERSION} \
      tai42-accounts-oidc==${VERSION} \
      tai42-accounts-postgres==${VERSION} \
      tai42-identity-oidc==${VERSION}

FROM venv-${SOURCE} AS venv

# ---------------------------------------------------------------------------
# Final image — batteries-included, non-root, one entrypoint (`tai`).
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS final

ARG VERSION
LABEL org.opencontainers.image.title="tai" \
      org.opencontainers.image.description="Batteries-included tai server image: serve, backend worker, and metrics from one image." \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.vendor="tai42" \
      org.opencontainers.image.url="https://tai42.ai" \
      org.opencontainers.image.source="https://github.com/tai42ai/tai-distribution" \
      org.opencontainers.image.licenses="Apache-2.0"

# Non-root runtime user (uid 1000). The app never needs root at runtime.
RUN groupadd --gid 1000 tai \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin tai

# The venv keeps its absolute path so the console-script shebangs stay valid,
# and the same interpreter exists at the same path in this base image.
ENV VIRTUAL_ENV=/opt/tai/venv \
    PATH=/opt/tai/venv/bin:$PATH \
    STUDIO_DIST_PATH=/opt/tai/studio-dist \
    PYTHONUNBUFFERED=1
COPY --from=venv   /opt/tai/venv        /opt/tai/venv
COPY --from=studio /studio/apps/studio/dist /opt/tai/studio-dist

# /app is the config dir (.env + manifest.yml) and WORKDIR; owned by tai so a
# writable bind mount / volume works under the non-root user.
RUN install -d -o tai -g tai /app
WORKDIR /app
USER tai

# Liveness against serve's default :8000/health, via the Python stdlib —
# python:3.13-slim ships neither curl nor wget. Generous start-period: boot
# does manifest-driven dynamic imports with variable startup time, and a
# start-period of 0 would flap slow boots to unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"]

ENTRYPOINT ["tai"]
CMD ["serve"]
