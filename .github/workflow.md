# GitHub Actions Workflows

This branch (`arm32`) builds **32-bit ARM (linux/arm/v7, armhf)** Docker images and publishes them to GitHub Container Registry (ghcr.io). Every image tag carries an `armhf` marker so it can never be confused with the 64-bit images of the upstream `main` branch.

Image paths below use `<owner>/linux-voice-assistant`, which resolves to the repository that runs the workflow (lowercased), e.g. `ghcr.io/omaramin-2000/linux-voice-assistant`.

## Workflows Overview

| Workflow | Trigger | Image Tag(s) |
|----------|---------|--------------|
| [docker-build-release.yml](workflows/docker-build-release.yml) | Tags: `v1.2.0`, `v1.2.0-alpha1` | `1.2.0-armhf`, `1.2-armhf`, `1-armhf`, `armhf` |
| [docker-build-nightly.yml](workflows/docker-build-nightly.yml) | Push to `arm32` (or manual run) | `nightly-armhf` |
| [docker-build-pr.yml](workflows/docker-build-pr.yml) | Pull requests targeting `arm32` | `<pr-number>-armhf` (image `linux-voice-assistant-pr`) |
| [lint.yml](workflows/lint.yml) | Push to `arm32`, release tags, PRs targeting `arm32` | none (Python 3.11) |
| [tests.yml](workflows/tests.yml) | Push to `arm32`, release tags, PRs targeting `arm32` | none (Python 3.11) |
| [pr-labels.yaml](workflows/pr-labels.yaml) | PRs targeting `arm32` | none (applies a release-note label) |
| [gen_changelog.yml](workflows/gen_changelog.yml) | Release published | none (updates `CHANGELOG.md` on `arm32`) |

## Detailed Description

### Release Workflow (`docker-build-release.yml`)

Publishes versioned images for new releases.

**Triggers:**
- Tags matching `v[0-9]+.[0-9]+.[0-9]+` (e.g., `v1.0.0`)
- Tags matching `v[0-9]+.[0-9]+.[0-9]+-[a-zA-Z0-9]+` (e.g., `v1.0.0-alpha1`)

**Image Tags:**
```
ghcr.io/<owner>/linux-voice-assistant:1.0.0-armhf
ghcr.io/<owner>/linux-voice-assistant:1.0-armhf
ghcr.io/<owner>/linux-voice-assistant:1-armhf
ghcr.io/<owner>/linux-voice-assistant:armhf
```

`armhf` always points to the latest **stable** release. Prereleases (e.g. `v1.3.0-alpha1`) only get their own `1.3.0-alpha1-armhf` tag. `docker-compose.yml` defaults to the `armhf` tag.

### Nightly Workflow (`docker-build-nightly.yml`)

Builds an image from every push to the `arm32` branch (documentation-only changes are skipped). It can also be started manually from the Actions tab.

**Image Tag:**
```
ghcr.io/<owner>/linux-voice-assistant:nightly-armhf
```

### PR Workflow (`docker-build-pr.yml`)

Builds images for pull requests that target `arm32`, using the PR number as tag.

**Image Tag:**
```
ghcr.io/<owner>/linux-voice-assistant-pr:<pr-number>-armhf
```

### Lint and Unit Tests (`lint.yml`, `tests.yml`)

Run on Python 3.11, the only Python version supported on this branch. The tests do not need armhf hardware; they run on the normal x86-64 runner.

## Architecture Support

All Docker workflows build **only** `linux/arm/v7`. GitHub-hosted runners are x86-64, so the build runs under QEMU emulation (`docker/setup-qemu-action`, `platforms: arm`). The Docker layer cache is stored in the GitHub Actions cache (`scope=armhf`) because emulated builds are slow, especially the first one.

The image is built from the `Dockerfile` in the repository root (`python:3.11-slim-bookworm`, with piwheels as an extra pip index).

## Setup Requirements

The workflows use `GITHUB_TOKEN`, which GitHub Actions provides automatically.

The first time an image is pushed, GitHub creates the package as **private**. To let devices pull it without logging in, open the package page (Profile or Organization → Packages → `linux-voice-assistant`) → Package settings → Change visibility → Public.

If you use a fork, point the compose file at your own image, for example `LVA_IMAGE=ghcr.io/<owner>/linux-voice-assistant` in `.env`; the compose default is `ghcr.io/ohf-voice/linux-voice-assistant`.

## Creating a Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

This creates images with the following tags:
- `ghcr.io/<owner>/linux-voice-assistant:1.0.0-armhf`
- `ghcr.io/<owner>/linux-voice-assistant:1.0-armhf`
- `ghcr.io/<owner>/linux-voice-assistant:1-armhf`
- `ghcr.io/<owner>/linux-voice-assistant:armhf`
