# tai-distribution

[![CI](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml/badge.svg)](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Client distribution for **tai**: the official container image, a Docker Compose
bundle, a Helm chart, and the release CI that publishes them. One version story
pins every first-party `tai42-*` package.

## Image

`docker.io/tai42/tai` (primary) · `ghcr.io/tai42ai/tai` (mirror) — one
minimal-core, multi-arch (amd64 + arm64) image. It ships the platform core plus
one reference provider per infrastructure kind — local storage, the arq task
backend, redis identity, and the k8s config provider — and bakes the built
tai-studio SPA, so a single image runs every deployment role. What activates at
boot is decided by your manifest and environment.

Every other plugin (the channels, the S3/GitHub storages, the celery/rq
backends, the connectors, the webhook verifiers, monitoring, accounts, and the
extra tools) is **not** baked in. Add the ones you need at runtime through the
[marketplace](#adding-plugins) or bake them into a
[derived image](#adding-plugins).

### One image, three commands

The entrypoint is `tai`; the default command is `serve`.

| Command | Role |
|---|---|
| `tai serve` | HTTP server (MCP + Studio SPA, same origin) — default |
| `tai backend worker` | Task backend worker (arq by default) |
| `tai metrics` | Standalone Prometheus metrics server |

```sh
docker run --rm docker.io/tai42/tai:latest --help
docker run --rm docker.io/tai42/tai:latest doctor
```

### Tags

Distribution semver — `X.Y.Z`, `X.Y`, `X`, and `latest`. Each tag pins the
`tai42-*` packages at a matching release.

### Config

`/app` is the config dir (and `WORKDIR`): mount your `.env` and `manifest.yml`
there. `TAI_CONFIG_DIR_PATH` moves the dir; `TAI_MANIFEST_PATH` moves the
manifest file alone. The serve port defaults to `8000` (`--port`, or the
`APP_ARGS_PORT` env var); the image `HEALTHCHECK` probes `:8000/health`.

### Adding plugins

The image is the minimal core. Add the plugins a deployment needs one of two
ways:

**Runtime, via the marketplace.** Install, update, and uninstall plugins against
a running server through the marketplace API/Studio — no rebuild. A
marketplace install `pip install`s the package into the server's environment and
patches the manifest in place, so the plugin is live on the next config reload
with no restart. For those installs to **survive a container recreation**, set
`TAI_PLUGINS_PREFIX` to a mounted, persistent directory (see
[Persistence](#persistence)); without it, a marketplace install lands in the
image's ephemeral venv and is lost when the container is replaced.

**Build time, via a derived image.** Bake extra plugins into your own image on
top of this one — the reproducible path for a fixed roster:

```dockerfile
FROM docker.io/tai42/tai:latest
# The base image ends on USER tai over a root-owned venv, so become root to
# install, then drop back to the non-root runtime user. Use `python -m pip`
# (the venv interpreter is first on PATH; a bare `pip` would be the base
# image's system pip and install outside the venv, where the server never
# looks).
USER root
RUN python -m pip install tai42-channel-slack tai42-storage-s3
USER tai
```

Then reference the added plugins from your `manifest.yml`.

### Persistence

A real deployment gives the server two writable, persistent mounts, both located
by env vars the skeleton reads:

| What | Env var | Default | Why it must persist |
|---|---|---|---|
| Plugin prefix directory | `TAI_PLUGINS_PREFIX` | image venv (ephemeral) | Where marketplace installs land; on a persistent disk they survive container recreation. |
| Manifest file | `TAI_MANIFEST_PATH` (or `TAI_CONFIG_DIR_PATH` for the whole config dir) | `/app/manifest.yml` | Marketplace installs patch the manifest; losing it drops every runtime-added plugin's registration. |

The two work together: the prefix keeps the installed **code**, the manifest
keeps its **registration**. Persist only one and a recreated container boots
with the halves out of sync.

## Building locally

Release builds pull `tai42-*` from PyPI and fetch tai-studio at the commit SHA
recorded in `docker/STUDIO_REF`. A dev build instead uses your local sibling
checkouts (all `tai-*` repos cloned beside this one). Because the sibling
context is the parent directory of every checkout — which may hold untracked
secrets — the dev build MUST run on a LOCAL builder only:

```sh
docker buildx build -f docker/Dockerfile \
  --build-context siblings=/path/to/checkouts \
  --build-arg SOURCE=local \
  --load -t docker.io/tai42/tai:latest .
```

To build and run the whole stack from source, layer the
`docker-compose.local.yml` override on the base compose — it adds the same
`SOURCE=local` build to every app service, so `up --build` builds the image from
your sibling checkouts and starts the stack against it. It takes the same
`.env` + `config/` the quickstart below needs:

```sh
cd compose
cp .env.example .env                 # fill POSTGRES_PASSWORD etc.
mkdir -p config && cp manifest.example.yml config/manifest.yml && touch config/.env
TAI_VERSION=local docker compose \
  -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

The `siblings` build context defaults to the parent of the checkouts (`../..`);
override it with `TAI_SIBLINGS`. Because that context may hold untracked
secrets, run this on a LOCAL builder only — never CI or a shared registry.

## Repository layout

```
docker/Dockerfile      multi-stage image (SOURCE=local | pypi)
docker/STUDIO_REF      tai-studio commit SHA the release image builds from
compose/               Docker Compose bundle (core stack + profiles)
compose/langfuse/      standalone Langfuse stack (optional monitoring)
charts/tai/            Helm chart
.github/workflows/     release + CI
```

Compose quickstart: in `compose/`, copy `.env.example` to `.env` and fill the
required values (the stack refuses to start while any required secret is
unset); create the config dir — `mkdir -p config && cp manifest.example.yml
config/manifest.yml && touch config/.env`; then `docker compose up -d`. Optional
profiles: `--profile celery` (RabbitMQ broker), `--profile minio` (S3
storage), `--profile agents-redis` (module-capable Redis). The `celery` and
`minio` profiles drive the celery backend and S3 storage plugins, which are not
in the minimal image — add them via a derived image or a marketplace install
first. Langfuse runs as its own stack from `compose/langfuse/` and likewise
needs the monitoring plugin added.

## Self-hosting docs

Transport, environment, and config-provider guidance for running a server for
real lives in the [deploy guide](https://tai42.ai/guides/deploy). The chart's own
values, RBAC, and secret handling are documented in
[`charts/tai/README.md`](./charts/tai/README.md).

## License

Apache-2.0. See [LICENSE](./LICENSE).
