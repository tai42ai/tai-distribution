# tai-sandbox-claude-code

The Claude Code session image of the [tai42 platform](https://github.com/tai42ai/tai42): the runtime the `claude_code` agent runs in when it works inside a sandbox session. It carries `python`, the `claude-agent-sdk` wheel, and the `claude` CLI that wheel bundles, on a non-root user with `/workspace` as the working directory. Nothing else: no platform code, no credentials.

- **Docs:** [docs.tai42.ai/operate/sandbox](https://docs.tai42.ai/operate/sandbox)
- **Distribution (Dockerfile, release CI):** [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution)
- **Mirror:** `ghcr.io/tai42ai/tai-sandbox-claude-code`

## How to use this image

The image is not run by hand. The `tai42-sandbox-docker` provider starts one container per conversation from it, mounts that conversation's workspace volume at `/workspace`, and injects the session credentials into the container environment at start. The `claude_code` agent then drives the SDK inside.

Point the agent at the image **by digest**; a bare tag is refused:

```sh
TAI_AGENTS_CLAUDE_SESSION_IMAGE=docker.io/tai42/tai-sandbox-claude-code@sha256:<digest>
```

## Tags

Distribution semver — `X.Y.Z`, `X.Y`, `X`, and `latest` — cut on the same release as the platform image. Every manifest list is multi-arch (amd64 + arm64), trivy-gated before push, and cosign-keyless-signed in both registries.

## License

Apache-2.0. The full source of the image build lives in [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution).
