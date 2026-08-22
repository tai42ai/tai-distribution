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
with no restart — including a first-ever install (the plugin prefix is on
`sys.path` from boot even while empty, and each reload invalidates the import
caches). The exception is **router** and **middleware** plugins: their handlers
and middleware stack are frozen into the ASGI app when the process builds, so a
router or middleware plugin — net-new or an upgrade of an already-listed one —
takes effect only on the next process restart. For those installs to **survive a
container recreation**, set
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

### Sandbox (optional)

The sandbox runs agent code in an isolated per-session workspace. It is **off by
default** — baking a provider does not turn it on; a deployment enables it by
naming a provider in the manifest (`sandbox_module`) and wiring that provider's
connection env. The provider is a scalar single-instance holder, so a deployment
picks **exactly one** of two mutually exclusive modes:

- **Container mode (`sandbox_module: tai42_sandbox_docker`).** Each session is an
  isolated container created by a sandbox engine over an mTLS control API. The
  app talks only to the engine (`SANDBOX_DOCKER_HOST`) — never the docker socket,
  and the app pod keeps its hardened, non-root, read-only-rootfs posture. A
  session is an inner container of the rootless engine on its own private bridge,
  NAT'd out for internet egress and reachable to nothing else of the deployment;
  it is not on any deployment network and holds no control-API client cert.
  Egress is **open by default** (README posture): the engine's egress firewall
  allows the public internet and DNS and denies only the deployment-internal
  ranges and cloud metadata — it is not an Anthropic-only allowlist. Persistent
  workspaces are durable only when the engine's block store is provisioned (a
  named volume / PVC on node-independent storage); an ephemeral session leaves no
  durable footprint.
- **Direct/host mode (`sandbox_module: tai42_sandbox_local`).** No engine, no
  certs, no session image: the agent's code runs **directly on the host** — the
  serve/backend process itself — with **NO isolation**. The workspace is the host
  directory `<SANDBOX_LOCAL_ROOT>/<workspace_key>`, so `SANDBOX_LOCAL_ROOT` must
  resolve to provisioned, persistent storage (a volume / PVC mounted into
  serve+backend) for a persistent workspace to survive a restart or node move.
  Running the coding agent in direct mode is an **operator prerequisite**: the
  operator installs the coding runtime on the host — the `claude-agent-sdk` wheel,
  which bundles the Claude Code CLI (no separate Node.js) — at the **same pinned
  SDK version** the session image carries (below); a version mismatch is a hard
  stop, never an improvised substitute. The base server image deliberately omits
  it to stay lean.

**Host prerequisite (container mode, builtin engine).** The engine
(`docker:28-dind-rootless`) runs a **rootless** docker-in-docker daemon:
rootlesskit must create an unprivileged Linux user namespace for it. On a host
with AppArmor **enforcing** the Ubuntu 23.10+/24.04 hardening
`kernel.apparmor_restrict_unprivileged_userns=1`, that userns creation is
**denied** (verified on real runners) — the engine container exits (1) ~2s after
start with `[rootlesskit:parent] error: failed to start the child: fork/exec
/proc/self/exe: operation not permitted` and container isolation is unavailable.
To run the rootless engine there, permit unprivileged user namespaces on the host:
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (persist it under
`/etc/sysctl.d/` for reboots). The daemon still runs fully rootless — this only
relaxes a host-level userns restriction. If you cannot change the host, point
`SANDBOX_DOCKER_HOST` at an **external** engine host that allows it (a `tcp://`
endpoint over mTLS), or use the direct/host provider (`tai42_sandbox_local`, no
isolation) on a trusted single-tenant box.

**Session images (container mode).** Two pinned images ship on the release lane,
each multi-arch, SBOM- and provenance-attested, trivy-gated before push, and
cosign-keyless-signed:

| Image | Carries | Wired by |
|---|---|---|
| `ghcr.io/tai42ai/tai-sandbox-claude-code` | `claude-agent-sdk` + its bundled `claude` CLI (no runner code, no credentials) | `TAI_AGENTS_CLAUDE_SESSION_IMAGE` |
| `ghcr.io/tai42ai/tai-sandbox-exec` | lean `sh` + `python3` + coreutils only (no SDK, no runner code, no credentials) | the `langchain_deep_agent` group's own `session_image` setting |

Both are **consumed by digest, never by a bare tag** — every reference is the
`…@sha256:…` form, and the intended pull posture is cosign signature
verification:

```sh
# In .env / the manifest — pin the exact digest, not a moving tag:
TAI_AGENTS_CLAUDE_SESSION_IMAGE=ghcr.io/tai42ai/tai-sandbox-claude-code@sha256:<digest>
```

The claude session image carries the SDK and its bundled `claude` binary and
**nothing consumer-specific**: no runner code (the adapter materializes the
runner payload per turn under `/workspace/.runner`, so one published digest stays
plugin-version-agnostic) and no credentials — the chosen operator credential
(`TAI_AGENTS_CLAUDE_API_KEY` **or** `TAI_AGENTS_CLAUDE_OAUTH_TOKEN`, exactly one)
is injected into the session at runtime, never baked. The **SDK pin has a single
source**: `docker/sandbox-images/claude-code/requirements.txt`. The server-side
plugin pins the SDK to the same version, and direct/host mode installs that same
version on the host.

## Upgrading

Moving a deployment to a new release is three steps: pull the new image tag,
start it, then bring the marketplace-installed plugins up to it. Compose shown;
the same three steps apply to any runner:

**Snapshot the database first.** A release may ship new migrations, and the
chain rolls forward only — there are no down-migrations. Take a snapshot before
upgrading (a managed Postgres point-in-time-recovery restore point, or a
`pg_dump` of the compose volume) so a bad upgrade is recoverable by restoring
the snapshot and re-running the older image, not by reversing the schema.

```sh
cd compose
# 1. Pull the new tag and recreate the containers. `up -d` runs the one-shot
#    db-migrate service first: it applies any pending migrations (recorded in
#    tai_schema_history) before serve/backend start, which gate on it.
TAI_VERSION=X.Y.Z docker compose pull
TAI_VERSION=X.Y.Z docker compose up -d

# 2. Move every marketplace-installed plugin to its newest compatible version.
docker compose exec serve tai plugins upgrade --all

# 3. Verify: every row's compat verdict, and anything still quarantined.
docker compose exec serve tai plugins installed
```

**Why the plugins survive the image swap.** A new tag replaces the container
and, with it, the image's baked-in venv — but `TAI_PLUGINS_PREFIX` points at a
mounted persistent directory outside the image, and the manifest holding each
plugin's registration is likewise mounted (see [Persistence](#persistence)).
Both halves of every marketplace install therefore carry over unchanged into
the new container; only a plugin installed without the prefix (into the
ephemeral venv) is lost with the old container.

**Why step 2 exists.** What carries over was installed against the OLD
release's core, and the new release may ship a newer `tai42-contract`. A
carried-over plugin whose declared contract range excludes the running contract
cannot be loaded — and boot does not crash on it: the server **quarantines**
it, starting up without it, serving everything else, and naming the plugin and
the reason loudly in the startup log and in the installed listing.
`tai plugins upgrade --all` then moves every installed plugin onto its newest
version compatible with the running core, re-patching the manifest and
reloading as it goes, and reports one outcome per plugin (`upgraded` /
`up-to-date` / `no-compatible-version` / `failed`). Each such reload is
broadcast fleet-wide on the worker bus, so every running container re-imports
the upgraded plugins in place — no restart is needed, and this holds for the
very first install too: the prefix site dirs are appended to `sys.path` on every
boot even while empty, and each in-place reload invalidates the import caches, so
a freshly installed or upgraded plugin propagates live to running peers. The one
exception is **router** and **middleware** plugins: their handlers and middleware
stack are frozen into the ASGI app when the process builds, so a router or
middleware plugin — net-new or an upgrade of an already-listed one — takes effect
only when each pod/container restarts. Step 3's
`tai plugins installed` confirms the result: each row's compat verdict, update
availability, and any plugin still quarantined.

## Building locally

Release builds pull `tai42-*` from PyPI and fetch tai-studio at the commit SHA
recorded in `docker/STUDIO_REF`. A dev build instead uses your local checkouts:
the `tai42/` monorepo (platform members at `core/{contract,kit,cli,skeleton}` +
`plugins/*`) and the `tai-studio` repo, both cloned beside this one. Because the
siblings context is the directory holding those checkouts — which may hold
untracked secrets — the dev build MUST run on a LOCAL builder only:

```sh
docker buildx build -f docker/Dockerfile \
  --build-context siblings=/path/to/checkouts \
  --build-arg SOURCE=local \
  --load -t docker.io/tai42/tai:latest .
```

To build and run the whole stack from source, layer the
`docker-compose.local.yml` override on the base compose — it adds the same
`SOURCE=local` build to every app service, so `up --build` builds the image from
your local checkouts and starts the stack against it. It takes the same
`.env` + `config/` the quickstart below needs:

```sh
cd compose
cp .env.example .env                 # fill POSTGRES_PASSWORD etc.
mkdir -p config && cp manifest.example.yml config/manifest.yml && touch config/.env
TAI_VERSION=local docker compose \
  -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

The `siblings` build context defaults to the directory holding those checkouts
(`../..`); override it with `TAI_SIBLINGS`. Because that context may hold
untracked secrets, run this on a LOCAL builder only — never CI or a shared
registry.

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

### Recycle and graceful shutdown

Process-identity settings (broker/bus URLs, config mode, worker counts) cannot
change in-process. A profile apply that touches one runs an orchestrated rolling
**recycle**: the process drains its current work, exits cleanly, and Compose's
`restart: unless-stopped` respawns it, which reloads the new env from `.env`. Two
things in `compose/docker-compose.yml` make that safe:

- **`TAI_SUPERVISED: compose`** in the shared `x-tai-app-env` anchor marks the
  supervision shape so the platform detects compose deterministically and refuses
  a recycle-class change to the anchor-pinned keys (they are re-injected from the
  compose file on respawn, so a profile-carried change would silently revert) —
  naming the manual step (edit the compose file, then recreate) instead of a
  silent no-op. The full pinned set is the `x-tai-app-env` anchor itself.
- **`stop_grace_period: 300s`** on `serve` and `backend` gives each `>=` its
  in-flight drain budget (uvicorn request drain; arq `job_completion_wait` /
  celery warm-drain) before Compose sends SIGKILL, so a recycle or a plain
  `docker compose stop` never severs live work.

## Self-hosting docs

Transport, environment, and config-provider guidance for running a server for
real lives in the [deploy guide](https://tai42.ai/guides/deploy). The chart's own
values, RBAC, and secret handling are documented in
[`charts/tai/README.md`](./charts/tai/README.md).

## License

Apache-2.0. See [LICENSE](./LICENSE).
