# GitHub Actions Workflows

This project uses three separate Docker build workflows that automatically build and publish Docker images to GitHub Container Registry (ghcr.io).

## Workflows Overview

| Workflow | Trigger | Image Tag(s) |
|----------|---------|--------------|
| [docker-build-release.yml](.github/workflows/docker-build-release.yml) | Tags: `v1.2.0`, `v1.2.0-alpha1` | `1.2.0`, `1.2`, `latest` |
| [docker-build-nightly.yml](.github/workflows/docker-build-nightly.yml) | Push to `main` | `nightly` |
| [docker-build-pr.yml](.github/workflows/docker-build-pr.yml) | Pull requests | `pr-<pr-number>` |

The image workflows are joined by [e2e-tests.yml](.github/workflows/e2e-tests.yml) (E2E-Tests), which builds no image. It runs in the same cases as the image builds: on push to `main` (nightly), on pull requests (pr, with the approval of the `pr-build` environment, because the PR code runs on `pull_request_target`) and on version tags (release), and not for changes that only touch `**.md` or `docs/**`. It has two jobs, each with its own job summary (annotations for failed tests) and artifact with the LVA log and JUnit XML: **LVA** runs `tests/e2e` without `tests/e2e/peripheral_api` (artifact `e2e-results`), **LVA-Peripheral-API-V1** runs `tests/e2e/peripheral_api` only (artifact `e2e-peripheral-results`). See `tests/e2e/README.md`.

The unit test workflow ([unit-tests.yml](.github/workflows/unit-tests.yml)) publishes its result the same way: `./script/tests --junitxml=<file>` writes a JUnit XML, `.github/scripts/junit_summary.py` turns it into the job summary and failure annotations (a long test list is collapsed), and the XML is uploaded as the artifact `unit-results`.

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

### PR Workflow (`docker-build-pr.yml`)

Builds images for pull requests using the PR number as tag.

**Triggers:**
- Pull request opened, synchronized, or reopened

**Image Tag:**
```
ghcr.io/ohf-voice/linux-voice-assistant:pr-<pr-number>
```

## Breaking change log

Migration notes for breaking changes are collected in `breaking_change.md` (never edited by hand). The helper is `.github/scripts/breaking_change.py`.

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [pr-labels.yaml](.github/workflows/pr-labels.yaml) | PR opened/edited | When the PR gets the `breaking-change` label, fails the check unless the "Breaking change migration" section of the PR description has text. |
| [breaking-change-log.yml](.github/workflows/breaking-change-log.yml) | PR merged with label `breaking-change` | Adds `#<number> <title>` and the migration text at the top of the `## Unreleased` section and pushes to `main`. A PR that is already listed is not added twice. |
| [gen_changelog.yml](.github/workflows/gen_changelog.yml) | Release published (not prereleases) | Renames `## Unreleased` to `## <tag> (<date>)`, appends the entries as `## Breaking change migration` to the release notes (before `CHANGELOG.md` is generated from them) and pushes the file to `main`. |

## Multi-Architecture Support

All workflows build for multiple architectures:
- `linux/amd64` (x86_64)
- `linux/aarch64` (ARM 64-bit, e.g., Raspberry Pi 4)
- `linux/arm/v7` (ARM 32-bit)

Each workflow has three jobs. The slow armv7 build does not delay the 64-bit platforms:

1. `build-and-push-64` builds amd64 + arm64 and publishes the tag.
2. `build-and-push-32` builds armv7 in parallel and pushes it by digest only (no tag).
3. `merge-32` runs after both and adds the armv7 image to the already published tags, so the same tag contains all three architectures.

amd64/arm64 can be used as soon as job 1 is done. Until `merge-32` has finished, the tag has no armv7 variant. If `build-and-push-32` fails, the tag stays at amd64 + arm64.

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
