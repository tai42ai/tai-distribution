#!/usr/bin/env bash
# kind-install.sh — install the tai Helm chart into an ephemeral kind cluster
# under a restricted Pod Security Admission namespace, wait for every workload
# (the app AND the quickstart Postgres/Redis StatefulSets) to become Ready, run
# `helm test`, then tear the cluster down.
#
# Used both for local chart validation and by .github/workflows/ci.yml, which
# passes its SOURCE=pypi smoke image. Every failure is loud: `set -euo pipefail`
# plus explicit checks that STOP with a non-zero exit and dump diagnostics.
#
# Usage:
#   charts/test/kind-install.sh <image-ref>
#   TAI_IMAGE=docker.io/tai42/tai:latest charts/test/kind-install.sh
#
# The image reference MUST include a tag. It is loaded into the cluster with
# `kind load docker-image`, and the chart is pinned to it with
# image.pullPolicy=Never so the loaded image is used (never a registry pull) and
# a missing image fails loudly instead of silently pulling something else.
#
# Environment:
#   TAI_IMAGE       image reference to test (arg 1 takes precedence)
#   CLUSTER_NAME    kind cluster name        (default: tai-kind-<pid>)
#   NAMESPACE       target namespace         (default: tai-test)
#   RELEASE         helm release name        (default: tai)
#   WAIT_TIMEOUT    per-workload readiness timeout (default: 300s)
#   KEEP_CLUSTER=1  skip teardown (leave the cluster up for debugging)

set -euo pipefail

# --- inputs -----------------------------------------------------------------

IMAGE="${1:-${TAI_IMAGE:-}}"
if [[ -z "${IMAGE}" ]]; then
  echo "ERROR: no image reference given (pass as arg 1 or set TAI_IMAGE)" >&2
  exit 2
fi

# Require an explicit tag. Split on the final colon, but only if that colon is
# in the image NAME component (after the last '/'), so a registry:port host such
# as localhost:5000/foo is not mistaken for a tag.
image_name="${IMAGE##*/}"
if [[ "${image_name}" != *:* ]]; then
  echo "ERROR: image reference must include a tag: ${IMAGE}" >&2
  exit 2
fi
IMAGE_REPO="${IMAGE%:*}"
IMAGE_TAG="${IMAGE##*:}"

CLUSTER_NAME="${CLUSTER_NAME:-tai-kind-$$}"
NAMESPACE="${NAMESPACE:-tai-test}"
RELEASE="${RELEASE:-tai}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-300s}"

# Chart lives at ../tai relative to this script (charts/test/kind-install.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/tai"

# --- preflight --------------------------------------------------------------

for bin in docker kind kubectl helm; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "ERROR: required binary not found on PATH: ${bin}" >&2
    exit 3
  fi
done

if [[ ! -f "${CHART_DIR}/Chart.yaml" ]]; then
  echo "ERROR: chart not found at ${CHART_DIR} (Chart.yaml missing)" >&2
  exit 3
fi

# --- teardown ---------------------------------------------------------------

teardown() {
  local ec=$?
  if [[ "${ec}" -ne 0 ]]; then
    echo "==> FAILURE (exit ${ec}) — dumping diagnostics for ${NAMESPACE}" >&2
    kubectl get pods -n "${NAMESPACE}" -o wide >&2 2>/dev/null || true
    kubectl describe pods -n "${NAMESPACE}" >&2 2>/dev/null || true
    kubectl get events -n "${NAMESPACE}" --sort-by=.lastTimestamp >&2 2>/dev/null || true
  fi
  if [[ "${KEEP_CLUSTER:-0}" == "1" ]]; then
    echo "==> KEEP_CLUSTER=1 — leaving cluster ${CLUSTER_NAME} up for debugging" >&2
  else
    echo "==> Tearing down kind cluster ${CLUSTER_NAME}" >&2
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  fi
  exit "${ec}"
}
trap teardown EXIT

# --- run --------------------------------------------------------------------

echo "==> Creating kind cluster ${CLUSTER_NAME}"
kind create cluster --name "${CLUSTER_NAME}" --wait 120s

echo "==> Creating namespace ${NAMESPACE} with restricted Pod Security Admission"
kubectl create namespace "${NAMESPACE}"
kubectl label namespace "${NAMESPACE}" \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/warn-version=latest \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/audit-version=latest \
  --overwrite

echo "==> Loading image ${IMAGE} into cluster ${CLUSTER_NAME}"
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"

echo "==> helm install ${RELEASE} into namespace ${NAMESPACE}"
helm install "${RELEASE}" "${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  --set image.repository="${IMAGE_REPO}" \
  --set image.tag="${IMAGE_TAG}" \
  --set image.pullPolicy=Never

# Wait for every Deployment and StatefulSet to roll out. `rollout status` blocks
# until Ready or errors, and covers the quickstart Postgres/Redis StatefulSets
# explicitly — their fitness under the restricted namespace is part of the
# chart's claim, not just the app's.
echo "==> Waiting for all workloads to become Ready (timeout ${WAIT_TIMEOUT})"
workloads="$(kubectl get deployment,statefulset -n "${NAMESPACE}" -o name)"
if [[ -z "${workloads}" ]]; then
  echo "ERROR: chart rendered no Deployments or StatefulSets in ${NAMESPACE}" >&2
  exit 4
fi
while IFS= read -r res; do
  [[ -n "${res}" ]] || continue
  echo "    rollout: ${res}"
  kubectl rollout status "${res}" -n "${NAMESPACE}" --timeout "${WAIT_TIMEOUT}"
done <<< "${workloads}"

# Final assertion: every pod reports Ready (belt-and-suspenders over rollout).
echo "==> Asserting all pods Ready"
kubectl wait --namespace "${NAMESPACE}" --for=condition=Ready pods --all \
  --timeout "${WAIT_TIMEOUT}"

echo "==> Running helm test ${RELEASE}"
helm test "${RELEASE}" --namespace "${NAMESPACE}" --logs --timeout "${WAIT_TIMEOUT}"

echo "==> SUCCESS: chart installed, all workloads Ready, helm test passed"
