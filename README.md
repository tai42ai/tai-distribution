# tai-distribution

[![CI](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml/badge.svg)](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Client distribution for **tai**: the official container image, a Docker Compose
bundle, a Helm chart, and the release CI that publishes them. One version story
pins every first-party `tai42-*` package.

## Image

`docker.io/tai42/tai` (primary) · `ghcr.io/tai42ai/tai` (mirror) — one
batteries-included, multi-arch (amd64 + arm64) image. It installs the full
first-party plugin set and bakes the built tai-studio SPA, so a single image
runs every deployment role. What activates at boot is decided by your manifest
and environment.

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
there. The serve port defaults to `8000` (`--port`, or the `APP_ARGS_PORT`
env var); the image `HEALTHCHECK` probes `:8000/health`.

### Extending the image

Add your own plugins on top of the official image:

```dockerfile
FROM docker.io/tai42/tai:latest
# install your extra plugin packages, then reference them from your manifest
```

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
storage), `--profile agents-redis` (module-capable Redis). Langfuse runs as
its own stack from `compose/langfuse/`.

## Self-hosting docs

Transport, environment, and config-provider guidance for running a server for
real lives in the [deploy guide](https://tai42.ai/guides/deploy). The chart's own
values, RBAC, and secret handling are documented in
[`charts/tai/README.md`](./charts/tai/README.md).

## License

Apache-2.0. See [LICENSE](./LICENSE).
