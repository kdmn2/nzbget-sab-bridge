# Changelog

All notable changes are documented here. This project follows semantic
versioning for its own release tags, but the NZBGet compatibility profile is a
separate claim and remains provisional.

## 0.1.6 - 2026-10-24

Fixes the NZBClient History screen, which failed with "APIError 2".

- Root cause: the bridge validated GET parameter *names* and answered JSON-RPC
  error code 2 for any name outside a small allowlist. NZBGet v21.1 consumes
  GET values *positionally* and ignores parameter names
  (`daemon/remote/XmlRpc.cpp`), so the same request succeeds against a real
  NZBGet. NZBClient sends one history GET argument whose name the bridge
  rejected, so the request failed before SAB was contacted.
- GET arguments are now parsed the way NZBGet parses them: the first query
  value is the `Hidden` flag for `history` and the log-entry count for
  `listgroups`, parameter names are not validated, and an unparseable value
  degrades to the method default instead of failing the request.
- `Hidden=true` still returns an explicit unsupported-hidden error, and POST
  positional arguments remain strictly validated.
- Added regression tests for the NZBGet GET contract at the unit and HTTP
  levels, including the argument-name cases that previously produced code 2.

## 0.1.5 - 2026-10-24

History GET-query compatibility patch.

- History GET requests now accept opaque cache-buster values, boolean
  `Hidden=true|false|1|0`, optional `Limit`/`NumberOfLogEntries`,
  `Start`/`Offset`, and `Search` arguments. Limits and offsets are bounded by
  the existing 500-item ceiling; search is forwarded to SAB history.
- Unknown history query arguments still return an explicit code 2; they are not
  silently ignored.
- Added safe diagnostics for the GET RPC error code and query argument count,
  without logging query names or values.
- Added regression coverage for the app-style GET query form, cache busters,
  pagination, search, unknown arguments, and redaction.

## 0.1.4 - 2026-10-24

History compatibility patch for NZBClient.

- Normalized SAB scan/parse failures to the generic `FAILURE/HEALTH` history
  status so the client does not receive a failure subtype it cannot reliably
  render. Records are never relabeled as unpack failures or dropped.
- Preserved zero-byte final-history records with their real sizes; added
  bounded HTTP-level coverage for 500 mixed completed/failed history items.
- Added safe history stage diagnostics: SAB/final slot counts, SAB status
  counts, and zero-byte slot counts without names, IDs, URLs, paths, or raw
  backend text.
- Added regression coverage for empty history, scan failures, malformed
  backend records, backend failures, queue-to-history NZBID continuity, and
  history immediately after both queue-delete variants.

## 0.1.3 - 2026-10-23

Release-workflow-only patch; no RPC behavior changes beyond 0.1.2.

- Fixed metadata-based image publication so it cannot auto-push `latest`.
- `latest` now advances only through the version-gated, serialized promotion
  job after published-image smoke verification.

## 0.1.2 - 2026-10-23

Queue deletion support and release-promotion hardening.

- Added `editqueue GroupDelete` as a queue-removal safe subset using SAB
  `del_files=0`; no history record is created.
- Added `editqueue GroupFinalDelete` using SAB `del_files=1`; no history record.
- Added complete paginated queue lookup for delete validation, all-target
  prevalidation, immediate pre-write rechecks, affected-ID validation, partial
  reporting, and bounded timeout reconciliation.
- Preserved NZBID mappings for stale references and history continuity.
- Added tests for deletion, history/file flags, pagination beyond 500 items,
  duplicate/invalid/stale targets, jobs moving out of the queue, rejection,
  affected-ID mismatch, timeout, reconciliation failure, and no retry.
- Enforced release-tag immutability with a Git tag ruleset and image-tag guard.
- Added version-gated, serialized `latest` promotion.

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
