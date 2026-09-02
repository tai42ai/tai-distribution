# syntax=docker/dockerfile:1.7-labs
# COPY --exclude is a labs-channel feature; the plain docker/dockerfile:1.7
# frontend fails to parse it. Keep the -labs suffix.

# One image, two source modes, selected by --build-arg SOURCE=<local|pypi>:
#   local  — dev build from local checkouts (studio built from the live
#            tai-studio sibling tree; tai42-* installed from the tai42/ monorepo
#            member trees at core/{contract,kit,cli,skeleton} + plugins/*). Runs on a
#            LOCAL builder only: the `siblings` named build context is the
#            directory holding tai42/ and tai-studio/ and carries untracked
#            secrets.
#   pypi   — release build (studio built from tai42ai/tai-studio at STUDIO_REF;
#            tai42-* installed from PyPI at the exact pins in
#            docker/pypi-requirements.txt, binary wheels only).
ARG SOURCE=pypi
ARG VERSION=1.6.43 # x-release-please-version
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
# corepack resolves the pnpm version and integrity hash pinned in tai-studio's
# packageManager field, verifying the fetched binary; disable its first-run
# download prompt so the build is non-interactive. That binary is fetched on the
# first `pnpm` call in the studio-local/studio-pypi stages, where the install is
# wrapped in a bounded retry: a single connection reset there would otherwise
# abort an already-pinned build.
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
# Retry the network-bound install (3 attempts, backoff, loud failure); the
# recursive build is local and stays outside it.
RUN n=1; while true; do \
      pnpm install --frozen-lockfile && break; \
      status=$?; \
      [ "$n" -ge 3 ] && { echo "pnpm install failed after $n attempts (exit $status)" >&2; exit "$status"; }; \
      echo "pnpm install attempt $n failed (exit $status); retrying in $((n * 5))s" >&2; \
      sleep $((n * 5)); n=$((n + 1)); \
    done \
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
# Retry the network-bound install (3 attempts, backoff, loud failure); the
# recursive build is local and stays outside it.
RUN n=1; while true; do \
      pnpm install --frozen-lockfile && break; \
      status=$?; \
      [ "$n" -ge 3 ] && { echo "pnpm install failed after $n attempts (exit $status)" >&2; exit "$status"; }; \
      echo "pnpm install attempt $n failed (exit $status); retrying in $((n * 5))s" >&2; \
      sleep $((n * 5)); n=$((n + 1)); \
    done \
 && pnpm -r build

FROM studio-${SOURCE} AS studio

# ---------------------------------------------------------------------------
# venv build — installs the minimal core plugin set into /opt/tai/venv: the
# platform core plus one reference provider per infrastructure kind (local
# storage, arq backend, redis identity, k8s config). Every other channel,
# storage, backend, connector, verifier, and tool is installed at runtime via
# the marketplace or baked into a derived image.
# Splits on SOURCE for the same reason as studio: venv-local COPYs the tai42/
# monorepo member trees and installs from them; venv-pypi installs pinned wheels
# and never references the siblings context (which does not exist on CI).
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS uv-base
# Pinned uv binary from the official distroless image; matches the local uv.
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV VIRTUAL_ENV=/opt/tai/venv \
    PATH=/opt/tai/venv/bin:$PATH \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN uv venv "$VIRTUAL_ENV" && python -m ensurepip --upgrade

# Dev install: COPY each monorepo member's FULL directory (build backends need
# README/LICENSE/NOTICE named in license-files — pyproject+src alone fails).
# Excludes drop stale build artifacts: a leftover setuptools `build/lib` is
# reused instead of `src/`, packaging stale code; `dist/` and `*.egg-info`
# carry stale wheels/metadata. They must not seed the build.
# then ONE uv pip install so the resolver satisfies the first-party
# ranges from the local trees alone, without reaching PyPI for them — which is
# what makes this stage work when those packages are unpublished. --no-sources is
# required: several packages' pyproject `[tool.uv.sources]` mark first-party
# deps `editable = true`, which uv honors under `uv pip install` and would
# install them as .pth links into /src — a path that does not exist in the
# final stage. --no-sources ignores those tables so every package is a regular
# wheel install that survives the venv copy.
FROM uv-base AS venv-local
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/core/contract/          /src/contract/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/core/kit/               /src/kit/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/toolbox/        /src/toolbox/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/core/cli/               /src/cli/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/core/skeleton/          /src/skeleton/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/agents/         /src/agents/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/storage-local/   /src/storage-local/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/backend-arq/     /src/backend-arq/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/identity-redis/  /src/identity-redis/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/config-k8s/      /src/config-k8s/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/sandbox-docker/  /src/sandbox-docker/
COPY --from=siblings --exclude=.git --exclude=.venv --exclude=**/__pycache__ --exclude=.env --exclude=**/.env --exclude=.env.* --exclude=**/.env.* --exclude=build --exclude=dist --exclude=**/*.egg-info tai42/plugins/sandbox-local/   /src/sandbox-local/
# The msgpack/setuptools floors hold the transitive versions above the CVEs the
# image vulnerability gate (HIGH, fixed-only) refuses; both venv flavors carry them.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-sources \
      /src/contract \
      /src/kit \
      /src/toolbox \
      /src/cli \
      "/src/skeleton[toolbox,files]" \
      /src/agents \
      /src/storage-local \
      /src/backend-arq \
      /src/identity-redis \
      /src/config-k8s \
      /src/sandbox-docker \
      /src/sandbox-local \
      "msgpack>=1.2.1" \
      "setuptools>=78.1.1"

# Release install: the first-party set comes from docker/pypi-requirements.txt,
# which pins EACH package to its own version. Packages release independently, so
# a single shared version is not expressible — a fix to one plugin bumps that
# plugin alone. That file is the image's manifest of contents; the VERSION
# build-arg names the IMAGE, never its contents.
#
# Wheels only, with a named carve-out for the three transitive dependencies that
# publish no wheel in any release — jinja2schema, odfpy and crontab, all sdist
# only, all pure Python, so building them needs no compiler and the slim final
# image is unaffected. Every other package stays wheels-only, so a dependency
# missing an arm64/amd64 wheel still fails the build loudly instead of falling
# into a source build. (Measured by resolving the pinned set, not assumed.)
FROM uv-base AS venv-pypi
ARG VERSION
COPY docker/pypi-requirements.txt /tmp/pypi-requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --only-binary=:all: --no-binary=jinja2schema,odfpy,crontab \
      -r /tmp/pypi-requirements.txt \
      "msgpack>=1.2.1" \
      "setuptools>=78.1.1"

FROM venv-${SOURCE} AS venv

# ---------------------------------------------------------------------------
# Final image — minimal core, non-root, one entrypoint (`tai`).
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS final

# The floating slim base can lag Debian security uploads (trivy gates the build
# on fixable CRITICAL/HIGH CVEs); apply the security updates the tag has not
# absorbed yet.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# The base interpreter's pip is unused at runtime (the app venv carries its own
# for marketplace installs); dropping it removes its vendored copies from the
# scan surface.
RUN /usr/local/bin/python -m pip uninstall -y pip

ARG VERSION
LABEL org.opencontainers.image.title="tai" \
      org.opencontainers.image.description="Minimal-core tai server image: serve, backend worker, and metrics from one image; extra plugins install at runtime via the marketplace or bake into a derived image." \
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
# Bounded conversation-checkpoint retention for image deployments (the library
# default keeps checkpoints forever); operators override or unset per install.
ENV LLM_PROVIDER_CHECKPOINT_TTL_MINUTES=43200
COPY --from=venv   /opt/tai/venv        /opt/tai/venv
COPY --from=studio /studio/apps/studio/dist /opt/tai/studio-dist

# /app is the config dir (.env + manifest.yml) and WORKDIR; owned by tai so a
# writable bind mount / volume works under the non-root user.
RUN install -d -o tai -g tai /app
WORKDIR /app
# Numeric uid:gid so runtimes can verify non-root without resolving the name.
USER 1000:1000

# Liveness against serve's default :8000/health, via the Python stdlib —
# python:3.13-slim ships neither curl nor wget. Generous start-period: boot
# does manifest-driven dynamic imports with variable startup time, and a
# start-period of 0 would flap slow boots to unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"]

ENTRYPOINT ["tai"]
CMD ["serve"]
