# Release and deployment policy

## Deployment preference

The public Compose example and Unraid template use:

```text
ghcr.io/kdmn2/nzbget-sab-bridge:latest
```

`latest` is intended for normal installation. Pin an immutable version tag when
you need a reproducible deployment or a rollback target.

## Tag roles

| Tag | Meaning |
|---|---|
| `main` | Development builds from the `main` branch. Not a release promise. |
| `latest` | The newest tested, verified release. Advances only through the release promotion job. |
| `vX.Y.Z` | Canonical immutable release tag. Never move or delete. |
| `X.Y.Z` | Immutable numeric release tag matching the same release. |
| `X.Y` | Moving minor release tag; it can advance within a major/minor line. |

The Git tag ruleset protects `v*` tags from deletion and non-fast-forward moves.
The publish workflow refuses to overwrite an existing `vX.Y.Z` or `X.Y.Z` image
tag.

## Promotion rules

- `main` builds only publish the `main` tag.
- A version tag publishes its immutable version tags and moving minor tag first.
- A separate promotion job compares the candidate semver with the version label
  of the current `latest` image.
- `latest` advances only when the candidate is newer.
- Promotion is serialized with the `ghcr-latest-promotion` concurrency group.
- An older maintenance release therefore cannot move `latest` backward, even
  when its workflow was queued or started late.
- Pull requests never publish and never receive package-write credentials.

The promotion job runs only after checks, container smoke, Unraid validation,
version-tag image publication, and a smoke test of the anonymously pullable
published version image succeed.

## Current release behind latest

At the time of this update (2026-10-24):

- **`latest`:** `v0.1.5`
- **Digest:** `sha256:2de984cb87470101f366a93ff5a8d4b4287c27ec9f7dd7dc3b77ba70e845cf64`
- **Architecture:** `linux/amd64`
- **Release:** https://github.com/kdmn2/nzbget-sab-bridge/releases/tag/v0.1.5

Check the live value:

```bash
docker buildx imagetools inspect ghcr.io/kdmn2/nzbget-sab-bridge:latest
```

## Rollback

Rollback means pinning an older immutable release, not moving `latest`
backward.

1. Choose the previous release tag, for example `v0.1.0`.
2. Set the Unraid template's Repository or Compose `BRIDGE_IMAGE` to that
   versioned image:

   ```text
   ghcr.io/kdmn2/nzbget-sab-bridge:v0.1.0
   ```

3. Recreate only the bridge container with the same `/data` appdata mount.
4. Verify `/healthz`, `/readyz`, and known IDs from the SQLite map.
5. Preserve the previous appdata backup. The bridge ID database format is
   independent of the image tag; do not delete it.

Rolling back the bridge image does not change SAB queue/history jobs. Do not
retag `latest` backwards; keep the old release pinned explicitly.

## Unattended updates

The template references `latest` for manual install/update convenience, but this
project does not enable or require unattended updates. Do not enable automatic
Unraid container updates for this service unless you have separately reviewed
the release and backup procedure. The maintainer does not change Unraid host
settings as part of publication.

Masked secret fields in the Unraid template hide values in the UI; they do not
encrypt the container environment. Host administrators can inspect container
configuration.
