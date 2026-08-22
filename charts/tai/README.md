# tai Helm chart

The application chart for TAI — one minimal-core image running the `serve`,
`backend worker`, and `metrics` processes, with optional first-party quickstart
Postgres and Redis. The image ships the platform core plus one reference provider
per infrastructure kind (local storage, the arq backend, redis identity, the k8s
config provider); every other plugin is added at runtime via the marketplace (see
[Plugin persistence](#plugin-persistence)) or baked into a derived image.
External-endpoints-first: point every dependency at your own managed Postgres /
Redis / Langfuse, or let the chart run the quickstart StatefulSets for a
self-contained trial.

## TL;DR

```bash
# Quickstart: app + arq backend + metrics + quickstart Postgres + Redis.
helm install tai oci://ghcr.io/tai42ai/charts/tai --version 0.2.0

# Verify.
helm test tai
```

For production, disable the quickstart DBs and point at your own endpoints:

```bash
helm install tai oci://ghcr.io/tai42ai/charts/tai \
  --set postgresql.enabled=false --set postgresql.host=postgres.internal \
  --set postgresql.auth.existingSecret=my-pg-secret \
  --set redis.enabled=false --set redis.host=redis.internal
```

## What the chart deploys

| Workload | Purpose |
| --- | --- |
| `serve` Deployment | the HTTP / MCP server (`tai serve`), plus the Studio SPA served same-origin |
| `backend` Deployment | the task worker (`tai backend worker`); default provider arq — the only backend in the minimal image, opt-in celery needs the plugin added (see below) |
| metrics sidecar | a `tai metrics` container in BOTH Deployments (see below) |
| plugin-prefix PVC | optional persistent volume for marketplace installs (`pluginPrefix.enabled`, see [Plugin persistence](#plugin-persistence)) |
| `postgresql` StatefulSet | optional quickstart Postgres (`postgres:16-alpine`) |
| `redis` StatefulSet | optional quickstart Redis (`redis:7-alpine`) — state, worker bus, feature stores |
| `memory-redis` StatefulSet | optional module-capable Redis (`redis:8`) for LangGraph flow memory ([Flow memory](#flow-memory-checkpoint--store)) |
| `sandbox-engine` Deployment | optional built-in rootless-dind engine + ClusterIP Service, mTLS cert Secrets, durable workspace PVC, egress NetworkPolicy, and in-pod egress firewall ConfigMap (`<engine>-firewall`) (`sandbox.enabled`, container/builtin mode; see [Sandbox](#sandbox-optional)) |

## Recorded design decisions

### Metrics runs as a sidecar, not a standalone Service

`tai metrics` renders the shared Prometheus multiprocess directory
(`PROMETHEUS_MULTIPROC_DIR`). That directory is shared **within a pod** between
the app process family and the metrics reader over an `emptyDir` — it cannot be
shared across pods. So the chart runs `tai metrics` as a **native sidecar in both
the serve and backend Deployments** — an `initContainer` with
`restartPolicy: Always` — each with its own `emptyDir`. A native sidecar keeps
the metrics reader's lifecycle tied to the pod while ensuring a metrics crash can
never drop the serve/backend app container out of `Ready` (it is not one of the
pod's regular containers). The sidecar binds explicitly with
`--host 0.0.0.0 --port <metrics.port>` (its default `127.0.0.1` is unreachable
via the pod IP) — one mechanism, and the one the Prometheus scraper hits at
`/metrics`. A ServiceMonitor (`metrics.serviceMonitor.enabled`) scrapes the
metrics port on both the serve Service and a headless backend Service.

Native sidecars require **Kubernetes >= 1.29** (the `SidecarContainers` feature,
GA in 1.29).

### Config is mounted read-only via subPath; `file` mode is static

In `file` mode (the default) the chart renders a Secret (`.env`) and a ConfigMap
(`manifest.yml`) and mounts them **read-only as individual files via `subPath`**
at `/app/.env` and `/app/manifest.yml`. The app pods also run with
`readOnlyRootFilesystem: true`, so the whole image filesystem — `/app` included —
is read-only; the app writes only to the explicit writable `emptyDir` mounts the
chart provides (`/tmp` and the Prometheus multiproc dir). Consequence: `file` mode
is **static config** — a `.lock`/atomic-rename write onto a `subPath`-mounted file
(or anywhere on the root filesystem) would fail loudly, which is correct; for
**dynamic multi-pod config** use `config.mode=k8s` (the `tai42-config-k8s` provider
reads/writes env + manifest via the Kubernetes API, and the chart grants the
ServiceAccount the matching RBAC).

### `config.mode=k8s` grants namespace-wide read of Secrets and ConfigMaps

Setting `config.mode=k8s` makes the chart create a namespace-scoped Role bound to
the release ServiceAccount. Read it before choosing this mode:

- `get` is pinned by name to exactly two objects — the chart's env Secret and its
  manifest ConfigMap.
- `patch`/`update` are pinned by name to the **env Secret only** — the write path
  the config provider uses to persist env (a profile apply / recycle env write).
  The manifest ConfigMap stays read-only here.
- `list` and `watch` **cannot** be pinned by name: Kubernetes RBAC has no name
  selector for collection verbs. So those two verbs are granted namespace-wide
  on **all** Secrets and **all** ConfigMaps in the release namespace.

The provider needs `list`/`watch` because `k8s` mode is the dynamic-config path:
it watches those two objects for changes and pushes them to every pod, which is
what makes multi-pod config reload work at all. The unavoidable cost is that a
pod running under this ServiceAccount can read every Secret in the namespace —
including the chart's own Postgres and Redis password Secrets, and any unrelated
Secret that happens to live there.

If that blast radius is not acceptable, keep the default `config.mode=file` and
give the release its own dedicated namespace when you do use `k8s` mode, so the
readable set is limited to Secrets you already trust the app with.

### Access control is OFF by default

`features.accessControl.enabled=false` so the quickstart boots with no identity
provider or key seeding. **Before exposing the server**, enable access control and
name an identity provider (e.g. `tai42_identity_redis.redis_api_key_provider`) in
`config.manifest` `lifecycle_modules`. The `/health` and `/ready` probes are served
public by default (no pin needed). See the [deploy guide](https://tai42.ai/guides/deploy).

### Recycle-ready: shape marker, backend readiness sentinel, grace, Reloader

Process-identity settings (broker/bus URLs, config mode, worker counts) cannot
change in-process; a profile apply that touches them runs an orchestrated rolling
**recycle** — the process finishes its current work, exits cleanly, and the
Deployment respawns it onto the new env. The chart carries what makes that safe on
Kubernetes:

- **Shape marker.** Every app container gets `TAI_SUPERVISED=k8s` (via the shared
  env helper), so the platform detects the k8s supervision shape deterministically
  and resolves the k8s refusal list — a recycle-class diff is never mistaken for
  an unsupervised (bare) host.
- **Backend readiness sentinel.** The backend worker has no HTTP listener, so its
  readiness is an **exec probe on a boot-ready sentinel file** the app writes when
  its boot latch flips (and removes at shutdown). `backend.readinessProbe.sentinelPath`
  is BOTH the `TAI_READY_SENTINEL_PATH` env (where the app writes it) and the
  probe's test path — one key, no drift. It MUST stay container-local/ephemeral
  (the default `/tmp/tai-ready`): a crash-restart must start **unready** until the
  fresh process re-writes it, so the rolling recycle waits for a genuinely ready
  replacement. serve keeps its HTTP `/ready` probe unchanged.
- **Termination grace.** `serve.terminationGracePeriodSeconds` and
  `backend.terminationGracePeriodSeconds` default to `300` — `>=` the worker's
  in-flight drain budget (arq `job_completion_wait` / celery warm-drain) — so a
  recycle SIGTERM lets in-flight work finish before SIGKILL.
- **Env-Secret RBAC.** In `config.mode=k8s` the config Role grants `patch`/`update`
  on the env Secret (see above) so the recycle env write is not a 403.
- **Reloader (optional, OFF).** `reloader.enabled=true` annotates the serve and
  backend Deployments for [Stakater Reloader](https://github.com/stakater/Reloader)
  so an **out-of-band** change to the env Secret / manifest ConfigMap (e.g. a
  k8s-mode profile apply writing them via the API) rolls the pods. Scoped to
  exactly the chart's two config objects, never a blanket `auto`. Requires the
  Reloader controller installed cluster-wide (not bundled).

## Plugin persistence

The image is **minimal-core** — the platform core plus one reference provider per
infrastructure kind (local storage, arq backend, redis identity, k8s config).
Every other plugin (channels, S3/GitHub storage, the celery/rq backends,
connectors, webhook verifiers, monitoring, accounts, extra tools) is **not** baked
in. Add the ones a deployment needs one of two ways:

- **Derived image** — bake a fixed roster into your own image
  (`FROM docker.io/tai42/tai`, then as `root` install the plugins into the venv —
  see the distribution README's derived-image recipe) and point `image.repository`
  / `image.tag` at it. Reproducible; needs no persistence below. This is the path
  for the `backend.type=celery` / `features.accounts` etc. blocks — those wire the
  provider's connection but do **not** ship the plugin.
- **Marketplace, at runtime** — install/update/uninstall plugins against a running
  server. A marketplace install `pip install`s the package **and** patches the
  manifest in place. Under the chart's read-only root filesystem the `pip install`
  can only write to a persistent, writable plugin prefix, so a runtime marketplace
  install **requires** `pluginPrefix.enabled=true`; without it the install targets
  the read-only image venv and **fails loudly** (no ephemeral fallback). Both
  halves must land on persistent storage:

  | Half | Where | How to persist |
  |---|---|---|
  | installed package files | plugin prefix (`TAI_PLUGINS_PREFIX`) | **required** — set `pluginPrefix.enabled=true`; the chart mounts a persistent, writable volume into the serve and backend pods and sets the env. Without it the `pip install` fails under the read-only root filesystem |
  | plugin registration | the manifest | use `config.mode=k8s` — the manifest lives in a ConfigMap patched via the Kubernetes API. The default `config.mode=file` mounts it **read-only** (static config); a runtime patch fails loudly there |

  `pluginPrefix.enabled=true` **with** `config.mode=k8s` is the combination that
  lets a runtime marketplace install both succeed and survive a container
  recreation. For a FIXED plugin roster, a derived image needs neither.

**Multi-pod.** The plugin prefix is one volume mounted read-write by every app pod
(serve + backend are separate Deployments — two pods even at one replica each), so
the default is **ReadWriteMany**; `ReadWriteOnce` is safe only when a single node
holds every mounting pod (backend disabled, or serve and backend co-scheduled).
Cross-pod pickup needs no restart: every marketplace install/update ends in a
fleet-wide reload broadcast on the worker bus, so running peers re-import the
changed plugins in place — a first-ever install included (the prefix site dirs
are on each pod's `sys.path` from boot even while empty, and every reload
invalidates the import caches). The one exception is **router** and **middleware**
plugins: their handlers and middleware stack are frozen into the ASGI app when the
process builds, so a router or middleware plugin — net-new or an upgrade of an
already-listed one — takes effect only when each pod restarts
(`kubectl rollout restart`).

## Sandbox (optional)

OFF by default (`sandbox.enabled=false`). The sandbox is a **scalar single-instance
holder**: a deployment installs EXACTLY ONE provider in its manifest
`sandbox_module`, and the two providers are **mutually exclusive** — set
`sandbox.provider` to match:

- **Container mode** (`provider: docker`, `sandbox_module: tai42_sandbox_docker`) —
  the agent code runs inside isolated per-session containers spawned by a Docker
  engine. `engine.mode`:
  - `builtin` (default when enabled) — the chart deploys a **rootless-dind engine**
    as a **separate** Deployment (never a sidecar; the app pods' hardened
    `securityContext` is untouched). The engine carries its own isolated privilege
    (`SYS_ADMIN`, unconfined seccomp) — the deliberate cost of an in-cluster sandbox;
    where a sandboxed RuntimeClass (**sysbox / gVisor**) is available, set
    `engine.runtimeClassName` and drop `SYS_ADMIN`. The chart also provisions the
    durable workspace **PVC** (mounted at the engine's data-root — rootless dind
    keeps its store under the rootless user's home, not `/var/lib/docker`), the
    **egress NetworkPolicy**, and the **mTLS certs**.
  - `external` — connect to an operator-run engine at `external.endpoint`
    (**required**); supply `external.clientCertSecret` (**required**, holding
    `cert.pem`/`key.pem`/`ca.pem`). The chart provisions no engine, PVC, certs, or
    NetworkPolicy; the operator owns the engine's durable storage and egress firewall.
- **Direct / host mode** (`provider: local`,
  `sandbox_module: tai42_sandbox_local`) — the agent code runs **on the serve /
  backend host** with **NO isolation** (the honest no-isolation posture). No engine,
  no certs, no session image. Workspaces live under `SANDBOX_LOCAL_ROOT`
  (`local.root`); for the `persistent` tier to survive a restart / node move, set
  `local.persistence.enabled=true` so a node-independent PVC mounts at that root in
  both app pods.

**Connection env.** When enabled the chart emits BOTH provider connection keys onto
serve + backend — `SANDBOX_DOCKER_HOST` (the built-in engine's ClusterIP service or
the external endpoint) and `SANDBOX_LOCAL_ROOT` (the host workspace root). Both
coexist in the pod env; the runtime `sandbox_module` selects which provider reads
its key, and the unselected key is unused. The mTLS client certs are a fixed
**volume mount** at `/certs/client` — never env — so no cert value lands in the
recycle-pinned pod env.

**mTLS trust (builtin).** The engine and app pods are separate pods and the engine
holds **no RBAC**, so the chart mints the mTLS trust at install time: one CA signs a
server bundle (the engine serves the control API with it) and a client bundle
(serve + backend present it). A `lookup` reuses the live Secrets across upgrades
(`helm.sh/resource-policy: keep`) so the certs are not rotated — the same
generated-credential keep pattern the Postgres/Redis passwords use. For GitOps
(`helm template | kubectl apply`, which cannot `lookup`), use an **external** engine
and supply `clientCertSecret`.

**Session isolation + egress.** An inner session is a container of the rootless-dind
daemon on dind's private inner bridge, NAT'd out through the engine pod's network
namespace and reachable to nothing else. `engine.internalCIDRs` is **required** in
builtin mode — the engine-pod egress NetworkPolicy allows the public internet
EXCEPT those cluster-internal ranges and cloud metadata (empty **fails the render**
rather than shipping an engine whose sessions can reach the whole internal network),
and allows DNS on `:53` to `engine.dnsCIDRs`. NetworkPolicy `ipBlock` enforcement is
CNI-dependent, so the engine pod also programs its own dind-netns egress firewall (a
chart ConfigMap script the engine container runs at daemon start) as the
CNI-independent backstop — both layers ship for builtin.

## Upgrading

Moving a release to a new chart/image version is three steps — the Helm
upgrade, then bringing the marketplace-installed plugins up to the new core
(release name `tai` shown; the serve Deployment is `<fullname>-serve`):

```bash
# 1. Move the release to the new version; the pods roll onto the new image.
helm upgrade tai oci://ghcr.io/tai42ai/charts/tai --version <new> --reuse-values

# 2. Move every marketplace-installed plugin to its newest compatible version.
kubectl exec deploy/tai-serve -- tai plugins upgrade --all

# 3. Verify: every row's compat verdict, and anything still quarantined.
kubectl exec deploy/tai-serve -- tai plugins installed
```

**Why the plugins survive the roll.** The new pods run the new image — a new
baked-in venv — but the plugin-prefix PVC (`pluginPrefix.enabled=true`)
outlives every pod, so the installed plugin code carries over, and under
`config.mode=k8s` each plugin's manifest registration lives in the ConfigMap,
which the upgrade does not rewrite. That is the same
[runtime-install-durable combination](#plugin-persistence) a runtime install
needs; without it there is nothing to carry over, and this section's step 2/3
apply only to plugins baked into a derived image (which the new image tag must
simply include again).

**Why step 2 exists.** The carried-over plugins were installed against the OLD
release's core, and the new image may ship a newer `tai42-contract`. A plugin
whose declared contract range excludes the running contract cannot be loaded —
and boot does not crash on it: the server **quarantines** it, starting without
it, serving everything else, and naming the plugin and the reason loudly in the
startup log and in the installed listing (`/api/marketplace/installed`).
`tai plugins upgrade --all` then moves every installed plugin onto its newest
version compatible with the running core, re-patching the manifest and
reloading as it goes, and reports one outcome per plugin (`upgraded` /
`up-to-date` / `no-compatible-version` / `failed`). Each such reload is
broadcast fleet-wide on the worker bus, so every serve and backend pod
re-imports the upgraded plugins in place — no pod restart is needed, the very
first install included (the prefix site dirs are on each pod's `sys.path` from
boot even while empty, and every reload invalidates the import caches). The one
exception is **router** and **middleware** plugins: their handlers and middleware
stack are frozen into the ASGI app when the process builds, so a router or
middleware plugin — net-new or an upgrade of an already-listed one — takes effect
only when each pod restarts (`kubectl rollout restart`).
Step 3's
`tai plugins installed` confirms the result: each row's compat verdict, update
availability, and any plugin still quarantined.

## Configuration

### Worker bus and multi-worker serve

The worker bus (`TAI_BUS_REDIS_URL`) is required whenever a task backend is
registered (the default) and whenever serve runs more than one worker. The chart
wires it — and `SUB_MCP_REDIS_URL` on serve — from the Redis endpoint
automatically. To scale serve in-process, set `serve.workers > 1` **and**
`serve.statelessHttp=true` (the stateful http transport refuses extra workers
otherwise).

### Flow memory (checkpoint + store)

Two Redis roles, two Redis instances. The core `redis` holds state, the worker
bus, and the feature stores — plain `redis:7-alpine`. LangGraph flow
checkpointing and the flow store default to the `redis` provider, which needs the
RediSearch + RedisJSON modules plain Redis lacks; a flow run against the core
Redis dies on `unknown command 'FT._LIST'`. The **memory Redis** leg is a
separate module-capable `redis:8` for flow memory only — never the core Redis's
endpoint, volume, or bus, and never its password Secret.

It is OFF by default. Set `memoryRedis.enabled=true` and the chart deploys the
StatefulSet **and** wires `LLM_PROVIDER_CHECKPOINT_CONN_STRING` and
`LLM_PROVIDER_STORE_CONN_STRING` to its service — redis-backed flow memory then
works out of the box:

```sh
helm install tai charts/tai --set memoryRedis.enabled=true
```

The image ships `LLM_PROVIDER_CHECKPOINT_TTL_MINUTES=43200`, a 30-day idle
retention on conversation checkpoints. Set it in the deployment env (`config.env`)
to override — a positive number of minutes, or empty to keep checkpoints forever.

**Auth.** Off by default (the Service is ClusterIP-only). Set
`memoryRedis.auth.enabled=true` to require a password — the StatefulSet gets
`--requirepass` (via a private config file, never argv) and the chart generates a
random password in its **own** Secret (`<release>-memory-redis-auth`, kept across
upgrades, never the core Redis's) and injects it into the derived conn strings via
kubelet `$(MEMORY_REDIS_PASSWORD)` expansion, so the plaintext never lands in a
rendered manifest:

```sh
helm install tai charts/tai --set memoryRedis.enabled=true --set memoryRedis.auth.enabled=true
```

Supply your own password with `memoryRedis.auth.existingSecret` (key
`memory-redis-password`) instead of the generated one — required for GitOps, where
`helm template | kubectl apply` cannot look up the live Secret. An `existingSecret`
password must be **URL-safe** (letters/digits/URL-unreserved only, no `:/?#[]@%`):
it is interpolated raw into the `redis://:<password>@host` URL without
percent-encoding.

To use an external module-capable Redis instead, set `memoryRedis.connString`
(e.g. `redis://redis-agents.internal:6379`, or `redis://:password@host:6379` with
auth) and leave `memoryRedis.enabled=false` — the conn strings point there and no
StatefulSet is deployed. An explicit `connString` always wins verbatim over the
derived quickstart address: you own the full URL, credentials included, and the
`memoryRedis.auth` block is not consulted.

### Postgres-backed features

Each is off by default; enabling any one wires the `TAI_DATABASE_DEFAULT_PG_*`
connection — the "default" registry database that every postgres-backed component
binds to — to the chart's Postgres endpoint (password via the DB Secret) and adds
its schema to the `tai db migrate` init hook (`schemaInit`). The migrator (admin)
identity is unset, so it falls back to the same runtime user/password.

The schema-init hook's phase depends on who owns the database:

- **External Postgres** (`postgresql.enabled=false`): `pre-install,pre-upgrade`.
  The DB pre-exists the release, so the pending migrations land **before** any
  serve/backend pod starts — strict ordering.
- **Quickstart Postgres** (`postgresql.enabled=true`): `post-install,post-upgrade`.
  The quickstart StatefulSet is a normal release resource, so it cannot exist
  during a pre-hook. The serve/backend pods may start before the migrations run
  and self-heal via crash-restart/startupProbe until they land; on upgrade the
  migrations land after the pods roll. This is acceptable because releases ship
  backward-compatible — new code tolerates the pre-migration schema until the
  chain lands. The StatefulSet is deliberately not made a hook itself — hook-owned
  workloads lose normal Helm lifecycle semantics.

All of these bind to the one `default` database (`TAI_DATABASE_DEFAULT_PG_*`);
enabling any of them brings that connection up.

| Value | Feature |
| --- | --- |
| `features.accessControl.enabled` | authn/authz policy store |
| `features.versioning.enabled` | presets + policy history |
| `features.accounts.enabled` | accounts (tai42-accounts-postgres — **not in the minimal image**; add via a derived image or marketplace install first) |
| `features.connectors.enabled` | OAuth connector store |
| `features.marketplace.enabled` | marketplace plugin store |

### Values

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` | `docker.io/tai42/tai` | Application image |
| `image.tag` | `""` (chart appVersion) | Image tag |
| `image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `serviceAccount.create` | `true` | Create a dedicated ServiceAccount |
| `serve.replicaCount` | `1` | serve pod replicas |
| `serve.workers` | `1` | in-process uvicorn workers (`--workers`) |
| `serve.statelessHttp` | `false` | `--stateless-http` (required for workers > 1) |
| `serve.transport` | `http` | serve transport |
| `serve.port` | `8000` | serve port — drives container port, all probes, and the Service targetPort |
| `serve.host` | `0.0.0.0` | serve bind address (must be 0.0.0.0 in-pod) |
| `serve.startupProbe.failureThreshold` | `60` | generous startup budget for manifest-driven dynamic imports |
| `serve.autoscaling.enabled` | `false` | HPA for serve |
| `serve.pdb.enabled` | `false` | PodDisruptionBudget for serve |
| `serve.terminationGracePeriodSeconds` | `300` | grace before SIGKILL on recycle/rollout (`>=` the serving drain budget) |
| `serve.resources` | requests 250m/512Mi | serve resources |
| `backend.enabled` | `true` | run the backend worker |
| `backend.type` | `arq` | backend provider: `arq` (only backend in the minimal image) or `celery` (needs the plugin added first — derived image or marketplace install; this block only wires the connection) |
| `backend.arq.redisUrl` | `""` (chart redis) | arq Redis URL |
| `backend.celery.brokerUrl` | placeholder | celery broker (bring your own) |
| `backend.livenessProbe.exec.command` | `/proc/1/cmdline` check | worker liveness (never HTTP) |
| `backend.readinessProbe.sentinelPath` | `/tmp/tai-ready` | boot-ready sentinel: BOTH `TAI_READY_SENTINEL_PATH` (where the app writes it) and the readiness exec-probe path — must be container-local/ephemeral |
| `backend.terminationGracePeriodSeconds` | `300` | grace before SIGKILL on recycle/rollout (`>=` the arq/celery in-flight drain budget) |
| `metrics.enabled` | `true` | run the metrics sidecar |
| `metrics.port` | `8012` | metrics sidecar port |
| `metrics.multiprocDir` | `/var/run/tai/prometheus` | shared multiproc dir (absolute) |
| `metrics.serviceMonitor.enabled` | `false` | prometheus-operator ServiceMonitor |
| `service.type` | `ClusterIP` | serve Service type |
| `service.port` | `80` | serve Service port |
| `ingress.enabled` | `false` | single-host Ingress (Studio same-origin) |
| `config.mode` | `file` | `file` (mounted Secret/ConfigMap) or `k8s` (API-read) |
| `config.env` | `{}` | env rendered into the env Secret |
| `config.manifest` | bare manifest | manifest.yml content (do not set `backend_module` — the chart injects it) |
| `config.existingSecret` | `""` | use an existing env Secret |
| `config.existingConfigMap` | `""` | use an existing manifest ConfigMap; **you** must set `backend_module` in it to match `backend.type` (the chart cannot inject into a manifest it does not render) |
| `reloader.enabled` | `false` | annotate serve/backend for Stakater Reloader (roll on out-of-band env Secret / manifest ConfigMap change); scoped to the two config objects; needs the Reloader controller installed ([Recycle-ready](#recycle-ready-shape-marker-backend-readiness-sentinel-grace-reloader)) |
| `pluginPrefix.enabled` | `false` | mount a persistent plugin prefix (`TAI_PLUGINS_PREFIX`) into serve + backend so marketplace installs survive pod recreation ([Plugin persistence](#plugin-persistence)) |
| `pluginPrefix.path` | `/var/lib/tai/plugins` | mount path and `TAI_PLUGINS_PREFIX` value |
| `pluginPrefix.existingClaim` | `""` | use an existing PVC instead of the chart-created one |
| `pluginPrefix.accessMode` / `.size` / `.storageClass` | `ReadWriteMany` / `1Gi` / `""` | chart-created PVC spec (`ReadWriteOnce` only when a single node holds every mounting pod) |
| `sandbox.enabled` | `false` | enable the code-execution sandbox; emits `SANDBOX_DOCKER_HOST` + `SANDBOX_LOCAL_ROOT` onto serve + backend ([Sandbox](#sandbox-optional)) |
| `sandbox.provider` | `docker` | `docker` (container) or `local` (direct/host) — must match the manifest `sandbox_module`; mutually exclusive |
| `sandbox.engine.mode` | `builtin` | `builtin` (chart deploys a rootless-dind engine) or `external` (connect to `external.endpoint`) |
| `sandbox.engine.image.*` | `docker:28-dind-rootless@sha256:…` | rootless-dind engine image (digest-pinned) |
| `sandbox.engine.podSecurityContext` / `.securityContext` | rootless-dind (`SYS_ADMIN`, unconfined seccomp) | engine-pod posture — isolated to the engine, never the app pods |
| `sandbox.engine.runtimeClassName` | `""` | optional sandboxed RuntimeClass (sysbox / gVisor); lets the operator drop `SYS_ADMIN` |
| `sandbox.engine.dataDir` | `/home/rootless/.local/share/docker` | daemon data-root the durable workspace PVC mounts at (rootless dind's store) |
| `sandbox.engine.persistence.*` | `10Gi` / `""` / `ReadWriteOnce` | durable workspace PVC (pick a node-independent StorageClass) |
| `sandbox.engine.internalCIDRs` | `[]` | **required** in builtin mode — cluster-internal CIDRs the engine-pod egress NetworkPolicy denies (empty fails the render) |
| `sandbox.engine.dnsCIDRs` | `[]` | cluster DNS CIDR(s) egress is allowed to on `:53` |
| `sandbox.external.endpoint` | `""` | `tcp://host:2376` — **required** when `engine.mode=external` |
| `sandbox.external.clientCertSecret` | `""` | Secret (`cert.pem`/`key.pem`/`ca.pem`) mounted at `/certs/client` — **required** when `engine.mode=external` |
| `sandbox.local.root` | `/var/lib/tai-sandbox-local` | `SANDBOX_LOCAL_ROOT` — direct/host workspace root |
| `sandbox.local.persistence.*` | `false` / `ReadWriteMany` / `10Gi` / `""` | persistent workspace-root PVC for direct/host mode (node-independent StorageClass) |
| `features.*.enabled` | `false` | Postgres-backed feature toggles (table above) |
| `schemaInit.enabled` | `auto` | `tai db migrate` hook: `auto`/`true`/`false`. Phase: pre-install/upgrade (external Postgres) or post-install/upgrade (quickstart) |
| `postgresql.enabled` | `true` | deploy the quickstart Postgres StatefulSet |
| `postgresql.host` / `.port` / `.database` / `.username` | — / 5432 / tai / tai | external Postgres connection |
| `postgresql.auth.existingSecret` | `""` | Secret (key `postgres-password`). Quickstart: else a random password is generated at install and kept. External Postgres (`enabled=false`) with a PG feature on: **required** (render fails otherwise) |
| `postgresql.securityContext.runAsUser` | `70` | postgres image uid (own context, not the app's) |
| `redis.enabled` | `true` | deploy the quickstart Redis StatefulSet |
| `redis.host` / `.port` / `.database` | — / 6379 / 0 | external Redis connection |
| `redis.auth.enabled` | `false` | Redis AUTH. Password injected into every `*_REDIS_URL` via kubelet `$(REDIS_PASSWORD)` at container start (never in a rendered manifest). Quickstart: generated + kept if no `existingSecret`; external Redis: `redis.auth.existingSecret` **required**. An `existingSecret` password must be **URL-safe** (letters/digits/URL-unreserved only, no `:/?#[]@%`) — it is interpolated raw into `redis://` URLs without percent-encoding |
| `redis.busNamespace` | `tai` | `TAI_BUS_NAMESPACE` (isolate co-tenant stacks) |
| `redis.securityContext.runAsUser` | `999` | redis image uid (own context) |
| `memoryRedis.enabled` | `false` | deploy the module-capable `redis:8` flow-memory StatefulSet and wire the flow checkpoint/store conn strings to it ([Flow memory](#flow-memory-checkpoint--store)) |
| `memoryRedis.connString` | `""` (derive) | flow checkpoint/store conn string. Empty = derive from the quickstart leg's service; set to point at an external module-capable Redis (then leave `enabled=false`, no StatefulSet). Wins **verbatim** over the derived address (you own the full URL, auth included; the `memoryRedis.auth` block is not consulted) |
| `memoryRedis.auth.enabled` | `false` | memory Redis AUTH (quickstart leg only). `--requirepass` on the StatefulSet; password injected into the derived conn strings via kubelet `$(MEMORY_REDIS_PASSWORD)` (never in a rendered manifest). Uses its **own** generated Secret — never the core Redis's; supply `memoryRedis.auth.existingSecret` (key `memory-redis-password`, must be **URL-safe**) for GitOps |
| `memoryRedis.port` | `6379` | memory Redis port |
| `memoryRedis.securityContext.runAsUser` | `999` | redis image uid (own context) |
| `podSecurityContext` / `securityContext` | restricted-PSA | app pod/container contexts |
| `tests.image.repository` | `curlimages/curl` | helm-test image (the app image has no curl) |

## Secrets and credentials

No working credential ships in `values.yaml`. For the **quickstart** Postgres —
the quickstart Redis when `redis.auth.enabled`, and the quickstart memory Redis
when `memoryRedis.auth.enabled` — the chart **generates a random password at
install** (a Secret annotated `helm.sh/resource-policy: keep`, with a `lookup` so
upgrades do not rotate it), and each such Secret feeds both its StatefulSet and
the app env so they cannot drift. The two Redis legs keep **separate** Secrets —
never shared. For an **external** Postgres (with a PG feature) or an **external**
Redis with auth, the chart will not generate a password for infrastructure it does
not own — supply `postgresql.auth.existingSecret` / `redis.auth.existingSecret`
(render fails otherwise); an external memory Redis carries its credentials in
`memoryRedis.connString`. Supply LLM keys, Langfuse keys, and external DB URLs via `config.env`
or, better, `config.existingSecret`. Every credential-bearing field in this chart
defaults to empty/placeholder — real secrets come via `existingSecret`.

**GitOps caveat.** The generated-password `lookup` needs a live cluster
connection. Under `helm template | kubectl apply` (Argo CD / Flux render) the
lookup returns nothing, so a fresh random password is minted on **every** render
and would overwrite the live one. For GitOps, always supply `existingSecret` for
Postgres and Redis rather than relying on chart generation.

## Restricted Pod Security Admission

Every pod carries a complete `restricted`-profile context (`runAsNonRoot`,
`runAsUser`, `allowPrivilegeEscalation: false`, `drop: [ALL]`,
`seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem: true`). The app pods
run as uid 1000; the quickstart DB pods run under their own image uids (postgres
70, redis 999) with `fsGroup` so the fresh PVC is writable and `PGDATA` at a
subdir of the mount. Under the read-only root filesystem every container writes
only to explicit writable `emptyDir` mounts: `/tmp` and the Prometheus multiproc
dir for the app pods, `/var/run/postgresql` (the Unix socket dir) for Postgres,
`/data` + `/etc/redis` for Redis. The `helm test` pod and the schema-init hook
carry the same restricted context.

`automountServiceAccountToken` is `false` on every pod that does not call the
Kubernetes API — the DB StatefulSets, the schema-init hook, the `helm test` pod,
and the serve/backend pods in the default `config.mode=file`. It is mounted on
the serve/backend pods only under `config.mode=k8s`, where the `tai42-config-k8s`
provider reads/watches the env Secret and manifest ConfigMap via the API (the
config Role is bound only then).

## Prerequisites (documented, not bundled)

The chart integrates with but never installs cluster addons: an Ingress
controller, cert-manager (for `ingress.tls`), the prometheus-operator (for
`metrics.serviceMonitor`), and a StorageClass (for persistence). Langfuse, when
used, is the upstream `langfuse/langfuse-k8s` chart wired via env — never a
subchart here.
