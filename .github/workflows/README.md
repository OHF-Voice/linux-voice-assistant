# GitHub Actions Workflows

## Docker Image Publishing

The `docker-publish.yaml` workflow automatically builds and publishes Docker images to GitHub Container Registry (ghcr.io).

### How It Works

**Triggers:**
- Push to `main` branch → builds and tags as `latest`
- Push tags matching `v*` (e.g., `v1.0.0`) → builds and tags with version numbers
- Pull requests → builds only (doesn't push)

**Multi-Architecture Support:**
The workflow builds for multiple architectures:
- `linux/amd64` (x86_64)
- `linux/arm64` (ARM 64-bit, like Raspberry Pi 4)
- `linux/arm/v7` (ARM 32-bit, like older Raspberry Pi models)

### Setup Requirements

**No additional setup needed!** The workflow uses `GITHUB_TOKEN` which is automatically provided by GitHub Actions.

### Usage

Once the workflow runs successfully, users can pull the image:

```bash
docker pull ghcr.io/ohf-voice/linux-voice-assistant:latest
```

Or use it in docker-compose.yaml:

```yaml
services:
  linux-voice-assistant:
    image: ghcr.io/ohf-voice/linux-voice-assistant:latest
    # ... rest of configuration
```

### Creating a Release

To publish a versioned image:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This creates images tagged as:
- `ghcr.io/ohf-voice/linux-voice-assistant:1.0.0`
- `ghcr.io/ohf-voice/linux-voice-assistant:1.0`
- `ghcr.io/ohf-voice/linux-voice-assistant:1`
- `ghcr.io/ohf-voice/linux-voice-assistant:latest`

### Making Images Public

By default, GitHub Container Registry images are private. To make them public:

1. Go to the package page: `https://github.com/orgs/OHF-Voice/packages/container/linux-voice-assistant`
2. Click "Package settings"
3. Scroll to "Danger Zone"
4. Click "Change visibility" → "Public"
