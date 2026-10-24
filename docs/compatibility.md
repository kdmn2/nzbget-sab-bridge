# Compatibility notes

> **NZBClient traffic was captured against a real NZBGet backend. Bridge-mode
> read-only checks and limited queue-control checks passed against SABnzbd
> 5.1.3.** This document records observed app traffic, documented API contracts,
> and current implementation status. It is not a claim of full compatibility; the real app
> has not yet been smoke-tested against the bridge. Per-job pause/resume,
> priority, and rate behavior is covered by stateful fake-backend HTTP tests, not
> live mutations.

## Sources and version assumptions

| Source | Version/date used | Purpose |
|---|---|---|
| [NZBGet API reference](https://nzbget.com/documentation/api/) | v13+ docs, current | Official method contracts |
| [NZBGet `docs/api/API.md` v26.3](https://github.com/nzbgetcom/nzbget/blob/v26.3/docs/api/API.md) | v26.3 (2026-08-27) | API overview and protocol rules |
| [NZBGet `XmlRpc.cpp` v26.3](https://github.com/nzbgetcom/nzbget/blob/v26.3/daemon/remote/XmlRpc.cpp) | v26.3 | JSON/JSONP envelopes, error codes, parameter parsing |
| [NZBGet `XmlRpc.cpp` v21.1](https://github.com/nzbgetcom/nzbget/blob/v21.1/daemon/remote/XmlRpc.cpp) | v21.1 | Advertised-profile append signature and URL/file behavior |
| [NZBGet `WebServer.cpp` v26.3](https://github.com/nzbgetcom/nzbget/blob/v26.3/daemon/remote/WebServer.cpp) | v26.3 | HTTP auth, status codes, RPC paths |
| [SABnzbd API reference 5.1](https://sabnzbd.org/wiki/configuration/5.1/api) | 5.1.x docs (stable 5.1.3) | Official SAB API behavior |
| [SABnzbd `api.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/api.py) | 5.1.3 (2026-09-08) | Response envelopes and command handling |
| [SABnzbd `constants.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/constants.py) | 5.1.3 | Status and priority constants |
| [SABnzbd `nzbqueue.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/nzbqueue.py) | 5.1.3 | Per-job pause/resume/priority API behavior |
| [SABnzbd `nzb/object.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/nzb/object.py) | 5.1.3 | Pause/resume and priority state transitions |
| [SABnzbd `downloader.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/downloader.py) | 5.1.3 | Speed-limit parsing and storage |
| [SABnzbd `misc.py` 5.1.3](https://github.com/sabnzbd/sabnzbd/blob/5.1.3/sabnzbd/misc.py) | 5.1.3 | Unit parsing (K/M/G in powers of 1024) |
| [NZBClient App Store](https://apps.apple.com/us/app/nzbclient-for-nzbget/id1178245637) | Public listing | App claims NZBGet v13+ and lists features |
| [NZBClient What’s New](https://www.nzbclient.app/whats-new) | 2026.7 | Public feature changes |

### Version assumptions

- **NZBGet:** documented as v13+; general source inspected at v26.3 and the
  advertised-profile append signature inspected at **v21.1**. The provisional
  advertised version is **21.1**, because it is a stable v13+ baseline with the
  v16 append field list (with practical optional trailing fields) and v18
  editqueue signatures. It is not proven against the app.
- **SABnzbd:** documentation/test-fixture target is **5.1.3**. A bounded,
  read-only live check detected backend version **5.1.3** and matched queue and
  final-history counts. One disposable paused job was used earlier for a live
  category-change check. No live per-job pause/resume, priority, rate, append,
  or NZBClient bridge smoke test has been run.
- **NZBClient:** observed using JSON-RPC over GET `/jsonrpc/<method>` and
  POST `/jsonrpc/`. No XML-RPC or JSON-P-RPC traffic was observed.

## What the bridge implements

| Capability | Status |
|---|---|
| `/jsonrpc`, `/jsonprpc` transport and NZBGet envelopes | Implemented |
| JSON-P callback parsing | Implemented; JSON-P is read-only |
| Bridge-mode GET `/jsonrpc/<method>` | Implemented for observed read-only forms |
| Bridge-mode POST `/jsonrpc/` without redirect | Implemented; mutations are serialized by a bridge-local lock |
| `version` in bridge mode | Implemented, returns `21.1` profile |
| `config` | Implemented as minimal category names only |
| `configtemplates` | Implemented as an empty local list; no SAB equivalent |
| `status` | Implemented from SAB queue + status + server stats |
| `listgroups` | Implemented from SAB queue |
| `history` | Implemented for final Completed/Failed SAB history |
| Persistent SQLite NZBID map | Implemented and used by listgroups/history and append |
| Explicit unsupported-operation errors | Implemented |
| Capture pass-through and shape recorder | Implemented |
| `listfiles`, `loadlog`, `log`, RSS | **Deferred/unsupported** |
| Global queue `pausedownload` / `resumedownload` | Implemented; source-confirmed `pause`/`resume` aliases |
| `editqueue GroupPause` / `GroupResume` | Implemented for currently queued items; per-job state, separate from global pause |
| `editqueue GroupSetPriority` | Implemented with lossy SAB priority mapping; rejects changes that would clear an individual pause |
| `editqueue GroupSetCategory` | Implemented for currently queued items |
| `editqueue GroupDelete` | Queue-only safe subset; SAB queue removal without final file deletion or history creation |
| `editqueue GroupFinalDelete` | Queue removal with SAB `del_files=1`; no history record |
| `rate` | Implemented in NZBGet KB/s with SAB absolute `K` value and read-back |
| `append` / `appendurl` | Implemented with the 21.1 practical 8–10 argument forms; plain NZB files and HTTP/HTTPS URLs |
| Other editqueue commands, retry, history deletion | **Unsupported in this milestone** |
| Hidden/archive history | **Unsupported; explicit error** |

### GET arguments and mutation reconciliation

NZBGet's GET form is `/jsonrpc/<method>?name=value`. Supported bridge GET
arguments are:

- `history?Hidden=true|false` — `true` returns an explicit unsupported-hidden
  error, `false` is accepted.
- `listgroups?NumberOfLogEntries=<non-negative int>` — accepted; the deprecated
  log-entry count is ignored.
- no semantic arguments for `version`, `config`, `configtemplates`, or
  `status`.

Conventional numeric cache-busting names (`_`, `t`, `timestamp`,
`cachebuster`, `cache_bust`, `cache-bust`) are tolerated. Other unknown
arguments return an explicit JSON-RPC parameter error. Query values are never
logged.

Queue and rate mutation timeout reconciliation reads SAB state. A matching
read-back proves the **resulting state**, not causality: another actor or the
backend may have produced that state. If reconciliation cannot be read, the
request returns an explicit “outcome unknown” error. Append submissions are not
reconciled by filename or other nonunique attributes; a timeout or malformed
success response is reported as outcome unknown. Mutations are never replayed
automatically.

All global pause aliases (`pausedownload`, `pause`, `pausedownload2` and their
resume counterparts) map to SAB's single global paused state. The bridge does
**not** implement separate post-processing or scan pause controls; all aliases
are the same global switch. Per-job `editqueue GroupPause`/`GroupResume` is
separate and does not change the global pause state.

### Per-job pause/resume

`editqueue GroupPause` and `GroupResume` use SAB `mode=queue&name=pause|resume`
with one validated queue `nzo_id` at a time. The v18 positional signature is
`(Command, Param, IDs)`; `Param` is ignored for these two commands.

Source inspection of SABnzbd 5.1.3 confirms that interactive pause/resume
changes the job `status` only; it does not rewrite the priority. A per-job
resume therefore restores the same priority the job had before it was paused.
Pausing a `Force`-priority job is rejected because SAB's forced priority
bypasses the paused status and could continue downloading.

### Queue deletion

The bridge supports `GroupDelete` and `GroupFinalDelete` only for IDs that are
currently in the SAB queue. No `value=all`, category search, filename search, or
empty-target deletion is used. The workflow uses the existing mutation lock and
a complete paginated queue read, not only the first 500 slots.

The exact command sent by the live iOS client was not captured. No pre-existing
job was deleted during verification. The implementation follows the documented
NZBGet 21.1 contracts and is tested against a stateful fake SAB backend.

| NZBGet command | SAB operation | Queue | Files | SAB history |
|---|---|---|---|---|
| `GroupDelete` | `queue&name=delete&del_files=0` | Removed | SAB removes partial/admin files but keeps the job folder; final completed files are not deleted by this path | No history record is created |
| `GroupFinalDelete` | `queue&name=delete&del_files=1` | Removed | SAB removes the job's incomplete download directory recursively | No history record is created |

NZBGet `GroupDelete` normally retains a history record, but SAB has no safe API
to add a removed queue item to history. The bridge therefore implements a
documented **queue-removal safe subset** for `GroupDelete`: it never deletes
history and never falls back to history deletion. If a target has already left
the queue, the bridge rejects the request before writing.

`GroupFinalDelete` maps more closely to SAB's no-history queue deletion, but it
still removes only the SAB queue/incomplete data path; it is not a general
filesystem deletion API. Post-processing jobs are not in the SAB queue and are
not targeted by these commands. The bridge never deletes arbitrary filesystem
paths.

Every request resolves NZBIDs through the persistent map and preserves those
mappings for stale-reference/history continuity. SAB's affected-ID response is
validated against the requested set; mismatches are not reported as success.
Queue absence is verified with an independent bounded read. A matching absence
proves the resulting state, not causality. Timeouts are never replayed, and
external SAB clients can still race the bridge.

### Priority mapping and paused-job safety

NZBGet's priority scale is wider than SAB's four levels. `GroupSetPriority`
accepts an integer `Param` string and maps it lossily:

| NZBGet priority | SAB level sent | Resulting `listgroups` priority |
|---|---|---|
| any value <= -1 (including -100, -50) | `-1` Low | `-50` |
| `0` | `0` Normal | `0` |
| `1` through `899` (including 50 and 100) | `1` High | `50` |
| `900` or greater | `2` Force | `900` |

Very low/low and high/very-high are collapsed; NZBGet `100` is **not**
promoted to SAB Force. The read-only reverse mapping has the same loss.

SAB 5.1.3 source confirms that its priority API is **not** atomic with pause:
`NzbQueue.__set_priority` changes the priority and, for a job whose status is
`Paused`, sets the status to `Queued` before returning. No official SAB API
operation combines priority and pause. Re-issuing a pause would therefore leave
a window in which an individually paused job is runnable and could start
downloading.

The bridge consequently rejects `GroupSetPriority` for an individually paused
job whenever the target SAB level differs from its current level, before
issuing any priority write. The error asks the caller to resume the job first.
A request that also targets unpaused jobs is rejected as a whole, so no target
is partially written. Changing priority to the same SAB level is a safe no-op
and is accepted without a write. The bridge does not temporarily pause the
global queue and does not depend on the global queue already being paused.

SAB reports individual job status separately from the global pause flag.
Changing an unpaused job's priority is therefore allowed while global pause is
active, and the global pause flag is never changed by `GroupSetPriority`.
Setting **Force** priority on an unpaused job is also allowed, but it has a real
behavioral consequence: SAB force priority bypasses global pause, and the job
can start downloading even though the queue remains globally paused. Force is
still rejected for individually paused jobs.

### Rate limit

`rate(Limit)` takes NZBGet's absolute KB/s integer and sends SAB an absolute
`<Limit>K` value, never a percentage. SAB parses `K` as 1024 bytes/second, so
`rate(400)` is expected to produce `speedlimit_abs = 409600`. `rate(0)` sends
`0`, SAB's unlimited state, and the expected `DownloadLimit` is `0`.

After a write, the bridge reads `status.speedlimit_abs` and compares it with
`Limit * 1024`. A mismatch is an explicit error; an unreadable status is
reported as outcome unknown. `status.DownloadLimit` is the same absolute
bytes/second value. NZBGet negative and non-integer values are rejected before
any SAB write. Very small nonzero NZBGet rates remain absolute because the
bridge always includes the `K` suffix.

### Append / NZB submission

The published NZBGet v16/21.1 signature lists:

```text
int append(string NZBFilename, string NZBContent, string Category, int Priority,
           bool AddToTop, bool AddPaused, string DupeKey, int DupeScore,
           string DupeMode, struct[] Parameters)
```

The actual v21.1 parser treats `Priority` as optional and consumes trailing
post-processing parameter pairs until none remain. The bridge therefore accepts
the practical **8-, 9-, and 10-argument forms**:

- 8: no Priority and no trailing PP slot
- 9: Priority present without PP slot, or Priority absent with one empty PP slot
- 10: published form with Priority and an empty PP slot

Nonempty PP parameters are rejected. The bridge does not require ten arguments
solely because the published signature lists ten.

`appendurl` is mapped to the **same `DownloadXmlCommand`** as `append` in the
v21.1 source; there is no separate legacy parameter list to implement there.
Both methods support POST `/jsonrpc/` and POST `/jsonrpc` without redirects.

File submissions use base64-encoded **plain `.nzb`** content and SAB
`mode=addfile` multipart upload. The bridge performs strict base64 decoding and
a bounded XML validation before submission. The validator accepts standard XML
declarations, leading BOMs, comments, and default or prefixed NZB namespaces. It
rejects malformed XML, non-`<nzb>` roots, empty NZBs, DTD/entity declarations,
and external entities. It does not resolve entities or fetch external resources.
Compressed payloads and archive filenames are rejected; no archive extraction or
local file access exists.

URL submissions use SAB `mode=addurl`; the bridge never fetches the URL. An
empty `NZBFilename` is accepted for URLs, matching NZBGet's documented behavior
of deriving the name from HTTP headers. Only HTTP and HTTPS URLs with a host are
accepted. URLs, embedded credentials, query strings, and NZB payloads are never
logged by the bridge. A successful add is acceptance only; URL fetching and
eventual download success are separate SAB states and may later fail in history.

Categories use the existing SAB category validation and `*` default marker.
Priority uses the documented lossy mapping. `AddPaused=true` is sent as SAB
`priority=-2` in the initial add request; the bridge never adds first and then
pauses. SAB pauses the job before replacing the sentinel with the category's
configured priority, so the **initial state is paused even when the category
default is High or Force** (a Force category default is not restored). The
priority that appears after an explicit resume is the category priority, not
the requested NZBGet Normal priority. The bridge accepts only Normal priority
with AddPaused because other requested priorities cannot be represented; this
is an approximation, not preservation of Normal. Any other priority with
AddPaused is rejected before the SAB write, and Force can never silently defeat
AddPaused.

`AddToTop=true` is rejected: SAB has no add-time top-insertion option, and
post-submission queue manipulation would not be atomic. `AddToTop=false` is
accepted. `DupeKey`, nonzero `DupeScore`, `DupeMode` values other than `SCORE`,
and nonempty post-processing `Parameters` are rejected rather than discarded.

When SAB returns exactly one job ID, the bridge persists it in the ID map and
returns the stable positive NZBID. Zero or multiple IDs produce an explicit
accepted-but-unresolved error. A write timeout or malformed success response is
reported as outcome unknown, never retried, and never resolved by guessing from
a filename. If the SAB job is accepted but the ID map cannot persist the
mapping, the error also says the submission was accepted and remains
unresolved.

### Append body-size limit

`REQUEST_MAX_BYTES` is the hard HTTP body limit and includes the JSON-RPC
envelope and base64 expansion. `NZB_MAX_DECODED_BYTES` is a ceiling on decoded
NZB bytes. The effective decoded limit is:

```text
min(NZB_MAX_DECODED_BYTES,
    floor((REQUEST_MAX_BYTES - 65536) / 4) * 3)
```

The 65536-byte allowance covers the JSON envelope and small fields. With the
defaults `REQUEST_MAX_BYTES=33554432` and `NZB_MAX_DECODED_BYTES=25165824`, the
effective upload limit is **25,116,672 decoded bytes** (about 23.95 MiB), not
the full 24 MiB ceiling. The bridge logs a startup warning when the body limit
reduces the configured decoded limit. To accept a full 24 MiB decoded NZB,
raise `REQUEST_MAX_BYTES` to at least `34603008` (33 MiB).

### Future speed-limit smoke-test safety

A future manual speed-limit test must record the original backend limit before
changing it and restore that exact setting afterward. Record
`status.speedlimit_abs` (bytes/second) and, where relevant, the original SAB
`speedlimit` percentage. Do not use `rate(0)` as cleanup unless the original
setting was exactly unlimited (`speedlimit_abs == 0`). If the original limit
came from a SAB percentage and is not exactly representable as integer KB/s,
restore it through the original SAB setting rather than approximating it with
the bridge. Confirm the restored value with a status read.

### Live queue-control verification

A single disposable paused SAB job was used for category-change verification
with categories whose configured scripts were default/none (no scripts were
available) and empty post-processing options. The category was changed through
the bridge, independently verified in SAB, restored, and the exact job was
removed afterward. The job remained paused throughout.

Live global pause/resume was intentionally skipped because SAB already had a
pre-existing queue job; the original paused state was preserved. Live
per-job pause/resume, priority changes, rate changes, append submissions, and
queue deletions were also skipped. Those operations were exercised only through
a stateful fake SAB backend over real HTTP to the bridge. Queue deletion was not
performed against the live SAB queue because no test job could be guaranteed to
avoid category/pre-queue processing. The live SAB queue remained paused with its
existing job untouched.

## Observed NZBClient traffic

The app was exercised through the capture proxy against the real NZBGet
backend. Ninety-three shape-only fixtures were recorded. The app used JSON-RPC
only:

- `GET /jsonrpc/version`
- `GET /jsonrpc/config`
- `GET /jsonrpc/history`
- `POST /jsonrpc/` with a JSON body for `version`, `config`,
  `configtemplates`, `listgroups`, and `status`

Observed request details:

- GET requests had an empty body and no query string.
- POST requests used positional parameters; all observed POST methods sent an
  empty parameter array (`param_count=0`).
- `listgroups` returned one queue item.
- `history` returned 25 final-history items.
- `config` returned 377 `{Name, Value}` option records, but option names and
  values were not recorded.
- `configtemplates` returned one `{Template: string}` item from NZBGet.
- `status` returned the full documented status field set.
- No `listfiles`, `loadlog`, batch, or `system.multicall` traffic was observed.
- A non-fatal `GET /` 404 remains; no synthetic root response was added.
- The app had previously emitted `POST /jsonrpc/` 307 redirects. The explicit
  bridge/capture route now handles that path directly.

The controlled requests were only for transport verification; all app traffic
is distinguishable by sequence/time and is documented in the capture review.

## Method matrix

Classification uses: **native mapping**, **approximation**, **local emulation**,
**deferred**, **unsupported**.

| Method / feature | Status | Notes |
|---|---|---|
| `version` | native mapping | Advertised profile; capture passes backend value unchanged |
| `status` | approximation | SAB queue/status/server_stats; downloaded-since-start, post jobs, and some counters are approximated as zero |
| `listgroups` | approximation | SAB queue slots; IDs from the persistent map; PP history merge not implemented |
| `history` | approximation | Final Completed/Failed only; hidden/archive unsupported; URL blanked; safe failure subtypes inferred; scan/parse failures use generic health failure |
| `config` | local emulation | Category names only; additional option requirements are an assumption |
| `configtemplates` | local emulation | Empty list; NZBGet extension/config templates have no SAB equivalent |
| `append` / `appendurl` | approximation | v21.1 practical 8–10 argument forms (optional Priority/trailing PP slot); file upload maps to SAB `addfile`; URL maps to `addurl`; empty URL filename accepted; AddToTop and non-default dupe/PP options are rejected; AddPaused is sent atomically as SAB paused priority |
| `pausedownload` / `resumedownload` | native mapping | SAB `mode=pause`/`resume`; source-confirmed aliases; uncertain timeouts reconciled with SAB status |
| `rate` | native mapping | KB/s to SAB absolute `K` value; status read-back and `DownloadLimit` round-trip |
| `scheduleresume` | unsupported | Out of scope |
| `editqueue GroupPause` / `GroupResume` | native mapping | Queue-only IDs; SAB per-job status pause/resume; priority retained; global pause separate |
| `editqueue GroupSetPriority` | approximation | Lossy four-level SAB mapping; priority changes that would clear an individual pause are rejected before writes; Force can bypass global pause for unpaused jobs; global pause is left untouched |
| `editqueue GroupSetCategory` | approximation | Queue-only IDs, SAB `get_cats` validation, `*` default marker; category scripts/PP are applied by SAB |
| `editqueue GroupDelete` | approximation | Queue-only safe subset; SAB `del_files=0`; no SAB history record; final files retained |
| `editqueue GroupFinalDelete` | native mapping | Queue-only removal with SAB `del_files=1`; SAB history is not created |
| `editqueue` other commands | unsupported | Explicit error, no writes |
| `listfiles` | deferred/unsupported | No observed app call; SAB `get_files` is not wired |
| `log` / `loadlog` | deferred/unsupported | SAB `showlog` is a text download, not structured entries |
| RSS methods | unsupported | No SAB equivalent confirmed |
| `sysinfo` / `systemhealth` / `servervolumes` | deferred/unsupported | Not observed |
| `shutdown` / `reload` / `saveconfig` | unsupported | Dangerous or out of scope |
| Push notifications | unsupported | Outside RPC/APIs |

JSON-P-RPC is restricted to the read-only methods; mutation methods return an explicit error without contacting SAB.

Implemented methods return real translated data. Known unsupported methods
return JSON-RPC error code 1 with an explicit message; malformed/unsupported
parameters return code 2; backend/storage failures return code 4. No mutation
is silently treated as successful.

### History status approximation

SAB `Failed` has no exact NZBGet failure subtype. The bridge uses an explicit
message/stage classifier:

- unpack/rar/password → `FAILURE/UNPACK`
- repair/par → `FAILURE/PAR`
- move/storage/destination → `FAILURE/MOVE`
- nzb/scan/parse → `FAILURE/HEALTH` (the generic failure status rendered by
  NZBClient; do not relabel these as unpack failures)
- otherwise → `FAILURE/HEALTH`

`ParStatus`, `UnpackStatus`, `MoveStatus`, and `ScriptStatus` are similarly
derived from stage names and the failure message. Zero-byte final records are
preserved rather than dropped; they remain visible with their real zero sizes.
This is an approximation, not a claim of exact equivalence. The bridge records
safe counts for the SAB/final history slot split and zero-byte slots so a
client-rendering failure can be distinguished from a backend failure.

### Known gaps

- Hidden/archived SAB history is not mapped to NZBGet hidden history.
- History URLs are blank to avoid propagating credential-bearing indexer URLs.
- Minimal `config` exposes only category names; the app may require more.
- `listgroups` does not merge SAB post-processing history items.
- `listfiles`/`loadlog` and job/history detail methods remain unavailable.
- Only final history is returned; PP states are not exposed in history.
- Queue/history response limits are bounded to 500 items. SAB JSON response bodies
are bounded by `SAB_MAX_RESPONSE_BYTES` (default 32 MiB).
- Priority mapping is lossy: SAB has no separate very-low or very-high levels.
- SAB has no atomic priority-and-pause operation. Priority changes for
  individually paused jobs are rejected unless the SAB level is unchanged.
- Force priority is refused for individually paused jobs because it would
  override the pause. For an unpaused job, Force can bypass global pause and
  start downloading.
- Append accepts only plain `.nzb` files and HTTP/HTTPS URLs. Archive uploads,
  AddToTop, non-default duplicate options, and nonempty post-processing
  parameters are unsupported.
- AddPaused with a non-Normal priority is rejected because SAB cannot represent
  both in one add request. Initial pause is guaranteed by SAB's `-2` sentinel,
  but the priority restored on resume is the category priority, not Normal.
- URL append confirms SAB acceptance, not eventual fetch or download success.
- The effective decoded upload size can be smaller than
  `NZB_MAX_DECODED_BYTES` because `REQUEST_MAX_BYTES` includes base64 and the
  JSON envelope.
- Mutations not listed above, including retry, history deletion, duplicate
  marking, and scheduled resume, remain unsupported.

## Observed NZBGet protocol details

These are from the official source, not app captures:

- JSON success envelope: `{"version":"1.1","id":...,"result":...}`.
- JSON error envelope: `{"version":"1.1","id":...,"error":{"name":"JSONRPCError","code":N,"message":"..."}}`.
- Error codes used by NZBGet: `1` invalid procedure, `2` invalid parameter,
  `3` invalid action, `4` parse/unsafe-GET/general, `401` access denied.
- JSON-RPC uses positional arrays only; named parameters are not supported.
- JSON-P-RPC callback is the first parameter; the response is wrapped as
  `callback(...)`.
- `append` v13 has 9 fields; v16 adds trailing `PPParameters`; v21.1 source
  treats `Priority` as optional and accepts trailing PP pairs until exhausted.
  The bridge accepts the practical 8–10 argument forms used by the `21.1`
  profile, not the newer `AutoCategory` form.
- `editqueue` v18 is `(Command, Param, IDs)`; earlier versions include `Offset`.
- `rate` takes KBytes/second; `status.DownloadLimit` is bytes/second.
- `scheduleresume` schedules a resume but does not pause; pause/resume cancels
  the timer.
- `config` returns `[{Name, Value}]` with no schema guarantee.

## Documented SABnzbd details (reference only)

- Queue/history/status responses are JSON envelopes under `queue`, `history`,
  or `status`.
- Queue priority values: `-2` paused, `-1` low, `0` normal, `1` high, `2` force,
  `-100` default. `-2` is an add-time sentinel; interactive per-job pause is
  represented by the queue slot status instead.
- Queue statuses include `Downloading`, `Paused`, `Queued`, `Propagating`,
  `Checking`, `Fetching`, `Grabbing`.
- History statuses include `Completed`, `Failed`, `Queued`, `QuickCheck`,
  `Verifying`, `Repairing`, `Extracting`, `Moving`, `Running`.
- `addfile` uses multipart form data and returns `nzo_ids`.
- History deletion archives by default; `archive=0` removes permanently.
- API key failures are usually HTTP 200 with `{"status": false, "error": ...}`;
  the full API key is required, not the NZB key.

The SAB client supports these calls and validates them with synthetic mocked
responses. The bridge translates queue, status, final history, and minimal config
into NZBGet responses.

## Deferred compatibility questions

These remain unresolved and must not be silently assumed:

1. **Failure classification.** SAB `Failed` does not directly encode NZBGet’s
   `FAILURE/PAR`, `FAILURE/UNPACK`, `FAILURE/MOVE`, `FAILURE/SCAN`, or
   `FAILURE/HEALTH`. The bridge uses a message/stage classifier and a
   `FAILURE/HEALTH` fallback. Scan/parse failures are intentionally presented
   as generic `FAILURE/HEALTH` because NZBClient does not reliably render the
   dedicated scan status; exact equivalence is not verified.
2. **Hidden history.** SAB archive is not semantically identical to NZBGet
   hidden `Kind=DUP` history. Hidden history is unsupported and returns an
   explicit error.
3. **Priority fidelity.** Read-only and write priority values use the documented
   four-level collapse. SAB cannot represent NZBGet's distinct very-low and
   very-high levels. Force-on-paused is intentionally refused. Priority changes
   on individually paused jobs are refused because SAB has no atomic
   priority-and-pause operation.
4. **Add-paused plus priority.** SAB uses `-2` as an add-time paused sentinel
   and then falls back to the category priority. The bridge never represents
   both AddPaused and a non-Normal priority; that combination is rejected.
5. **Post-processing placement.** SAB includes post-processing items in history;
   NZBGet shows them in `listgroups`. Merging is not implemented; only final
   history is returned.
6. **Queue delete history semantics.** SAB queue deletion does not create a
   history record; NZBGet `GroupDelete` normally does. The bridge implements
   queue removal for `GroupDelete` without creating or deleting history.
7. **ID reuse on retry.** SAB retry may create a new `nzo_id`; retry is out of
   scope for this milestone.
8. **Config fields.** The app may require options beyond category names.
   Capture records only the `config` response shape, not option names or values.
   Recording option names alone would not prove which options the app reads.
   Minimal-config requirements remain an explicit assumption to validate in the
   bridge smoke test.
9. **listfiles/loadlog, RSS, push notifications, detailed file state,
   mark-good/bad, and re-postprocess.** Not implemented or no safe equivalent.

## What shape metadata cannot tell us

Default capture intentionally records **no arbitrary string values, names,
paths, URLs, credentials, NZB content, cookies, or bodies**. That means the
fixtures are discovery metadata, not complete contract fixtures. Specifically,
they cannot reveal:

- Exact response values, including category names, status strings, paths,
  server names, error text, or file names.
- Exact numeric values in responses or their units (only types).
- Which values the app treats as meaningful for hidden/archived history,
  failure classification, or priority.
- Whether the app depends on undocumented fields that happen to be present.
- Whether the app displays or branches on specific error messages.
- Push-notification behavior, which is outside the RPC API.
- Authentication/protocol choices beyond the observed method, path, parameter
  count/types, and response shape.

They do reveal the **request sequence**, method names, transport, parameter
count/types, safe editqueue commands, response envelope/field names and types,
HTTP status, safe error codes, and timing. That is enough to identify the
protocol and method surface, but not enough to infer every translation rule.

Until a separate, reviewed sanitization process is agreed, translation tests
must use explicitly synthetic fixtures. If a specific missing value is needed,
it must be requested and reviewed separately rather than enabling broad capture.

## Related documents

- [Capture workflow](CAPTURE.md)
- [Security model](SECURITY.md)
- [README](../README.md)
