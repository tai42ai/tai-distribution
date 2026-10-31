# tai-distribution

[![CI](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml/badge.svg)](https://github.com/tai42ai/tai-distribution/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Client distribution for **tai**: the official container image, a Docker Compose
bundle, and the release CI that publishes them. One version story
pins every first-party `tai42-*` package.

## Image

`docker.io/tai42/tai` (primary) · `ghcr.io/tai42ai/tai` (mirror) — one
minimal-core, multi-arch (amd64 + arm64) image. It ships the platform core, the
agents layer, and the baked reference providers — local storage, the arq task
backend, redis identity, and both sandbox providers — and bakes the built
tai-studio SPA, so a single image runs every deployment role. What activates at
boot is decided by your manifest and environment.

Every other plugin (the channels, the S3/GitHub storages, the celery/rq
backends, the connectors, the webhook verifiers, monitoring, accounts, and the
extra tools) is **not** baked in. Add the ones you need at runtime through the
[marketplace](#adding-plugins) or bake them into a
[derived image](#adding-plugins).

### One image, three commands

The entrypoint is `tai`, run under the `tini` init so the container reaps the
processes an exec leaves behind and forwards signals to `tai`; the default
command is `serve`.

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

### Labels

Besides the `org.opencontainers.image.*` labels, every platform image the
release pipeline publishes carries `ai.tai42.e2e.ref`: the
`tai42-e2e-v<X>.<Y>.<Z>` release tag of the end-to-end suite that ran and passed
against that image before it was published. The pipeline sets it on every arch,
for stable and prerelease releases alike; the release build refuses to start
without it. Images published before the pipeline set this label carry none.
Read it from a local image or from the registry:

```sh
docker image inspect --format '{{ index .Config.Labels "ai.tai42.e2e.ref" }}' docker.io/tai42/tai:latest
docker buildx imagetools inspect docker.io/tai42/tai:latest --format '{{json .Image}}'
```

"Passed" means: the image booted through this repository's Compose bundle with
the auth gate on, the owner key was minted through the setup door, and the
suite at that tag ran in target mode against it — every test the target can
serve ran, none failed, and at least one ran. Tests the image cannot serve as
shipped — plugin kinds the minimal core does not bake, the suite's probe tools,
or control of a stack the suite builds itself — are skipped there with their
unmet need as the reason; the run's junit report is attached to the workflow
run.

### Config

`/app` is the config dir (and `WORKDIR`): mount your `.env` and `manifest.yml`
there. `TAI_CONFIG_DIR_PATH` moves the dir; `TAI_MANIFEST_PATH` moves the
manifest file alone. The serve port defaults to `8000` (`--port`, or the
`APP_ARGS_PORT` env var); the image `HEALTHCHECK` probes `:8000/health`.
The worker serves no HTTP, so a `tai backend worker` container overrides that
check with the `tai42-backend-probe` exec check, which reads the worker's
liveness file (`TAI_BACKEND_LIVENESS_PATH`) from the container environment.

### First run

Turn the auth gate on (`ACCESS_CONTROL_ENABLE=true` in `.env`) and name an
identity provider in your `manifest.yml` (see [Config](#config) and the
commented block in `manifest.example.yml`), then initialize the deployment once
to create the owner and mint the owner's first key:

```sh
cd compose
docker compose exec serve tai setup
```

`tai setup` drives the public `POST /api/setup` door: it creates the owner
principal and prints the owner's key **once** — copy it then, it is not shown
again. Gate the door with a token: set `TAI_SETUP_TOKEN` in `config/.env` to pin
your own, or leave it unset and the server mints a one-time token and logs it once
at startup (read it from the `serve` log). Setup is one-shot — once the owner
principal exists the door answers `409` and mints nothing more, and every later
key is minted by the owner.

If the identity store is ever lost with no backup export, re-mint one surviving
owner key on the deployment host with `docker compose exec serve tai setup
--recover`; the [backup and restore
guide](https://tai42.ai/operate/backup-and-restore) carries the detail.

### Adding plugins

The image is the minimal core. Add the plugins a deployment needs one of two
ways:

**Runtime, via the marketplace.** Install, update, and uninstall plugins against
a running server through the marketplace API/Studio — no rebuild. A
marketplace install `pip install`s the package into the directory named by
`TAI_PLUGINS_PREFIX` and
patches the manifest in place, so the plugin is live on the next config reload
with no restart — including a first-ever install (the plugin prefix is on
`sys.path` from boot even while empty, and each reload invalidates the import
caches). The exception is **router** and **middleware** plugins: their handlers
and middleware stack are frozen into the ASGI app when the process builds, so a
router or middleware plugin — net-new or an upgrade of an already-listed one —
takes effect only on the next process restart. A marketplace install writes to
the directory `TAI_PLUGINS_PREFIX` names: the image runs as a non-root user that
cannot write its own venv, so the prefix must be a writable directory. The image
bakes `TAI_PLUGINS_PREFIX=/app/plugins` — a writable, tai-owned directory it
creates — so a deployment installs plugins out of the box; the compose bundle
mounts the `tai-plugins-data` named volume there (see
[Persistence](#persistence)), so the installs also **survive a container
recreation**. An operator may override the prefix to another writable, mounted
directory.

**Build time, via a derived image.** Bake extra plugins into your own image on
top of this one — the reproducible path for a fixed roster:

```dockerfile
FROM docker.io/tai42/tai:latest
# The base image ends on USER tai over a root-owned venv, so become root to
# install, then drop back to the non-root runtime user. Use `python -m pip`
# (the venv interpreter is first on PATH; a bare `pip` would be the base
# image's system pip and install outside the venv, where the server never
# looks). The base image sets PIP_CONSTRAINT/UV_CONSTRAINT to its own
# contract/kit/skeleton/cli versions, so this install is held to them: an
# incompatible plugin is refused loudly by the resolver, never installed by
# downgrading or upgrading the core.
USER root
RUN python -m pip install tai42-channel-slack tai42-storage-s3
USER tai
```

The base image sets `PIP_CONSTRAINT`/`UV_CONSTRAINT` to a constraints file of its
own first-party core versions, so any `python -m pip install` — here, or run by hand in a
container — that would move `tai42-contract`, `tai42-kit`, `tai42-skeleton`,
`tai42-cli`, or any other `tai42-*` package the image ships off the image's version is
refused by the resolver rather than silently downgrading or upgrading the core and breaking
the server. The constraints file pins the first-party `tai42-*` core only, not
the third-party packages the image ships, so a plugin's dependency closure must
still accept the third-party versions the image already holds. After a manual or
derived-image install, run `python -m pip check` to confirm the environment is
consistent.

Then reference the added plugins from your `manifest.yml`.

### Persistence

A real deployment gives the server three writable, persistent mounts, each
located by an env var the skeleton reads:

| What | Env var | Default | Why it must persist |
|---|---|---|---|
| Plugin prefix directory | `TAI_PLUGINS_PREFIX` | set by the image to `/app/plugins` (a writable, tai-owned directory the image creates) | Where marketplace installs land; the non-root runtime user cannot write the image's venv, so this points at a writable mounted directory, and on a persistent disk the installs survive container recreation. The compose bundle mounts the `tai-plugins-data` named volume there. |
| Manifest file | `TAI_MANIFEST_PATH` (or `TAI_CONFIG_DIR_PATH` for the whole config dir) | `/app/manifest.yml` | Marketplace installs patch the manifest; losing it drops every runtime-added plugin's registration. |
| Local storage root | `STORAGE_LOCAL_ROOT_PATH` | set by the image to `/app/templates` (required: the provider refuses every call while it is unset) | Where the baked local storage provider keeps stored files; the compose bundle mounts the `tai-storage-local-data` named volume there. |

The prefix and the manifest work together: the prefix keeps the installed
**code**, the manifest keeps its **registration**. Persist only one and a
recreated container boots with the halves out of sync; drop the storage root and
it loses its stored files.

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
| `docker.io/tai42/tai-sandbox-claude-code` (mirror `ghcr.io/tai42ai/tai-sandbox-claude-code`) | `claude-agent-sdk` + its bundled `claude` CLI (no runner code, no credentials) | `TAI_AGENTS_CLAUDE_SESSION_IMAGE` |
| `docker.io/tai42/tai-sandbox-exec` (mirror `ghcr.io/tai42ai/tai-sandbox-exec`) | lean `sh` + `python3` + coreutils only (no SDK, no runner code, no credentials) | the `langchain_deep_agent` group's own `session_image` setting |

The release lane also publishes both session images to Oracle Cloud's registry
(OCIR) when the repository sets the Actions variable `OCIR_IMAGE_PREFIX`
(`<region>.ocir.io/<namespace>/<path>`) and the secrets `OCI_NAMESPACE`,
`OCI_USERNAME` and `OCI_AUTH_TOKEN`: the same tags, signed the same way, under
`<OCIR_IMAGE_PREFIX>/tai-sandbox-claude-code` and
`<OCIR_IMAGE_PREFIX>/tai-sandbox-exec`.

Both are **consumed by digest, never by a bare tag** — every reference is the
`…@sha256:…` form, and the intended pull posture is cosign signature
verification:

```sh
# In config/.env (or saved from the admin UI) — pin the exact digest, not a moving tag:
TAI_AGENTS_CLAUDE_SESSION_IMAGE=docker.io/tai42/tai-sandbox-claude-code@sha256:<digest>
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

# 3. Verify: every row's compat verdict and update availability.
docker compose exec serve tai plugins installed
```

**Why the plugins survive the image swap.** A new tag replaces the container
and, with it, the image's baked-in venv — but `TAI_PLUGINS_PREFIX` points at a
mounted persistent directory outside the image, and the manifest holding each
plugin's registration is likewise mounted (see [Persistence](#persistence)).
Both halves of every marketplace install therefore carry over unchanged into
the new container.

**Why step 2 exists.** What carries over was installed against the OLD
release's core, and the new release may ship a newer `tai42-contract`. A
carried-over plugin whose declared contract range excludes the running contract
cannot load, and because the manifest names it the new container **refuses to
boot**: the process exits naming the module, its kind and the reason, rather
than coming up without a plugin the manifest asked for. Fix or update the
plugin, point the manifest at a working one, or remove it from the manifest,
then boot again. `tai plugins upgrade --all` keeps the installed set current so
this does not arise: it moves every installed plugin onto its newest version
compatible with the running core, re-patching the manifest and
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
`tai plugins installed` confirms the result: each row's compat verdict and
update availability.

## Building locally

Release builds pull `tai42-*` from PyPI and fetch tai-studio at the commit SHA
recorded in `docker/STUDIO_REF`, and take the `E2E_REF` build-arg — the
tai42-e2e suite tag in `docker/E2E_REF`, which the image records as its
`ai.tai42.e2e.ref` label (see [Labels](#labels)); a release build refuses an
empty one. A dev build runs no suite, so its label is empty. It instead uses
your local checkouts:
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

The release build runs only when the pinned `tai42-contract` version lies inside
the contract range the pinned tai-studio commit declares.
`scripts/check_image_alignment.py` gates the release workflow on it: it reads the
`tai42-contract` pin from `docker/pypi-requirements.txt` and the supported
contract range the studio commit in `docker/STUDIO_REF` declares (its
`_SPEC["contract"]` marketplace-registry entry), and fails the release loudly —
naming the version, the range and the commit, no image published — when the pin
falls outside the range. CI runs the same check on every pull request, so a pin
and a studio ref outside each other's range never reach `main`. Both inputs are
kept current by `.github/workflows/refresh-image-inputs.yml`: every platform
release and every studio release recomputes the pins and the studio ref together
from the newest release tags of both repositories
(`scripts/refresh_image_inputs.py`) and proposes them as one pull request, so a
platform release and the studio release that supports it merge as one change.
The same refresh keeps `docker/E2E_REF` on the newest stable tai42-e2e release
tag, and the alignment check also fails when that tag is not one the monorepo
serves.

Before a release pushes an arch of the platform image, the e2e gate
(`.github/actions/e2e-gate`) runs the suite the image's label names against the
scanned image on that arch's native runner; a failed test, or a run in which no
test ran (`scripts/check_e2e_gate_report.py`), stops the push. CI runs the same
gate on the amd64 smoke image of every pull request that builds one (a pull
request that only bumps versions builds no image).

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
docker/E2E_REF         tai42-e2e suite tag the e2e gate runs; the image's ai.tai42.e2e.ref label
compose/               Docker Compose bundle (core stack + profiles)
compose/langfuse/      standalone Langfuse 4 stack (optional monitoring)
.github/actions/       the e2e gate (composite action) the release and CI run
.github/workflows/     release + CI
```

The Compose bundle needs Docker Compose 2.20 or newer — the first release with the `required: false` dependency field the stack uses.

Compose quickstart: in `compose/`, copy `.env.example` to `.env` and fill the
required values (the stack refuses to start while any required secret is
unset); create the config dir — `mkdir -p config && cp manifest.example.yml
config/manifest.yml && touch config/.env`; then `docker compose up -d`. The app
writes the settings it saves back into `config/`, so at every `up` the one-shot
`config-init` hands that directory to the image's user (uid 1000) before serve,
backend and metrics start; reading or editing those files by hand on the host
afterwards takes that uid. Optional
profiles: `--profile celery` (RabbitMQ broker), `--profile minio` (S3
storage), `--profile monitoring`
(the OpenTelemetry collector serve and backend export monitoring records to). The `celery` and
`minio` profiles drive the celery backend and S3 storage plugins, which are not
in the minimal image — add them via a derived image or a marketplace install
first. Langfuse 4 runs as its own stack from `compose/langfuse/` and likewise
needs the monitoring plugin added. Its `langfuse-web` joins the core stack's
`<project>_monitoring` network (`tai_monitoring` by default; `TAI_CORE_PROJECT` in
`compose/langfuse/.env` names the core project), so bring the core stack up first;
serve, backend and the `monitoring` profile's collector then reach it at
`http://langfuse-web:3000`. The network is scoped to one core stack: with several on
one host, run one Langfuse stack per core stack, each under its own `-p` and
`LANGFUSE_WEB_PORT`. Set that address as `LANGFUSE_HOST` twice: in `config/.env`
with `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (the plugin's reads), and in
`.env` with `OTEL_COLLECTOR_LANGFUSE_AUTH` (the collector's sends). The core stack
owns the network: if it was taken `down` and up again while `langfuse-web` was
stopped, that container refuses to start (`network … not found`); bring it back with
`docker compose -f compose/langfuse/docker-compose.yml up -d --force-recreate langfuse-web`.
Moving from a Langfuse 3.x stack: stop it and remove its volumes
(`docker compose -f compose/langfuse/docker-compose.yml down --volumes`), then start
this one. Nothing recorded under 3.x is carried over.

### Service healthchecks

Each long-running app service in `compose/docker-compose.yml` carries its own
healthcheck in place of the image's `:8000/health` one: `serve` probes
`:8000/health`, `metrics` probes `:8012/metrics`, and `backend` runs
`tai42-backend-probe`, which passes while the worker loop's liveness file
promises a pass that is not overdue past `TAI_BACKEND_LIVENESS_GRACE`. The probe
reads the container environment only, so a `TAI_BACKEND_LIVENESS_*` setting goes
in the `backend` service's `environment:`, never in `config/.env`. The health
state drives `depends_on: condition: service_healthy`, `docker compose up --wait`
and `docker compose ps`; Compose does not restart an unhealthy container.

### Deployment settings and admin settings

A setting whose value is in the container environment when the app starts
belongs to the deployment. On boot and on every reload that value wins over any
copy the app has stored, the admin UI shows the setting locked, and the app
refuses a save that sets it. In this bundle that covers every key of the shared
`x-tai-app-env` anchor in `compose/docker-compose.yml` and of each app service's
own `environment:` entries that is set to a non-empty value, and the ENV the
image sets; a key the compose file leaves empty (such as an unset
`${SANDBOX_DOCKER_HOST:-}`) is not the deployment's. A key set in only one
service's `environment:` is still the deployment's for every service: no service
applies a stored copy of it, and a service the compose file does not give the
value runs its default. Change one by editing the compose file (or the `.env` it
substitutes from) and recreating the containers.

Every other setting is the admin's: the app stores it in `config/.env`, and the
admin changes it through the app (the UI or the API). A setting an admin should
edit from the UI must therefore not be set in the compose file.

### Recycle and graceful shutdown

Some settings cannot change in-process: each declares that a change needs a
fresh process (its `recycle` reload class, for example
`TAI_AGENTS_CLAUDE_CRASH_RESUME` or a plugin field declared `"reload": "recycle"`).
The bus URLs and the config mode are never saved through the app at all: in this
bundle the compose file sets them, so they change only by editing it and
recreating the containers. A saved change that touches a recycle-class setting
runs an orchestrated rolling **recycle**: the process drains its current work
and exits cleanly, and its supervisor starts it again, which loads the new
settings. The app needs a supervisor that restarts it on exit; without one, a
recycled process stays stopped and the recycle reports it as stopped. Two
things in `compose/docker-compose.yml` make that safe:

- **`restart: unless-stopped`** on `serve`, `backend` and `metrics` is the
  supervisor: Compose starts each process again when it exits.
- **`stop_grace_period: 300s`** on `serve` and `backend` gives each `>=` its
  in-flight drain budget (uvicorn request drain; arq `job_completion_wait` /
  celery warm-drain) before Compose sends SIGKILL, so a recycle or a plain
  `docker compose stop` never severs live work.

## Self-hosting docs

Transport, environment, and config-provider guidance for running a server for
real lives in the [deploy guide](https://tai42.ai/guides/deploy).

## License

Apache-2.0. See [LICENSE](./LICENSE).
