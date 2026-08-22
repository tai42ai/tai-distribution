#!/bin/sh
# dind inner-bridge egress firewall — programmed at daemon start, BEFORE the
# daemon accepts any session create. Installs rules at the top of the
# DOCKER-USER chain (docker traverses it FIRST in FORWARD and never flushes it
# on reloads) so inner-session egress to the deployment is filtered.
#
# POSTURE — runs as ROOT, enters dockerd's ROOTLESS netns with `nsenter -n`.
# The rootless daemon owns its network namespace via a rootlesskit CHILD user
# namespace; programming DOCKER-USER there needs CAP_SYS_ADMIN over that child
# userns, which the rootless user (uid 1000) does NOT hold. So this script is
# run as root — by the privileged container's root entrypoint in compose, and
# by a privileged root sidecar (shareProcessNamespace) in the k8s chart — while
# dockerd itself stays rootless. Errors surface LOUDLY (any failed step aborts —
# never a daemon whose sessions are unconfined).
#
# Every agent session is an inner container of this rootless-dind daemon, NAT'd
# out through the engine's network namespace, so these DOCKER-USER rules apply
# to ALL inner-session egress:
#   - DENY the full private + link-local space (10/8, 172.16/12, 192.168/16,
#     169.254/16) so a session reaches NOTHING private — not only peers on the
#     sandbox control network but any deployment infra reachable via the HOST's
#     own routing (e.g. an in-VPC datastore at 10.x off this engine's ENI), and
#     cloud metadata 169.254.169.254. Public internet stays OPEN (egress default
#     is OPEN by ruling — NOT an Anthropic-only allowlist; that hardening is
#     offered in docs, not forced here).
#   - The catch: the rootless daemon's OWN egress plumbing rides private space
#     too. With `rootlesskit --net=vpnkit`, vpnkit transparently proxies inner
#     outbound through the docker0 bridge address, and the DNS resolver is the
#     tap0 (vpnkit) gateway. So the daemon's own connected subnets are ACCEPTed
#     FIRST (above the DROPs) — else egress + DNS die.
#
# The app control path is unaffected: serve/backend -> engine:2376 hits the
# engine's INPUT (not FORWARD), so the control API stays reachable while inner
# sessions cannot address the engine's own private address.
set -eu

log() { echo "[dind-egress-firewall] $*" >&2; }
fail() { log "FATAL: $*"; exit 1; }

# Full private + link-local ranges denied to inner sessions.
PRIVATE_CIDRS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16"

# Select the dockerd whose netns actually holds the DOCKER-USER chain. During
# rootless startup `pidof dockerd` can briefly report a transient pid that then
# re-execs and dies, so pick the one that is READY (chain present) — not just
# the first — and wait until such a pid exists.
i=0
DOCKERD_PID=""
while [ -z "$DOCKERD_PID" ]; do
  for _p in $(pidof dockerd 2>/dev/null || true); do
    if nsenter -t "$_p" -n iptables -w -n -L DOCKER-USER >/dev/null 2>&1; then
      DOCKERD_PID="$_p"
      break
    fi
  done
  [ -n "$DOCKERD_PID" ] && break
  i=$((i + 1))
  [ "$i" -gt 120 ] && fail "no dockerd netns with a DOCKER-USER chain within 120s"
  sleep 1
done

# Every iptables call runs INSIDE dockerd's rootless netns.
in_netns() { nsenter -t "$DOCKERD_PID" -n "$@"; }

# The daemon's OWN connected subnets INSIDE its netns — the inner-session bridge
# (docker0) and the rootless vpnkit uplink (tap0, e.g. 192.168.65.0/24). These
# carry the sandbox's own egress + DNS plumbing and fall inside the private
# ranges above, so they are ACCEPTed first. Derived from the daemon's routes so
# they track whatever docker0/vpnkit actually use.
ALLOW_CIDRS="$(in_netns ip -o route show scope link 2>/dev/null | awk '{print $1}' | grep -E '^[0-9]+\.' || true)"
[ -n "$ALLOW_CIDRS" ] || fail "could not resolve the daemon's own subnets to allow"
log "dockerd pid=$DOCKERD_PID; allow(own) -> $ALLOW_CIDRS; deny(private) -> $PRIVATE_CIDRS"

# Insert at an incrementing position so rules land ABOVE docker's default RETURN
# in the intended order: the ACCEPTs first, then the DROPs. Idempotent — a
# re-run finds the rule already present and is a no-op rather than a duplicate.
POS=1
add_rule() {  # $1=ACCEPT|DROP  $2=cidr
  if in_netns iptables -w -C DOCKER-USER -d "$2" -j "$1" 2>/dev/null; then
    log "already set $1 -> $2"
    return 0
  fi
  in_netns iptables -w -I DOCKER-USER "$POS" -d "$2" -j "$1" \
    || fail "could not install $1 rule for $2"
  POS=$((POS + 1))
  log "$1 egress -> $2"
}
for cidr in $ALLOW_CIDRS; do add_rule ACCEPT "$cidr"; done
for cidr in $PRIVATE_CIDRS; do add_rule DROP "$cidr"; done

log "egress firewall active: private ranges + metadata denied (own bridge/uplink allowed), internet OPEN"
