# Changelog

All notable changes are documented here. This project follows semantic
versioning for its own release tags, but the NZBGet compatibility profile is a
separate claim and remains provisional.

## 0.1.1 - 2026-10-23

Production-readiness patch; no new RPC features.

- Serialized bridge mutations with a bounded bridge-local asyncio lock.
  External SAB clients can still race the bridge.
- Restricted JSON-P-RPC to read-only methods and escaped script-breaking
  payload characters.
- Bounded buffered SABnzbd response bodies with `SAB_MAX_RESPONSE_BYTES`.
- Added stream-enforced body-limit, backend failure, SQLite lock/disk/backup,
  JSON-P, authentication, and concurrency tests.
- Added dependency and container vulnerability scanning to CI.
- Added VM-to-Unraid ID migration and SQLite backup/restore documentation.
- Changed fresh appdata setup examples to non-recursive ownership changes and
  documented Unraid secret masking limits.
- Updated GitHub Actions to current pinned action revisions.

## 0.1.0 - 2026-10-23

Initial public prerelease.

- Added bridge-mode JSON-RPC and JSON-P-RPC endpoints for a limited NZBGet
  compatibility surface.
- Added read-only `version`, `config`, `configtemplates`, `status`,
  `listgroups`, and final `history`.
- Added global queue pause/resume aliases and per-job `GroupPause` /
  `GroupResume`.
- Added queue-item `GroupSetCategory` and a lossy `GroupSetPriority` mapping.
- Added absolute `rate` control with SAB read-back verification.
- Added plain `.nzb` append uploads and HTTP/HTTPS URL append submissions using
  the NZBGet 21.1 practical append forms.
- Added persistent SQLite NZBID mapping.
- Added capture-mode infrastructure for protocol discovery.
- Added Docker image, portable Compose example, Unraid template, original icon,
  and GitHub Actions checks/publication.
- Known limitations: provisional NZBGet 21.1 compatibility, no full NZBClient
  smoke test, no `listfiles`/`loadlog`, no hidden history, no delete/retry or
  history mutations, and no separate post-processing/scan pause controls.
