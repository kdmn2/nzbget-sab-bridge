# Safe capture workflow

> **Capture mode is unsafe outside a trusted LAN or VPN.** It forwards real RPC
> requests—including pause, delete, retry, and add operations—to your existing
> NZBGet instance. It is not a sandbox. Use disposable NZBGet jobs and a
> disposable history/queue item for every destructive step.

Capture mode exists because the NZBClient app’s protocol and required fields are
not documented. It records **shape-only discovery metadata**, not raw request or
response bodies. The resulting fixtures are not complete contract fixtures; see
[compatibility.md](compatibility.md#what-shape-metadata-cannot-tell-us).

## What is forwarded

Only these paths are forwarded:

- `/jsonrpc`
- `/jsonprpc`
- `/xmlrpc` only when `CAPTURE_XMLRPC=true`

Requests go to one fixed origin, `CAPTURE_NZBGET_URL`, and the original bytes are
preserved. Redirects are refused; forwarded calls are never retried or cached.
The app’s incoming `Authorization` and cookies are not forwarded. The bridge
authenticates to NZBGet with `CAPTURE_NZBGET_USERNAME` /
`CAPTURE_NZBGET_PASSWORD` (or `_FILE`).

## Before you start

You need:

- Docker with Compose.
- A reachable existing NZBGet instance.
- A small disposable NZB file and a disposable category.
- The iOS app configured to point at the bridge, not at NZBGet.
- A trusted LAN or VPN. Do not publish this bridge to the Internet.

Create a shared Docker network if you do not already have one:

```bash
docker network create usenet 2>/dev/null || true
```

If your NZBGet runs in Docker, attach that existing container to the same
network; do not start NZBGet from this repository:

```bash
docker network connect usenet <your-nzbget-container-name>
```

### Backend URL choices

| NZBGet location | `CAPTURE_NZBGET_URL` example |
|---|---|
| Container on `usenet` | `http://nzbget:6789` |
| Another LAN host | `http://192.0.2.50:6789` |
| Reverse proxy subpath | `https://nzbget.example.lan/nzbget` |
| Inside a container, host service | Use the host’s LAN address, not `localhost` |

`localhost` inside the bridge container refers to the bridge container itself.

## 1. Prepare `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# App -> bridge credentials (required)
NZBGET_USERNAME=bridgeuser
NZBGET_PASSWORD=use-a-long-unique-password

# Existing NZBGet backend (separate credentials)
CAPTURE_NZBGET_URL=http://192.0.2.50:6789
CAPTURE_NZBGET_USERNAME=backenduser
CAPTURE_NZBGET_PASSWORD=use-another-unique-password
# CAPTURE_NZBGET_PASSWORD_FILE=/run/secrets/capture_nzbget_password

# Optional, default false. Capture-only XML forwarding; no XML translation.
CAPTURE_XMLRPC=false
```

Set `SABNZBD_API_KEY` to blank or omit it. Capture mode does **not** require a
SABnzbd key.

Prepare the fixture directory and make sure the container user can write it.
The capture Compose override runs as `CAPTURE_UID:CAPTURE_GID`, so use your host
identity:

```bash
mkdir -p tests/fixtures/captured
```

Do not use a world-writable directory for long-term storage. If you do not pass
`CAPTURE_UID`/`CAPTURE_GID`, the container defaults to `10001:10001`; the capture
directory must then be writable by that UID.

## 2. Start the capture stack

```bash
CAPTURE_UID=$(id -u) CAPTURE_GID=$(id -g) \
  docker compose -f docker-compose.yml -f docker-compose.capture.yml up -d --build
```

The override sets `MODE=capture`, uses `/tmp/data` (so the container can remain
read-only), and mounts `./tests/fixtures/captured` at `/captures`.

Check the process and readiness:

```bash
curl -fsS http://127.0.0.1:6790/healthz
curl -fsS -u 'bridgeuser:the-password-from-.env' \
  http://127.0.0.1:6790/readyz
```

Expected readiness components (capture mode):

```json
{"status":"ready","mode":"capture","components":{"capture":"ok"}}
```

Readiness checks the capture recorder and the configured NZBGet backend. It does
not check SABnzbd and does not require a SAB key. A `503` means the recorder
directory is unusable or the backend is unreachable/redirecting.

Watch the logs for safe startup status:

```bash
docker compose -f docker-compose.yml -f docker-compose.capture.yml logs -f bridge
```

Logs must never show Authorization values, passwords, API keys, cookies, NZB
content, or full indexer URLs. If you see one, stop and report it privately as
described in [SECURITY.md](SECURITY.md).

## 3. Configure NZBClient

In the app, add/edit an NZBGet server:

| Setting | Value |
|---|---|
| Host | Bridge LAN/VPN address |
| Port | `6790` |
| Username | `NZBGET_USERNAME` |
| Password | `NZBGET_PASSWORD` |
| HTTPS | Off for a trusted LAN, or use a TLS reverse proxy |
| SABnzbd URL/API key | Leave blank; the app talks to the bridge |

Do not point the app at the real NZBGet for this test. Connect once and confirm
the app reaches the bridge. The first observed request will be recorded as
sequence `000001` (filename timestamp prefix may vary).

## 4. Manual checklist

Perform these actions slowly so each call is recorded separately. Use only
disposable jobs; the bridge is forwarding to the real backend.

### Connection and read paths

- [ ] Add/select the bridge server and connect.
- [ ] Open the dashboard/status screen and wait for a refresh.
- [ ] Open the queue.
- [ ] Open job details for one disposable queue item.
- [ ] Open history.
- [ ] Open hidden/archived history if the app offers it.
- [ ] Open server details / version / disk-space screen.
- [ ] Open the log screen.
- [ ] Open the RSS screen.
- [ ] Use any search/filter controls on queue and history.

### Add paths

- [ ] Add a small disposable NZB at normal priority.
- [ ] Add a second disposable NZB paused.
- [ ] Add a disposable NZB with a category.
- [ ] Add a disposable NZB with a non-default priority.
- [ ] Add a disposable NZB with “add to top” if available.

### Queue mutations

- [ ] Pause the whole queue.
- [ ] Resume the whole queue.
- [ ] Pause one disposable job.
- [ ] Resume one disposable job.
- [ ] Change speed limit (try a clearly different value).
- [ ] Use timed pause, then let it resume or resume manually.
- [ ] Change a disposable job’s category.
- [ ] Change a disposable job’s priority.
- [ ] Reorder/top/bottom a disposable job.
- [ ] Delete one disposable queue item.

### History mutations

- [ ] Retry a failed disposable history item, or a disposable item you can
      safely retry. Do not retry valuable production history.
- [ ] Mark a disposable history item good/bad if the app offers it.
- [ ] Re-download a disposable history item.
- [ ] Re-postprocess a disposable history item if the app offers it.
- [ ] Delete a disposable history item.
- [ ] Delete a second disposable history item permanently if the app offers
      that distinction.

### Other

- [ ] Trigger any refresh that shows post-processing/verifying/repairing/
      extracting/moving states, if you have a disposable job for it.
- [ ] Open any push-notification setup screen that talks to the server. Note:
      push notifications may be app-specific and outside the RPC API.

If the app has a “test connection” button, use it before the full checklist.

## 5. Stop and collect

```bash
CAPTURE_UID=$(id -u) CAPTURE_GID=$(id -g) \
  docker compose -f docker-compose.yml -f docker-compose.capture.yml down
```

This does **not** delete the named `/data` volume. Do not add `-v`.

List the fixtures:

```bash
ls -l tests/fixtures/captured
```

Each file is a JSON discovery record containing:

- local sequence number and UTC timestamp;
- transport (`jsonrpc`, `jsonprpc`, or `xmlrpc`);
- HTTP method and approved RPC path;
- query **key names** only, never values;
- request method, parameter count/types, safe enums, body size, parse status;
- response status, content type, envelope/field names and types, safe error code;
- timing;
- `X-Bridge-Capture` outcome in the HTTP response and recorder logs.

Inspect a record:

```bash
python3 -m json.tool tests/fixtures/captured/0000000000000-000001-jsonrpc.json
```

Do not commit capture files. This public repository excludes the entire
`tests/fixtures/captured/` directory; keep real capture output local and review
it before sharing outside your environment.

### If `X-Bridge-Capture` is `failed`

The backend response was still returned; the bridge did **not** replay the
request. Check:

- `CAPTURE_DIR` is writable by `CAPTURE_UID:CAPTURE_GID`;
- `CAPTURE_MAX_RECORDS` / `CAPTURE_MAX_TOTAL_BYTES` are not exhausted;
- the disk has free space.

Fix the cause and repeat only the specific app action; do not repeat a write
blindly if you are unsure whether the backend applied it.

## 6. XML-RPC option

If the initial capture shows `/xmlrpc` requests (or the app’s connection fails
only because XML is not forwarded), restart with:

```dotenv
CAPTURE_XMLRPC=true
```

XML is forwarded byte-for-byte. The recorder may inspect it for shape metadata,
but it rejects DTD/entity declarations and uses bounded depth/node/text limits.
There is **no XML translation in bridge mode**.

## 7. What to send back

Provide the complete `tests/fixtures/captured/` set and a note containing:

- SABnzbd version (for the later live backend test, not from capture mode);
- NZBGet version;
- app version;
- which checklist actions succeeded/failed;
- any response header `X-Bridge-Capture: failed` events.

Do not send `.env`, secrets, raw NZB files, backend logs containing credentials,
or full response bodies. The shape metadata may be insufficient to derive exact
translation values; that is expected. See the limitations section in
[compatibility.md](compatibility.md#what-shape-metadata-cannot-tell-us).
