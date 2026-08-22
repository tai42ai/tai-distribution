# Claude session image — carries the claude-agent-sdk and its bundled real
# `claude` CLI and NOTHING consumer-specific: NO runner code (the adapter
# materializes the runner payload per turn into /workspace/.runner), NO
# credentials. One published digest stays plugin-version-agnostic and serves
# every plugin version. Consumed BY DIGEST (…@sha256:…), cosign-signed at
# publish.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="tai-sandbox-claude-code" \
      org.opencontainers.image.description="Pinned session image for the claude_code sandbox runner: claude-agent-sdk plus its bundled claude CLI, no runner code and no credentials." \
      org.opencontainers.image.vendor="tai42" \
      org.opencontainers.image.url="https://tai42.ai" \
      org.opencontainers.image.source="https://github.com/tai42ai/tai-distribution" \
      org.opencontainers.image.licenses="Apache-2.0"

# Non-root runtime user (uid 1000). The session never needs root.
RUN groupadd --gid 1000 sandbox \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sandbox

# The SDK pin is the single source in requirements.txt beside this Dockerfile.
# The wheel bundles the real Claude Code CLI as a self-contained native binary
# under the package (no Node.js, no console-script), so after install surface it
# on PATH with a symlink to the bundled binary — never a second copy or a node
# layer. The SDK resolves the same bundled binary internally; the symlink makes
# `claude` directly invokable (the build smoke below and any exec into a session).
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt \
 && ln -s "$(python -c 'import os, claude_agent_sdk as c; print(os.path.join(os.path.dirname(c.__file__), "_bundled", "claude"))')" /usr/local/bin/claude

# Build-time smoke: the SDK must import and its bundled `claude` CLI must be
# present and runnable on this arch. Fails the build loudly if the pinned wheel
# is arch-incomplete or ships without its bundled binary — never patched over
# with an apt/node layer.
RUN claude --version \
 && python -c "import claude_agent_sdk"

# /workspace owned by uid 1000 so a fresh named volume mounted here seeds
# uid-1000-writable: the runner writes /workspace/project,
# /workspace/.claude-home, and /workspace/.runner; without this the first turn
# fails EACCES.
RUN mkdir -p /workspace && chown 1000:1000 /workspace

WORKDIR /workspace
USER 1000:1000
