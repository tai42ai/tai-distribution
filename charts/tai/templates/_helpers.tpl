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
The map of enabled Postgres-feature env-var prefixes. Each prefix P wires
P_PG_HOST/PORT/DB/USER (plain) + P_PG_PASSWORD (from the db secret).
*/}}
{{- define "tai.pgFeaturePrefixes" -}}
{{- $prefixes := list -}}
{{- if .Values.features.accessControl.enabled -}}{{- $prefixes = append $prefixes "ACCESS_CONTROL_STORE_" -}}{{- end -}}
{{- if .Values.features.versioning.enabled -}}{{- $prefixes = append $prefixes "VERSIONING_STORE_" -}}{{- end -}}
{{- /* The accounts plugin's settings put env_prefix TAI_ACCOUNTS_PG_ over
       fields already named pg_* — so the env names it reads double the PG:
       TAI_ACCOUNTS_PG_PG_HOST etc. The chart must emit what the code reads. */ -}}
{{- if .Values.features.accounts.enabled -}}{{- $prefixes = append $prefixes "TAI_ACCOUNTS_PG_" -}}{{- end -}}
{{- if .Values.features.connectors.enabled -}}{{- $prefixes = append $prefixes "CONNECTOR_STORE_" -}}{{- end -}}
{{- if .Values.features.marketplace.enabled -}}{{- $prefixes = append $prefixes "MARKETPLACE_STORE_" -}}{{- end -}}
{{/* TAI_DB_ is the schema-admin connection used by `tai db apply`; wired whenever any feature is on. */}}
{{- if include "tai.anyPgFeature" . -}}{{- $prefixes = append $prefixes "TAI_DB_" -}}{{- end -}}
{{- $prefixes | toJson -}}
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
{{/* Redis-backed feature URLs (wired to the chart redis endpoint). Wired
     unconditionally — each defaults to localhost otherwise, which fails loudly
     in-pod the moment its feature is exercised. These surfaces are inert until
     used: their env presence alone opens no store at boot. */}}
{{- range list "ACCESS_CONTROL_REDIS_URL" "INTERACTIONS_REDIS_URL" "TAI_TOOL_RUNS_REDIS_URL" "TAI_RATE_LIMIT_REDIS_URL" "HOOKS_REDIS_URL" }}
- name: {{ . }}
  value: {{ include "tai.redis.url" $ | quote }}
{{- end }}
{{/* Connector store redis URL. Unlike the URLs above, the app marks connectors
     "in use" from the mere presence of any CONNECTOR_STORE_* env and opens the
     connector catalog's Postgres at boot — so this is wired ONLY when the
     connectors feature is on, together with its CONNECTOR_STORE_PG_* half, never
     unconditionally (which would half-activate connectors and stall boot on an
     unwired Postgres). */}}
{{- if .Values.features.connectors.enabled }}
- name: CONNECTOR_STORE_REDIS_URL
  value: {{ include "tai.redis.url" . | quote }}
{{- end }}
{{/* Postgres-backed feature connections. */}}
{{- $root := . -}}
{{- range $prefix := (include "tai.pgFeaturePrefixes" . | fromJsonArray) }}
- name: {{ $prefix }}PG_HOST
  value: {{ include "tai.postgres.host" $root | quote }}
- name: {{ $prefix }}PG_PORT
  value: {{ $root.Values.postgresql.port | quote }}
- name: {{ $prefix }}PG_DB
  value: {{ $root.Values.postgresql.database | quote }}
- name: {{ $prefix }}PG_USER
  value: {{ $root.Values.postgresql.username | quote }}
- name: {{ $prefix }}PG_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tai.dbSecretName" $root | quote }}
      key: {{ include "tai.dbSecretKey" $root | quote }}
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

