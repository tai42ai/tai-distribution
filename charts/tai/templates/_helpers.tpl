{{/*
Name helpers
*/}}
{{- define "tai.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tai.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "tai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels / selector labels
*/}}
{{- define "tai.labels" -}}
helm.sh/chart: {{ include "tai.chart" . }}
{{ include "tai.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "tai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
ServiceAccount name
*/}}
{{- define "tai.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tai.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image reference (tag defaults to appVersion)
*/}}
{{- define "tai.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
Resource names
*/}}
{{- define "tai.envSecretName" -}}
{{- if .Values.config.existingSecret -}}
{{- .Values.config.existingSecret -}}
{{- else -}}
{{- printf "%s-env" (include "tai.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "tai.manifestConfigMapName" -}}
{{- if .Values.config.existingConfigMap -}}
{{- .Values.config.existingConfigMap -}}
{{- else -}}
{{- printf "%s-manifest" (include "tai.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "tai.dbSecretName" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- else if .Values.postgresql.enabled -}}
{{- printf "%s-db" (include "tai.fullname" .) -}}
{{- else -}}
{{- required "postgresql.auth.existingSecret is required when postgresql.enabled=false and a Postgres-backed feature is enabled — the chart will not generate a password for a database it does not own" .Values.postgresql.auth.existingSecret -}}
{{- end -}}
{{- end -}}

{{- define "tai.dbSecretKey" -}}
{{- .Values.postgresql.auth.existingSecretKey | default "postgres-password" -}}
{{- end -}}

{{/*
Postgres host (quickstart service or external)
*/}}
{{- define "tai.postgres.host" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "%s-postgresql" (include "tai.fullname" .) -}}
{{- else -}}
{{- required "postgresql.host is required when postgresql.enabled is false" .Values.postgresql.host -}}
{{- end -}}
{{- end -}}

{{/*
Redis host (quickstart service or external)
*/}}
{{- define "tai.redis.host" -}}
{{- if .Values.redis.enabled -}}
{{- printf "%s-redis" (include "tai.fullname" .) -}}
{{- else -}}
{{- required "redis.host is required when redis.enabled is false" .Values.redis.host -}}
{{- end -}}
{{- end -}}

{{/*
Redis URL. When AUTH is enabled the password is NOT baked into the string:
instead the URL carries the shell-style reference `$(REDIS_PASSWORD)`, which
kubelet interpolates from the REDIS_PASSWORD env at container start — so the
plaintext password never lands in a rendered manifest. REDIS_PASSWORD is defined
at the top of tai.commonEnv, which every container consuming a redis URL includes
first (env vars must be declared before the vars that reference them). db index
appended.
*/}}
{{- define "tai.redis.url" -}}
{{- $auth := "" -}}
{{- if .Values.redis.auth.enabled -}}
{{- $auth = ":$(REDIS_PASSWORD)@" -}}
{{- end -}}
{{- printf "redis://%s%s:%v/%v" $auth (include "tai.redis.host" .) .Values.redis.port .Values.redis.database -}}
{{- end -}}

{{/*
Memory-redis host (the module-capable quickstart service for flow memory).
*/}}
{{- define "tai.memoryRedis.host" -}}
{{- printf "%s-memory-redis" (include "tai.fullname" .) -}}
{{- end -}}

{{/*
Whether the flow checkpoint/store conn strings are wired to a module-capable
redis: either the quickstart leg is on, or an external one is named via
memoryRedis.connString.
*/}}
{{- define "tai.memoryRedis.wired" -}}
{{- if or .Values.memoryRedis.enabled .Values.memoryRedis.connString -}}true{{- end -}}
{{- end -}}

{{/*
Flow checkpoint/store connection string. Explicit connString wins verbatim (the
operator owns the full URL, auth included). Otherwise derive from the quickstart
leg's own ClusterIP service; when memoryRedis.auth is on the derived URL carries
the shell-style reference `$(MEMORY_REDIS_PASSWORD)`, which kubelet interpolates
from the MEMORY_REDIS_PASSWORD env at container start — so the plaintext password
never lands in a rendered manifest. MEMORY_REDIS_PASSWORD is declared in
tai.commonEnv ahead of the conn strings that reference it. This leg's Secret is
SEPARATE from the core redis's — never shared.
*/}}
{{- define "tai.memoryRedis.connString" -}}
{{- if .Values.memoryRedis.connString -}}
{{- .Values.memoryRedis.connString -}}
{{- else -}}
{{- $auth := "" -}}
{{- if .Values.memoryRedis.auth.enabled -}}
{{- $auth = ":$(MEMORY_REDIS_PASSWORD)@" -}}
{{- end -}}
{{- printf "redis://%s%s:%v" $auth (include "tai.memoryRedis.host" .) .Values.memoryRedis.port -}}
{{- end -}}
{{- end -}}

{{/*
Memory-redis auth secret name — own Secret, never the core redis's.
*/}}
{{- define "tai.memoryRedisSecretName" -}}
{{- if .Values.memoryRedis.auth.existingSecret -}}
{{- .Values.memoryRedis.auth.existingSecret -}}
{{- else -}}
{{- printf "%s-memory-redis-auth" (include "tai.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Whether any Postgres-backed feature is enabled.
*/}}
{{- define "tai.anyPgFeature" -}}
{{- if or .Values.features.accessControl.enabled .Values.features.versioning.enabled .Values.features.accounts.enabled .Values.features.connectors.enabled .Values.features.marketplace.enabled -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether the schema-init hook should run.
*/}}
{{- define "tai.schemaInit.enabled" -}}
{{- $mode := .Values.schemaInit.enabled -}}
{{- if kindIs "bool" $mode -}}
{{- if $mode -}}true{{- end -}}
{{- else if eq (toString $mode) "auto" -}}
{{- include "tai.anyPgFeature" . -}}
{{- end -}}
{{- end -}}

{{/*
App environment shared by serve, backend, metrics and the schema-init hook.
Emits a YAML list of env entries. Pass the root context.
*/}}
{{- define "tai.commonEnv" -}}
{{/* Config mode */}}
- name: TAI_CONFIG_MODE
  value: {{ .Values.config.mode | quote }}
{{- if eq .Values.config.mode "k8s" }}
- name: TAI_K8S_SECRET_NAME
  value: {{ include "tai.envSecretName" . | quote }}
- name: TAI_K8S_CONFIGMAP_NAME
  value: {{ include "tai.manifestConfigMapName" . | quote }}
- name: TAI_K8S_MANIFEST_KEY
  value: "manifest.yml"
{{- else }}
- name: TAI_CONFIG_DIR_PATH
  value: "/app"
- name: TAI_MANIFEST_PATH
  value: "/app/manifest.yml"
{{- end }}
{{/* Redis AUTH password. Declared FIRST so kubelet can interpolate it into the
     $(REDIS_PASSWORD) placeholder every *_REDIS_URL below carries. No redis
     consumer exposes a separate password env — they are all URL-only — so the
     password rides the URL via kubelet dependent-env expansion. */}}
{{- if .Values.redis.auth.enabled }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tai.redisSecretName" . | quote }}
      key: {{ .Values.redis.auth.existingSecretKey | default "redis-password" | quote }}
{{- end }}
{{/* Worker bus — required for any task backend and for multi-worker serve. */}}
- name: TAI_BUS_REDIS_URL
  value: {{ include "tai.redis.url" . | quote }}
- name: TAI_BUS_NAMESPACE
  value: {{ .Values.redis.busNamespace | quote }}
{{/* Access control toggle. */}}
- name: ACCESS_CONTROL_ENABLE
  value: {{ .Values.features.accessControl.enabled | quote }}
{{/* Redis-backed feature URLs (wired to the chart redis endpoint). Connection
     identity has no baked-in default: an unset URL means the feature is cleanly
     OFF, not a silent localhost, so the chart wires every var explicitly to run
     these features against the chart's Redis. These surfaces are inert until
     used: their env presence alone opens no store at boot. */}}
{{- range list "ACCESS_CONTROL_REDIS_URL" "INTERACTIONS_REDIS_URL" "TAI_TOOL_RUNS_REDIS_URL" "TAI_RATE_LIMIT_REDIS_URL" "HOOKS_REDIS_URL" "CONNECTOR_STORE_REDIS_URL" }}
- name: {{ . }}
  value: {{ include "tai.redis.url" $ | quote }}
{{- end }}
{{/* LangGraph flow memory (checkpoint + store). Both default to the redis
     provider, which needs RediSearch/RedisJSON the plain core redis lacks; an
     unset conn string falls back to the base redis namespace (the core redis),
     where a flow run dies on FT._LIST. When the module-capable memory-redis leg
     is on (or an external one is named), point the checkpoint and store conn
     strings at it so redis-backed flow memory works out of the box. */}}
{{- if include "tai.memoryRedis.wired" . }}
{{/* Memory-redis AUTH password. Declared BEFORE the conn strings so kubelet can
     interpolate it into their $(MEMORY_REDIS_PASSWORD) placeholder. Only on the
     derived (quickstart) path — an explicit connString owns its own credentials.
     Its own Secret, never the core redis's. */}}
{{- if and .Values.memoryRedis.auth.enabled (not .Values.memoryRedis.connString) }}
- name: MEMORY_REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tai.memoryRedisSecretName" . | quote }}
      key: {{ .Values.memoryRedis.auth.existingSecretKey | default "memory-redis-password" | quote }}
{{- end }}
- name: LLM_PROVIDER_CHECKPOINT_CONN_STRING
  value: {{ include "tai.memoryRedis.connString" . | quote }}
- name: LLM_PROVIDER_STORE_CONN_STRING
  value: {{ include "tai.memoryRedis.connString" . | quote }}
{{- end }}
{{/* Postgres registry: the "default" database. Wired whenever any PG-backed
     feature is on (tai.anyPgFeature) — every postgres-backed component binds to
     "default", so one connection block serves them all. Inert until a loaded
     feature opens its store, so the Redis cache URLs above are safe to wire
     unconditionally. The admin (migrator) identity is unset, so it falls back to
     this runtime user/password — one identity for both. */}}
{{- if include "tai.anyPgFeature" . }}
- name: TAI_DATABASE_DEFAULT_PG_HOST
  value: {{ include "tai.postgres.host" . | quote }}
- name: TAI_DATABASE_DEFAULT_PG_PORT
  value: {{ .Values.postgresql.port | quote }}
- name: TAI_DATABASE_DEFAULT_PG_DB
  value: {{ .Values.postgresql.database | quote }}
- name: TAI_DATABASE_DEFAULT_PG_USER
  value: {{ .Values.postgresql.username | quote }}
- name: TAI_DATABASE_DEFAULT_PG_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tai.dbSecretName" . | quote }}
      key: {{ include "tai.dbSecretKey" . | quote }}
{{- end }}
{{- end -}}

{{/*
Redis auth secret name.
*/}}
{{- define "tai.redisSecretName" -}}
{{- if .Values.redis.auth.existingSecret -}}
{{- .Values.redis.auth.existingSecret -}}
{{- else if .Values.redis.enabled -}}
{{- printf "%s-redis-auth" (include "tai.fullname" .) -}}
{{- else -}}
{{- required "redis.auth.existingSecret is required when redis.enabled=false and redis.auth.enabled=true — the chart will not generate a password for a Redis it does not own" .Values.redis.auth.existingSecret -}}
{{- end -}}
{{- end -}}

{{/*
The backend_module string for the selected backend type.
*/}}
{{- define "tai.backendModule" -}}
{{- if eq .Values.backend.type "celery" -}}
tai42_backend_celery
{{- else if eq .Values.backend.type "arq" -}}
tai42_backend_arq
{{- else -}}
{{- fail (printf "backend.type must be 'arq' or 'celery', got %q" .Values.backend.type) -}}
{{- end -}}
{{- end -}}

{{/*
SUB_MCP_REDIS_URL env entry (serve only — shared sub-MCP routing store).
*/}}
{{- define "tai.subMcpEnv" -}}
- name: SUB_MCP_REDIS_URL
  value: {{ include "tai.redis.url" . | quote }}
{{- end -}}

{{/*
Backend-provider connection env (serve renders the backend module in its
manifest too, so both need the provider's connection).
*/}}
{{- define "tai.backendEnv" -}}
{{- if eq .Values.backend.type "celery" }}
- name: CELERY_BROKER_URL
  value: {{ required "backend.celery.brokerUrl is required for backend.type=celery" .Values.backend.celery.brokerUrl | quote }}
- name: CELERY_RESULT_BACKEND
  value: {{ .Values.backend.celery.resultBackend | default (include "tai.redis.url" .) | quote }}
{{/* RedBeat (celery-beat schedule store) rides the same Redis as the result
     backend; without it celery falls back to redis://localhost:6379/0 in-pod. */}}
- name: CELERY_REDBEAT_REDIS_URL
  value: {{ .Values.backend.celery.resultBackend | default (include "tai.redis.url" .) | quote }}
{{- else }}
- name: ARQ_REDIS_URL
  value: {{ .Values.backend.arq.redisUrl | default (include "tai.redis.url" .) | quote }}
{{- end }}
{{- end -}}

{{/*
Metrics sidecar env — only the shared multiproc dir. The bind host/port are
passed as explicit `--host/--port` args (one mechanism, the one the Prometheus
scraper hits) rather than BACKEND_METRICS_* env.
*/}}
{{- define "tai.metricsEnv" -}}
- name: PROMETHEUS_MULTIPROC_DIR
  value: {{ .Values.metrics.multiprocDir | quote }}
{{- end -}}

{{/*
Metrics sidecar container. Rendered as a NATIVE sidecar — an initContainer with
restartPolicy: Always. Deliberately carries NO readiness probe: a sidecar's
readiness rolls up into the POD's Ready condition, so probing metrics would let
an observability failure drop the app from its Service — the wrong coupling. A
crashed sidecar restarts via restartPolicy; a broken-but-running one surfaces as
up=0/5xx at the scraper, which is the correct signal. Binds explicitly via
--host/--port (0.0.0.0 so the port is reachable over the pod IP for scraping).
Requires Kubernetes >= 1.29 (native sidecars GA). Pass the root context; emit
under initContainers.
*/}}
{{- define "tai.metricsSidecar" -}}
- name: metrics
  image: {{ include "tai.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  restartPolicy: Always
  securityContext:
    {{- toYaml .Values.securityContext | nindent 4 }}
  args:
    - metrics
    - --host
    - "0.0.0.0"
    - --port
    - {{ .Values.metrics.port | quote }}
  ports:
    - name: metrics
      containerPort: {{ .Values.metrics.port }}
      protocol: TCP
  env:
    {{- include "tai.commonEnv" . | nindent 4 }}
    {{- include "tai.metricsEnv" . | nindent 4 }}
  resources:
    {{- toYaml .Values.metrics.resources | nindent 4 }}
  volumeMounts:
    {{- include "tai.containerVolumeMounts" . | nindent 4 }}
{{- end -}}

{{/*
Dependency-wait initContainer for the serve / backend pods.

Boot ordering fix: serve and backend do Redis (and, with a PG feature, Postgres)
work in their boot handlers with raise-on-error / no retry. If the pod starts
before its backing Service has endpoints, the handler dies and k8s restarts the
container 2-3x until DNS resolves — the "boot crash-loop" churn. This gate blocks
the main container until every dependency the app will actually open at boot is
TCP-reachable, so the app boots into a resolvable world on the first try.

CONFIG-SCENARIO-SAFE — the wait targets are derived from the SAME helpers the app
env is built from (tai.redis.host / tai.postgres.host + the ports), so quickstart
and external endpoints alike are honoured, and each dependency is gated by the
SAME conditional the chart uses to wire/own it:
  * Redis   : waited ONLY when redis.enabled (the quickstart Redis this chart
              owns and races). When redis.enabled=false the endpoint is external
              and assumed already up — we do NOT block on it (external-everything).
  * Memory  : the module-capable flow-memory leg, waited ONLY when
    Redis       memoryRedis.enabled (the quickstart StatefulSet this chart owns
                and races). An external memoryRedis.connString is operator-owned
                and assumed already up — we do NOT block on it.
  * Postgres: the app opens Postgres at boot ONLY when a PG-backed feature is on
              (see tai.commonEnv). So waited ONLY when postgresql.enabled AND a PG
              feature is enabled — never in the default quickstart (no PG feature)
              and never for an external Postgres. celery's external broker is not
              Redis/Postgres, so it is never waited on here.
If neither applies this renders NOTHING (no initContainer emitted).

Uses the app image (already loaded, python present) and a Python-stdlib socket
probe — no extra tool/image, restricted-PSA compliant via .Values.securityContext.
*/}}
{{- define "tai.dependencyWaitTargets" -}}
{{- $targets := list -}}
{{- if .Values.redis.enabled -}}
{{- $targets = append $targets (printf "%s:%v" (include "tai.redis.host" .) .Values.redis.port) -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (include "tai.anyPgFeature" .) -}}
{{- $targets = append $targets (printf "%s:%v" (include "tai.postgres.host" .) .Values.postgresql.port) -}}
{{- end -}}
{{- if .Values.memoryRedis.enabled -}}
{{- $targets = append $targets (printf "%s:%v" (include "tai.memoryRedis.host" .) .Values.memoryRedis.port) -}}
{{- end -}}
{{- $targets | toJson -}}
{{- end -}}

{{- define "tai.dependencyWait" -}}
{{- $targets := include "tai.dependencyWaitTargets" . | fromJsonArray -}}
{{- if $targets -}}
- name: wait-for-deps
  image: {{ include "tai.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  securityContext:
    {{- toYaml .Values.securityContext | nindent 4 }}
  command:
    - python
    - -c
    - |
      import socket, sys, time
      targets = {{ $targets | toJson }}
      deadline = time.monotonic() + 180
      for target in targets:
          host, _, port = target.rpartition(":")
          port = int(port)
          while True:
              try:
                  with socket.create_connection((host, port), timeout=3):
                      print(f"wait-for-deps: {host}:{port} reachable", flush=True)
                      break
              except OSError as exc:
                  if time.monotonic() >= deadline:
                      print(f"wait-for-deps: FATAL {host}:{port} unreachable after 180s: {exc}", file=sys.stderr, flush=True)
                      sys.exit(1)
                  print(f"wait-for-deps: waiting for {host}:{port} ({exc})", flush=True)
                  time.sleep(2)
{{- end -}}
{{- end -}}

{{/*
The multiproc dir env for the APP containers (so the process family writes its
counters to the same shared dir the sidecar reads).
*/}}
{{- define "tai.multiprocEnv" -}}
{{- if .Values.metrics.enabled }}
- name: PROMETHEUS_MULTIPROC_DIR
  value: {{ .Values.metrics.multiprocDir | quote }}
{{- end }}
{{- end -}}

{{/*
Plugin-prefix persistence (marketplace-install survival across pod recreation).

The claim name (existing or chart-created), the pod volume, the container mount,
and the TAI_PLUGINS_PREFIX env. The volume/mount/env are emitted onto the serve
and backend APP containers only — never the metrics sidecar or the schema-init
hook: neither loads plugins, and the hook mounts no prefix, so setting
TAI_PLUGINS_PREFIX there would make boot mkdir an unmounted, unwritable path.
*/}}
{{- define "tai.pluginPrefixClaimName" -}}
{{- if .Values.pluginPrefix.existingClaim -}}
{{- .Values.pluginPrefix.existingClaim -}}
{{- else -}}
{{- printf "%s-plugin-prefix" (include "tai.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "tai.pluginPrefixVolume" -}}
{{- if .Values.pluginPrefix.enabled }}
- name: plugin-prefix
  persistentVolumeClaim:
    claimName: {{ include "tai.pluginPrefixClaimName" . }}
{{- end }}
{{- end -}}

{{- define "tai.pluginPrefixVolumeMount" -}}
{{- if .Values.pluginPrefix.enabled }}
- name: plugin-prefix
  mountPath: {{ .Values.pluginPrefix.path | quote }}
{{- end }}
{{- end -}}

{{- define "tai.pluginPrefixEnv" -}}
{{- if .Values.pluginPrefix.enabled }}
- name: TAI_PLUGINS_PREFIX
  value: {{ .Values.pluginPrefix.path | quote }}
{{- end }}
{{- end -}}

{{/*
Pod volumes shared by serve / backend pods.
*/}}
{{- define "tai.podVolumes" -}}
{{- if eq .Values.config.mode "file" }}
- name: config-env
  secret:
    secretName: {{ include "tai.envSecretName" . }}
- name: config-manifest
  configMap:
    name: {{ include "tai.manifestConfigMapName" . }}
{{- end }}
{{- if .Values.metrics.enabled }}
- name: metrics-multiproc
  emptyDir: {}
{{- end }}
{{- end -}}

{{/*
Container volumeMounts shared by the app + metrics containers. In file mode the
env / manifest mount read-only as individual files via subPath so /app itself
(the image WORKDIR) stays writable for the config lock file and any runtime
writes.
*/}}
{{- define "tai.containerVolumeMounts" -}}
{{- if eq .Values.config.mode "file" }}
- name: config-env
  mountPath: /app/.env
  subPath: .env
  readOnly: true
- name: config-manifest
  mountPath: /app/manifest.yml
  subPath: manifest.yml
  readOnly: true
{{- end }}
{{- if .Values.metrics.enabled }}
{{/*
Mount the emptyDir at the PARENT of the multiproc dir, not the dir itself. The
process family writes a lock file as a sibling of the multiproc dir
(``<multiprocDir>.lock``) and creates the multiproc dir under it; both must land
on the writable emptyDir. Mounting the dir itself leaves the parent as the
root-owned image filesystem, so the sibling lock write is denied under the
non-root securityContext.
*/}}
- name: metrics-multiproc
  mountPath: {{ dir .Values.metrics.multiprocDir }}
{{- end }}
{{- end -}}

