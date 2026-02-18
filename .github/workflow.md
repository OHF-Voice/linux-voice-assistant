# GitHub Actions Workflows

This project uses three separate Docker build workflows that automatically build and publish Docker images to GitHub Container Registry (ghcr.io).

## Workflows Overview

| Workflow | Trigger | Image Tag(s) |
|----------|---------|--------------|
| [docker-build-release.yml](.github/workflows/docker-build-release.yml) | Tags: `v1.2.0`, `v1.2.0-alpha1` | `1.2.0`, `1.2`, `latest` |
| [docker-build-nightly.yml](.github/workflows/docker-build-nightly.yml) | Push to `main` | `nightly` |
| [docker-build-branch.yml](.github/workflows/docker-build-branch.yml) | All other branches | Branch name |

## Detailed Description

### Release Workflow (`docker-build-release.yml`)

Publishes versioned images for new releases.

**Triggers:**
- Tags matching `v[0-9]+.[0-9]+.[0-9]+` (e.g., `v1.0.0`)
- Tags matching `v[0-9]+.[0-9]+.[0-9]+-[a-zA-Z0-9]+` (e.g., `v1.0.0-alpha1`)

**Image Tags:**
```
ghcr.io/ohf-voice/linux-voice-assistant:1.0.0
ghcr.io/ohf-voice/linux-voice-assistant:1.0
ghcr.io/ohf-voice/linux-voice-assistant:1
ghcr.io/ohf-voice/linux-voice-assistant:latest
```

### Nightly Workflow (`docker-build-nightly.yml`)

Automatically builds nightly images from the main branch.

**Triggers:**
- Push to `main` branch

**Image Tag:**
```
ghcr.io/ohf-voice/linux-voice-assistant:nightly
```

### Branch Workflow (`docker-build-branch.yml`)

Builds images for all other branches.

**Triggers:**
- Push to all branches except `main`
- No tags

**Image Tag:**
```
ghcr.io/ohf-voice/linux-voice-assistant:<branch-name>
```

## Multi-Architecture Support

All workflows build for multiple architectures:
- `linux/amd64` (x86_64)
- `linux/aarch64` (ARM 64-bit, e.g., Raspberry Pi 4)

## Setup Requirements

**No additional setup needed!** The workflows use `GITHUB_TOKEN` which is automatically provided by GitHub Actions.

## Creating a Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

This creates images with the following tags:
- `ghcr.io/ohf-voice/linux-voice-assistant:1.0.0`
- `ghcr.io/ohf-voice/linux-voice-assistant:1.0`
- `ghcr.io/ohf-voice/linux-voice-assistant:1`
- `ghcr.io/ohf-voice/linux-voice-assistant:latest`
