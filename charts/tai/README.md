# tai Helm chart

The application chart for TAI — one batteries-included image running the
`serve`, `backend worker`, and `metrics` processes, with optional first-party
quickstart Postgres and Redis. External-endpoints-first: point every dependency
at your own managed Postgres / Redis / Langfuse, or let the chart run the
quickstart StatefulSets for a self-contained trial.

## TL;DR

```bash
# Quickstart: app + arq backend + metrics + quickstart Postgres + Redis.
helm install tai oci://ghcr.io/tai42ai/charts/tai --version 0.1.0

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
| `backend` Deployment | the task worker (`tai backend worker`); default provider arq, opt-in celery |
| metrics sidecar | a `tai metrics` container in BOTH Deployments (see below) |
| `postgresql` StatefulSet | optional quickstart Postgres (`postgres:16-alpine`) |
| `redis` StatefulSet | optional quickstart Redis (`redis:7-alpine`) |

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

### Config is mounted read-only via subPath; `/app` stays writable

In `file` mode (the default) the chart renders a Secret (`.env`) and a ConfigMap
(`manifest.yml`) and mounts them **read-only as individual files via `subPath`**
at `/app/.env` and `/app/manifest.yml`. The app's file config provider writes
`.env` / `manifest.yml` and a `.lock` file under the config dir on config
mutations, and `/app` is also the image WORKDIR — so the chart does NOT shadow
`/app` with a whole-directory read-only mount. `subPath` keeps only those two
files read-only while `/app` itself remains writable. Consequence: `file` mode is
**static config** — a `.lock`/atomic-rename write onto a `subPath`-mounted file
would fail loudly, which is correct; for **dynamic multi-pod config** use
`config.mode=k8s` (the `tai42-config-k8s` provider reads/writes env + manifest via
the Kubernetes API, and the chart grants the ServiceAccount the matching RBAC).

### `config.mode=k8s` grants namespace-wide read of Secrets and ConfigMaps

Setting `config.mode=k8s` makes the chart create a namespace-scoped Role bound to
the release ServiceAccount. Read it before choosing this mode:

- `get` is pinned by name to exactly two objects — the chart's env Secret and its
  manifest ConfigMap.
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
provider or key seeding. **Before exposing the server**, enable access control,
name an identity provider (e.g. `tai42_identity_redis.redis_api_key_provider`) in
`config.manifest` `lifecycle_modules`, and public-pin `/health` and `/ready`.
See the [deploy guide](https://tai42.ai/guides/deploy).

## Configuration

### Worker bus and multi-worker serve

The worker bus (`TAI_BUS_REDIS_URL`) is required whenever a task backend is
registered (the default) and whenever serve runs more than one worker. The chart
wires it — and `SUB_MCP_REDIS_URL` on serve — from the Redis endpoint
automatically. To scale serve in-process, set `serve.workers > 1` **and**
`serve.statelessHttp=true` (the stateful http transport refuses extra workers
otherwise).

### Postgres-backed features

Each is off by default; enabling one wires its `<PREFIX>PG_*` connection to the
chart's Postgres endpoint (password via the DB Secret) and adds its schema to the
`tai db apply` init hook (`schemaInit`).

The schema-init hook's phase depends on who owns the database:

- **External Postgres** (`postgresql.enabled=false`): `pre-install,pre-upgrade`.
  The DB pre-exists the release, so the schema (and each upgrade's migration)
  lands **before** any serve/backend pod starts — strict ordering.
- **Quickstart Postgres** (`postgresql.enabled=true`): `post-install,post-upgrade`.
  The quickstart StatefulSet is a normal release resource, so it cannot exist
  during a pre-hook. The serve/backend pods may start before the schema exists
  and self-heal via crash-restart/startupProbe until it lands; on upgrade the
  migration lands after the pods roll. This is acceptable because the DDL is
  additive `IF NOT EXISTS` and releases ship schema-compatible. The StatefulSet
  is deliberately not made a hook itself — hook-owned workloads lose normal Helm
  lifecycle semantics.

| Value | Feature | Env prefix |
| --- | --- | --- |
| `features.accessControl.enabled` | authn/authz policy store | `ACCESS_CONTROL_STORE_` |
| `features.versioning.enabled` | presets + policy history | `VERSIONING_STORE_` |
| `features.accounts.enabled` | accounts (tai42-accounts-postgres) | `TAI_ACCOUNTS_PG_` (rendered vars double the PG: `TAI_ACCOUNTS_PG_PG_HOST` … — the names the plugin's settings read) |
| `features.connectors.enabled` | OAuth connector store | `CONNECTOR_STORE_` |
| `features.marketplace.enabled` | marketplace plugin store | `MARKETPLACE_STORE_` |

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
| `serve.resources` | requests 250m/512Mi | serve resources |
| `backend.enabled` | `true` | run the backend worker |
| `backend.type` | `arq` | backend provider: `arq` or `celery` |
| `backend.arq.redisUrl` | `""` (chart redis) | arq Redis URL |
| `backend.celery.brokerUrl` | placeholder | celery broker (bring your own) |
| `backend.livenessProbe.exec.command` | `/proc/1/cmdline` check | worker liveness (never HTTP) |
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
| `features.*.enabled` | `false` | Postgres-backed feature toggles (table above) |
| `schemaInit.enabled` | `auto` | `tai db apply` hook: `auto`/`true`/`false`. Phase: pre-install/upgrade (external Postgres) or post-install/upgrade (quickstart) |
| `postgresql.enabled` | `true` | deploy the quickstart Postgres StatefulSet |
| `postgresql.host` / `.port` / `.database` / `.username` | — / 5432 / tai / tai | external Postgres connection |
| `postgresql.auth.existingSecret` | `""` | Secret (key `postgres-password`). Quickstart: else a random password is generated at install and kept. External Postgres (`enabled=false`) with a PG feature on: **required** (render fails otherwise) |
| `postgresql.securityContext.runAsUser` | `70` | postgres image uid (own context, not the app's) |
| `redis.enabled` | `true` | deploy the quickstart Redis StatefulSet |
| `redis.host` / `.port` / `.database` | — / 6379 / 0 | external Redis connection |
| `redis.auth.enabled` | `false` | Redis AUTH. Password injected into every `*_REDIS_URL` via kubelet `$(REDIS_PASSWORD)` at container start (never in a rendered manifest). Quickstart: generated + kept if no `existingSecret`; external Redis: `redis.auth.existingSecret` **required**. An `existingSecret` password must be **URL-safe** (letters/digits/URL-unreserved only, no `:/?#[]@%`) — it is interpolated raw into `redis://` URLs without percent-encoding |
| `redis.busNamespace` | `tai` | `TAI_BUS_NAMESPACE` (isolate co-tenant stacks) |
| `redis.securityContext.runAsUser` | `999` | redis image uid (own context) |
| `podSecurityContext` / `securityContext` | restricted-PSA | app pod/container contexts |
| `tests.image.repository` | `curlimages/curl` | helm-test image (the app image has no curl) |

## Secrets and credentials

No working credential ships in `values.yaml`. For the **quickstart** Postgres —
and the quickstart Redis when `redis.auth.enabled` — the chart **generates a
random password at install** (a Secret annotated `helm.sh/resource-policy: keep`,
with a `lookup` so upgrades do not rotate it), and that one Secret feeds both the
DB StatefulSet and the app env so they cannot drift. For an **external** Postgres
(with a PG feature) or an **external** Redis with auth, the chart will not
generate a password for infrastructure it does not own — supply
`postgresql.auth.existingSecret` / `redis.auth.existingSecret` (render fails
otherwise). Supply LLM keys, Langfuse keys, and external DB URLs via `config.env`
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
`seccompProfile: RuntimeDefault`). The app pods run as uid 1000; the quickstart
DB pods run under their own image uids (postgres 70, redis 999) with `fsGroup`
so the fresh PVC is writable and `PGDATA` at a subdir of the mount. The
`helm test` pod and the schema-init hook carry the same restricted context.

## Prerequisites (documented, not bundled)

The chart integrates with but never installs cluster addons: an Ingress
controller, cert-manager (for `ingress.tls`), the prometheus-operator (for
`metrics.serviceMonitor`), and a StorageClass (for persistence). Langfuse, when
used, is the upstream `langfuse/langfuse-k8s` chart wired via env — never a
subchart here.
