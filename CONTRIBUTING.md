# Contributing to tai-distribution

`tai-distribution` is the client distribution for **tai**: the official
container image, a Docker Compose bundle, a Helm chart, and the release CI that
publishes them. It ships artifacts, not a Python package — one version story
pins every first-party `tai42-*` package.

## Ground rules

- **One version story.** A distribution tag (`X.Y.Z`) pins the `tai42-*` packages
  at a matching release; keep the image, chart, and compose bundle in lockstep.
- **Release builds pull from PyPI.** The release image installs `tai42-*` from
  PyPI and fetches tai-studio at the SHA in `docker/STUDIO_REF`. Only the dev
  build (`SOURCE=local`) uses local checkouts — the `tai42` monorepo and the
  `tai-studio` repo — and it MUST run on a local builder — its context is the
  directory holding those checkouts, which may hold untracked secrets.
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
(`manifest.yml` + env). New plugins are not baked into the image either: the
image is **minimal-core** (the platform packages plus one reference provider per
infrastructure kind), and every other plugin publishes to PyPI and registers in
the marketplace catalog, then joins a deployment at runtime (a marketplace
install) or in a derived image. So this repo changes only when the shipped
packaging itself changes:

- **A new plugin of any kind (first- or third-party, including backends):** no
  change here. It publishes to PyPI + the marketplace catalog; a deployment adds
  it via a derived image (`FROM docker.io/tai42/tai`, then as `root` install your
  package into the venv — see the README's derived-image recipe — with
  `backend_module` / `lifecycle_modules` pointed at it in the manifest) or a
  marketplace install, and runs any extra service it needs itself.
- **A change to the minimal core itself** (a platform package, or the one
  reference provider per infrastructure kind the image bakes): the matching
  entry in `docker/Dockerfile`'s install list and `docker/pypi-requirements.txt`,
  kept as one roster.
- **A NEW companion infrastructure service a plugin needs** (e.g. a broker type
  the stack doesn't already ship): one opt-in compose profile for that service.
  The default stack never changes.

The existing profiles follow exactly that rule: `celery` (RabbitMQ broker),
`minio` (S3 backing), `agents-redis` (module-capable Redis) — each provides an
infra service for a plugin you add yourself, not the plugin itself.

## Naming

PyPI is a flat namespace with no owner in the path, so distributions carry the
`tai42-` prefix. The platform packages live as members inside the `tai42`
monorepo at `core/<name>` or `plugins/<name>`; the live standalone repos (e.g.
`tai-studio`) keep their `tai-<name>` names, because the `tai42ai` organisation
already namespaces them. Import packages follow the distribution.

| Surface | Form |
| --- | --- |
| Distribution — PyPI, `pip install`, dependency pins | `tai42-<name>` |
| Import package | `tai42_<name>` |
| Monorepo member directory | `core/<name>` or `plugins/<name>` |
| Standalone GitHub repository | `tai-<name>` |

So a dependency is declared as `tai42-<name>` while its source lives at
`core/<name>` / `plugins/<name>` in the `tai42` monorepo (or, for a standalone
repo, at `tai-<name>`), and every spelling is correct in its own context.

Some surfaces are deliberately neither, and must not be renamed: the `tai` CLI
command (`tai42` is an alias), the Prometheus metric namespace (`tai_tool_*`),
`TAI_*` environment variables, and the `tai-plugin.yml` descriptor filename.

## Dev

Build the image from your local checkouts — the `tai42` monorepo and the
`tai-studio` repo cloned beside this one — on a local builder; see the README
for the full note on why:

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
