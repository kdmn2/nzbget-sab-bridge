# nzbget-sab-bridge

An early compatibility bridge that exposes a limited NZBGet-style JSON-RPC API
in front of SABnzbd, primarily for clients built around the NZBGet protocol.

> **Early release.** The project is not a full NZBGet emulator and is not
> endorsed by NZBGet, SABnzbd, NZBClient, or Unraid. The compatibility profile is
> provisional. Actual NZBClient upload behavior has not been verified.

The bridge has **no web UI**. It is an API service for a trusted LAN, VPN, or
TLS reverse proxy.

## What works

- Bridge-mode JSON-RPC and read-only JSON-P-RPC endpoints.
- Read-only `version`, `config`, `configtemplates`, `status`, `listgroups`, and
  final `history`.
- Global queue pause/resume aliases.
- A bridge-local mutation lock serializes mutations; external SAB clients can still race.
- Per-job pause/resume and category changes.
- Lossy per-job priority changes with paused-job safety checks.
- Absolute rate control with SAB read-back verification.
- Plain `.nzb` uploads and HTTP/HTTPS URL submissions through `append` and
  `appendurl`.
- Persistent SQLite mapping between bridge NZBIDs and SAB job IDs.
- Optional capture mode for protocol discovery; it is unsafe outside a trusted
  network and is documented separately.

See [docs/compatibility.md](docs/compatibility.md) for the detailed method
matrix and known gaps, and [docs/MIGRATION.md](docs/MIGRATION.md) for moving an
existing ID database from a VM to Unraid.

## Not implemented

- Full NZBGet emulation or XML-RPC bridge mode.
- `listfiles`, `loadlog`, structured logs, RSS, append archives, retry/delete,
  history mutations, or scheduled resume.
- Hidden/archived history.
- Separate post-processing or scan pause controls.
- Verified iOS upload/button compatibility.
- ARM64 images. The published image is currently `linux/amd64` only.

## Quick start with Docker Compose

Prerequisites: Docker and the Compose plugin.

```bash
cp .env.example .env
# Set NZBGET_USERNAME, NZBGET_PASSWORD, SABNZBD_URL, and SABNZBD_API_KEY.
# Keep SABNZBD_API_KEY secret.
docker compose -f docker-compose.example.yml up -d
curl -fsS http://127.0.0.1:6790/healthz
curl -fsS http://127.0.0.1:6790/readyz
```

`/healthz` and `/readyz` are intentionally unauthenticated and expose only
coarse component status. RPC endpoints require the configured Basic auth.

The example uses a named Docker volume for `/data`. For a bind mount, the
non-root runtime UID/GID is `10001:10001`; see [Data and permissions](#data-and-permissions).

The published image is:

```text
ghcr.io/kdmn2/nzbget-sab-bridge:latest
```

`latest` tracks the latest verified release and is not a stability promise.
Release tags use the canonical `vMAJOR.MINOR.PATCH` format. Images are published
with the matching `vX.Y.Z`, `X.Y.Z`, and `X.Y` tags for reproducible installs.

## Configuration

Copy [.env.example](.env.example) and edit it. Secrets may also be supplied with
`NAME_FILE` variables pointing at Docker secrets.

| Variable | Default | Notes |
|---|---|---|
| `MODE` | `bridge` | `bridge` or `capture` |
| `LISTEN_HOST` | `127.0.0.1` | Container image sets `0.0.0.0` |
| `LISTEN_PORT` | `6789` | Container port |
| `NZBGET_USERNAME` | empty | Required unless insecure mode is explicitly enabled |
| `NZBGET_PASSWORD` / `_FILE` | empty | Required unless insecure mode is explicitly enabled |
| `SABNZBD_URL` | empty | Required in bridge mode; reverse-proxy subpaths supported |
| `SABNZBD_API_KEY` / `_FILE` | empty | Required in bridge mode; full API key, not the NZB key |
| `DATA_DIR` | `/data` | SQLite ID map location |
| `ADVERTISED_NZBGET_VERSION` | `21.1` | Only supported compatibility profile |
| `REQUEST_MAX_BYTES` | `33554432` | Maximum incoming RPC body, including base64 and JSON overhead |
| `NZB_MAX_DECODED_BYTES` | `25165824` | Decoded `.nzb` ceiling; effective limit may be lower |
| `SAB_MAX_RESPONSE_BYTES` | `33554432` | Maximum buffered SABnzbd JSON response |
| `HTTP_CONNECT_TIMEOUT` | `5` | Seconds |
| `HTTP_READ_TIMEOUT` | `30` | Seconds |
| `HTTP_WRITE_TIMEOUT` | `30` | Seconds |
| `HTTP_POOL_TIMEOUT` | `5` | Seconds |
| `LOG_LEVEL` | `INFO` | `CRITICAL` … `DEBUG` |
| `LOG_FORMAT` | `json` | `json` or `human` |
| `TLS_VERIFY` | `true` | `false` logs a startup warning |
| `ALLOW_INSECURE_NO_AUTH` | `false` | Explicitly permits empty RPC credentials |
| `READYZ_CHECK_SAB` | `true` | Bridge-mode SAB connectivity check |
| `READYZ_TIMEOUT_SECONDS` | `2` | Bounded readiness timeout |

The effective decoded NZB upload limit is:

```text
min(NZB_MAX_DECODED_BYTES,
    floor((REQUEST_MAX_BYTES - 65536) / 4) * 3)
```

With the defaults, that is **25,116,672 bytes** (about 23.95 MiB), because the
body contains base64 plus JSON. To allow a full 24 MiB decoded NZB, set
`REQUEST_MAX_BYTES` to at least `34603008`.

## Data and permissions

The image runs as UID/GID `10001:10001`, not root. It does not implement
`PUID`/`PGID`.

For a named Docker volume, Docker initializes ownership from the image. For a
host bind mount, prepare the directory explicitly:

```bash
sudo mkdir -p /mnt/user/appdata/nzbget-sab-bridge
sudo chown 10001:10001 /mnt/user/appdata/nzbget-sab-bridge
sudo chmod 750 /mnt/user/appdata/nzbget-sab-bridge
```

Use the equivalent commands for your host. Do not use world-writable directories
or recursive chown of unrelated paths. Keep appdata on local storage; SQLite is
not reliable on network filesystems.

`/data/bridge.sqlite3` stores only integer IDs, opaque SAB job IDs, timestamps,
and coarse state. It contains no credentials or NZB content.

## Security

- Put the bridge on a trusted LAN or VPN. Do not publish it directly to the
  Internet.
- Use a TLS reverse proxy if traffic leaves a trusted network.
- The image drops capabilities, sets `no-new-privileges`, and uses a read-only
  root filesystem with `/tmp` on tmpfs in the supplied examples.
- Secrets are sent to SAB in POST form/multipart fields, never in URLs.
- Logs redact Authorization values, API keys, passwords, cookies, tokens,
  credentials in URLs, and NZB content.
- URL append submissions are passed to SAB; the bridge does not fetch them.
- Read [SECURITY.md](SECURITY.md) and [docs/SECURITY.md](docs/SECURITY.md).

Report vulnerabilities privately through the repository owner's GitHub
advisory/contact channel. Do not attach secrets or production captures.

## Unraid

The repository includes:

- [unraid/nzbget-sab-bridge.xml](unraid/nzbget-sab-bridge.xml)
- [docs/UNRAID.md](docs/UNRAID.md)
- An original icon at [assets/icon.svg](assets/icon.svg), with 256px and 512px
  PNG exports.

The template is prepared for manual installation; this project is **not** listed
in Community Applications and has not been submitted for CA approval.

## Reverse proxy and networking

Publish only the bridge port. A typical LAN setup maps host `6790` to container
`6789/TCP`. If using Caddy, nginx, Traefik, or another reverse proxy, pass
`Authorization` through unchanged and keep request-body limits at least as large
as `REQUEST_MAX_BYTES`.

Do not expose capture mode. It forwards real mutations to a backend.

## Development and checks

Python 3.12 is required. The repository pins runtime and development
dependencies.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy src tests
pytest -q
```

GitHub Actions runs Ruff, formatting checks, mypy, pytest, `pip-audit`, a
HIGH/CRITICAL container vulnerability scan, container smoke, Unraid
template/asset validation, and GHCR publication on `main` and version tags.
Pull requests never receive package-write credentials.

## Compatibility and limitations

The advertised profile is **NZBGet 21.1**, but this is a provisional
compatibility target, not a claim that every NZBGet client works. In particular:

- Priority mapping is lossy; SAB has only Low, Normal, High, and Force.
- Priority changes on individually paused jobs are rejected because SAB has no
  atomic priority-and-pause operation.
- AddPaused is sent in the initial SAB request. Only Normal priority is accepted
  with AddPaused; the resumed priority is category-derived, not preserved Normal.
- AddToTop, archives, non-default duplicate options, and nonempty post-processing
  parameters are rejected.
- Append returns a stable NZBID only for exactly one returned SAB job. Zero or
  multiple IDs, timeouts, malformed success responses, and post-acceptance
  ID-map failures are reported as accepted-but-unresolved or outcome unknown.
- Capture fixtures from real environments are intentionally not part of the
  public repository.

## License

The original project code is MIT licensed. See [LICENSE](LICENSE).
Third-party notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[licenses/](licenses/).

NZBGet, SABnzbd, NZBClient, Unraid, and other names are trademarks of their
respective owners. This project is independent and unaffiliated.
