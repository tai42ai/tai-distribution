#!/bin/sh
# dind inner-bridge egress firewall — programmed at daemon start, BEFORE the
# daemon accepts any session create. Runs INSIDE the rootless-dind user
# namespace (NET_ADMIN there, no host privilege).
#
# Every agent session is an inner container of this rootless-dind daemon, NAT'd
# out through the engine's network namespace. This installs deny rules into the
# DOCKER-USER chain — the chain docker guarantees is traversed FIRST in FORWARD
# and never flushes on daemon reloads — so they apply to ALL inner-session
# egress:
#   - DROP the deployment-internal RFC1918 ranges (10/8, 172.16/12, 192.168/16;
#     the sandbox-ctrl subnet is one of these) — a session must reach NOTHING of
#     the deployment (serve, backend, datastores, the control API).
#   - DROP cloud metadata 169.254.169.254 and the whole 169.254.0.0/16 link-local
#     range that covers it.
#   - ALLOW everything else: the public internet + DNS. Egress default is OPEN by
#     ruling (README §A.6) — this is NOT an Anthropic-only allowlist (that
#     hardening is offered in docs, not forced here).
#
# The app control path is unaffected: serve/backend -> engine:2376 hits the
# engine's INPUT (not FORWARD), so the control API stays reachable while inner
# sessions cannot address the engine's own RFC1918 address.
#
# Errors surface LOUDLY: any step that fails aborts (the caller backgrounds this
# and continues starting dockerd, so a failure is visible in the engine log and
# must be treated as a STOP — never a daemon whose sessions are unconfined).
set -eu

log() { echo "[dind-egress-firewall] $*" >&2; }
fail() { log "FATAL: $*"; exit 1; }

# Deployment-internal + link-local/metadata ranges denied to inner sessions.
INTERNAL_CIDRS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16"

# 1. Wait for the rootless dockerd (same PID namespace as this script). Its
#    network namespace — where docker0 and the DOCKER-USER chain live — is the
#    rootlesskit child netns we must program.
i=0
DOCKERD_PID=""
while [ -z "$DOCKERD_PID" ]; do
  DOCKERD_PID="$(pidof dockerd 2>/dev/null || true)"
  [ -n "$DOCKERD_PID" ] && break
  i=$((i + 1))
  [ "$i" -gt 120 ] && fail "dockerd did not start within 120s"
  sleep 1
done
# pidof may return several PIDs (space-separated); take the first.
DOCKERD_PID="${DOCKERD_PID%% *}"
log "dockerd pid=$DOCKERD_PID"

# Every iptables call runs INSIDE dockerd's netns (rootless: NET_ADMIN within the
# user namespace, no host privilege).
in_netns() { nsenter -t "$DOCKERD_PID" -n "$@"; }

# 2. Wait for docker to create the DOCKER-USER chain (it does so once the daemon
#    finishes bringing up its iptables). Docker traverses DOCKER-USER FIRST in
#    FORWARD and preserves pre-existing user rules across reloads, so ours stick.
i=0
while ! in_netns iptables -w -n -L DOCKER-USER >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 120 ] && fail "DOCKER-USER chain not present within 120s of dockerd start"
  sleep 1
done

# 3. Install the deny rules idempotently at the TOP of DOCKER-USER (drop before
#    docker's own accept). Insert at position 1 only when the rule is absent, so
#    a re-run is a no-op rather than a duplicate.
add_drop() {
  _cidr="$1"
  if in_netns iptables -w -C DOCKER-USER -d "$_cidr" -j DROP 2>/dev/null; then
    log "already denied -> $_cidr"
    return 0
  fi
  in_netns iptables -w -I DOCKER-USER 1 -d "$_cidr" -j DROP \
    || fail "could not install DROP rule for $_cidr"
  log "deny egress -> $_cidr"
}
for cidr in $INTERNAL_CIDRS; do
  add_drop "$cidr"
done

log "egress firewall active: internal ranges + metadata denied, internet OPEN"
