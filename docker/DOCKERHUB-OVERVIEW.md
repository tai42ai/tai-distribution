# tai

The official image of the [tai42 platform](https://github.com/tai42ai/tai42) — one minimal-core, multi-arch (amd64 + arm64) image that runs every deployment role. It ships the platform core plus one reference provider per infrastructure kind (local storage, the arq task backend, redis identity, the k8s config provider) and bakes the built Studio SPA. What activates at boot is decided by your manifest and environment.

- **Docs:** [docs.tai42.ai](https://docs.tai42.ai)
- **Distribution (Compose bundle, Helm chart, release CI):** [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution)
- **Mirror:** `ghcr.io/tai42ai/tai`

## How to use this image

The entrypoint is `tai`; the default command is `serve`.

| Command | Role |
| --- | --- |
| `tai serve` | HTTP server (MCP + Studio SPA, same origin) — default |
| `tai backend worker` | Task backend worker (arq by default) |
| `tai metrics` | Standalone Prometheus metrics server |

```console
$ docker run --rm docker.io/tai42/tai:latest --help
$ docker run --rm docker.io/tai42/tai:latest doctor
```

The fastest full-stack path is the [Compose bundle](https://docs.tai42.ai/self-hosted/quickstart): serve, the backend worker, metrics, Postgres, and Redis wired together, with a one-shot `tai db migrate` that serve and the worker gate on.

```console
$ git clone https://github.com/tai42ai/tai-distribution
$ cd tai-distribution/compose
$ cp .env.example .env   # set POSTGRES_PASSWORD, pin TAI_VERSION
$ mkdir -p config && cp manifest.example.yml config/manifest.yml && touch config/.env
$ docker compose up -d
```

## Configuration

`/app` is the config dir (and `WORKDIR`): mount your `.env` and `manifest.yml` there. `TAI_CONFIG_DIR_PATH` moves the dir; `TAI_MANIFEST_PATH` moves the manifest file alone. The serve port defaults to `8000` (`--port`, or the `APP_ARGS_PORT` env var); the image `HEALTHCHECK` probes `:8000/health`.

## Where to store data

Two mounts a real deployment must keep across container recreation, each located by an env var the server reads:

| Mount | Env var | Holds |
| --- | --- | --- |
| Config directory | `TAI_MANIFEST_PATH` (in `TAI_CONFIG_DIR_PATH`) | the manifest — the registration of every runtime-installed plugin |
| Plugin prefix | `TAI_PLUGINS_PREFIX` | the installed plugin code |

Persist both, or a recreated container boots with the code and its registration out of sync. The bundled Postgres and Redis keep their own named data volumes.

## Adding plugins

The image is the minimal core; channels, extra storages and backends, connectors, webhook verifiers, monitoring, accounts, and the extra tools are added per deployment:

- **Runtime, via the marketplace** — install, update, and uninstall against a running server through the marketplace API or Studio; no rebuild.
- **Pre-installed prefix or derived image** — `pip install --prefix` onto the persistent plugins volume, or bake plugins into a derived image. `tai db migrate` discovers migration chains from both the marketplace install store and the plugins prefix.

See [Add plugins](https://docs.tai42.ai/operate#add-plugins).

## Tags

Distribution semver — `X.Y.Z`, `X.Y`, `X`, and `latest`. Each tag pins the first-party `tai42-*` packages at a matching release, so one version story covers the whole image.

## License

Apache-2.0. The full source of the image build lives in [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution).
