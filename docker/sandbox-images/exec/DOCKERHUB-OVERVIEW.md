# tai-sandbox-exec

The lean shell session image of the [tai42 platform](https://github.com/tai42ai/tai42): `sh`, `python3`, and coreutils on a slim base, on a non-root user with `/workspace` as the working directory. Nothing else: no SDK, no platform code, no credentials. It is the runtime behind the `langchain_deep_agent` sandbox shell.

- **Docs:** [docs.tai42.ai/operate/sandbox](https://docs.tai42.ai/operate/sandbox)
- **Distribution (Dockerfile, release CI):** [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution)
- **Mirror:** `ghcr.io/tai42ai/tai-sandbox-exec`

## How to use this image

The image is not run by hand. The `tai42-sandbox-docker` provider starts one container per conversation from it, mounts that conversation's workspace volume at `/workspace`, and injects the session credentials into the container environment at start.

Point the agent at the image **by digest**; a bare tag is refused:

```sh
TAI_AGENTS_LANGCHAIN_DEEP_SESSION_IMAGE=docker.io/tai42/tai-sandbox-exec@sha256:<digest>
```

## Tags

Distribution semver — `X.Y.Z`, `X.Y`, `X`, and `latest` — cut on the same release as the platform image. Every manifest list is multi-arch (amd64 + arm64), trivy-gated before push, and cosign-keyless-signed in both registries.

## License

Apache-2.0. The full source of the image build lives in [tai42ai/tai-distribution](https://github.com/tai42ai/tai-distribution).
