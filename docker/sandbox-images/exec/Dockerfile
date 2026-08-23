# Lean generic exec session image — for langchain_deep_agent's durable
# SandboxSessionBackend, which derives ls/read/glob/grep/edit/write by running
# server-side shell + python3 scripts via session.exec(["sh","-lc",…]). It needs
# only sh + python3 + coreutils and NOTHING vendor-specific: NO claude-agent-sdk,
# NO claude binary, NO runner code, NO credentials. Consumed BY DIGEST
# (…@sha256:…) via langchain_deep_agent's own session_image setting,
# cosign-signed at publish.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="tai-sandbox-exec" \
      org.opencontainers.image.description="Lean generic sandbox session image (sh + python3 + coreutils) for the langchain_deep_agent durable backend; no vendor SDK, no runner code, no credentials." \
      org.opencontainers.image.vendor="tai42" \
      org.opencontainers.image.url="https://tai42.ai" \
      org.opencontainers.image.source="https://github.com/tai42ai/tai-distribution" \
      org.opencontainers.image.licenses="Apache-2.0"

# The slim tag lags the debian security repo (the release trivy gate fails on
# fixable CRITICAL/HIGH CVEs); apply the security updates the tag has not
# absorbed yet — the same posture as the platform image.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (uid 1000). The session never needs root.
RUN groupadd --gid 1000 sandbox \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sandbox

# Build-time smoke: the base already carries the shell + python3 + coreutils the
# derived scripts call — assert their presence, and assert the vendor SDK is
# ABSENT (this image must never carry it). Fails the build loudly either way.
RUN set -eu; \
    for c in sh ls cat find grep python3; do \
      command -v "$c" >/dev/null || { echo "missing required command: $c" >&2; exit 1; }; \
    done; \
    if python3 -c "import claude_agent_sdk" 2>/dev/null; then \
      echo "claude_agent_sdk must NOT be present in the exec image" >&2; exit 1; \
    fi

# /workspace owned by uid 1000 so a fresh mounted volume seeds uid-1000-writable:
# the deep agent roots /workspace/project here.
RUN mkdir -p /workspace && chown 1000:1000 /workspace

WORKDIR /workspace
USER 1000:1000
