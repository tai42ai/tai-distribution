# Contributing to tai-distribution

`tai-distribution` is the client distribution for **tai**: the official
container image, a Docker Compose bundle, a Helm chart, and the release CI that
publishes them. It ships artifacts, not a Python package — one version story
pins every first-party `tai-*` package.

## Ground rules

- **One version story.** A distribution tag (`X.Y.Z`) pins the `tai-*` packages
  at a matching release; keep the image, chart, and compose bundle in lockstep.
- **Release builds pull from PyPI.** The release image installs `tai-*` from
  PyPI and fetches tai-studio at the SHA in `docker/STUDIO_REF`. Only the dev
  build (`SOURCE=local`) uses local sibling checkouts, and it MUST run on a
  local builder — its context is the parent directory of every checkout, which
  may hold untracked secrets.
- **Multi-arch stays multi-arch.** The image builds for amd64 + arm64; don't add
  arch-specific steps that break either target.

## Layout

- `docker/Dockerfile` — multi-stage image (`SOURCE=local | pypi`)
- `docker/STUDIO_REF` — tai-studio commit SHA the release image builds from
- `compose/` — Docker Compose bundles
- `charts/tai/` — Helm chart
- `charts/test/kind-install.sh` — local kind smoke install for the chart
- `.github/workflows/` — release + CI

## When does this repo need changes?

Plugin ACTIVATION never happens here — it is client-side configuration
(`manifest.yml` + env). This repo changes only when the shipped packaging
itself changes:

- **Third-party / community plugin (any kind, including backends):** no
  change here, ever. Extend the image (`FROM docker.io/tai42/tai` + pip
  install your package), point `backend_module` / `lifecycle_modules` at it
  in your manifest, and run any extra service it needs yourself.
- **A new first-party plugin joins the batteries image:** one line in
  `docker/Dockerfile`'s install lists.
- **…and only if that plugin requires a NEW companion infrastructure
  service** (e.g. a broker type the stack doesn't already ship): one opt-in
  compose profile for that service. The default stack never changes.

The existing profiles follow exactly that rule: `celery` (RabbitMQ broker),
`minio` (S3 backing), `agents-redis` (module-capable Redis) — each exists
for an infra service, not for the plugin itself.

## Dev

Build the image from your local sibling checkouts (all `tai-*` repos cloned
beside this one) on a local builder — see the README for the full note on why:

```sh
docker buildx build -f docker/Dockerfile \
  --build-context siblings=/path/to/checkouts \
  --build-arg SOURCE=local \
  --load -t docker.io/tai42/tai:latest .
```

Smoke-test the Helm chart against a local kind cluster:

```sh
charts/test/kind-install.sh docker.io/tai42/tai:latest
```

Before any commit, run a secret scan over the repo (e.g.
`detect-secrets scan`).

## License

By contributing you agree your contributions are licensed under Apache-2.0.
