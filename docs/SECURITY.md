# Security model

## Scope and threat model

The bridge sits between an authenticated iOS app and one configured backend.
It does not proxy NNTP, parse Usenet articles, modify SABnzbd, or expose a web
UI. The primary assets are:

- the SABnzbd API key;
- the bridge’s client username/password;
- the capture backend’s NZBGet username/password;
- NZB file contents and indexer URLs;
- the persistent ID map and any capture metadata;
- integrity of the real downloader’s queue/history.

Primary threats considered:

- unauthorized access to the RPC endpoints;
- secret leakage through logs, exception strings, fixtures, or container config;
- SSRF/arbitrary forwarding from a malicious or compromised client;
- unintended writes or retries during capture;
- secret-bearing redirects;
- denial of service through oversized request bodies or unbounded capture files;
- running as root or with unnecessary container capabilities;
- loss or corruption of the ID map.

## Client authentication

- HTTP Basic auth is required unless `ALLOW_INSECURE_NO_AUTH=true` is explicitly
  set.
- Empty credentials and common defaults (`nzbget`, `tegbzn6789`, `admin`,
  `password`, `changeme`, etc.) are rejected at startup.
- Username and password are compared with constant-time comparisons; both fields
  are always compared.
- Failed auth returns an empty `401` with `WWW-Authenticate: Basic realm="NZBGet"`.
- Basic auth is not encryption. Use a trusted LAN, WireGuard, or TLS reverse
  proxy. Never send Basic auth over the public Internet in clear text.

## Secret handling

- Secrets may be supplied as `NAME` or `NAME_FILE` (`_FILE`). Supplying both is a
  configuration error.
- Secret files with group/other permissions produce a startup warning.
- Secret values are stored as Pydantic `SecretStr` and are not included in repr
  output or safe diagnostics.
- SABnzbd API calls use POST form fields and multipart uploads. The API key is
  never put in the URL. There is no silent fallback to URL credentials.
- The SABnzbd client disables redirects and does not retain the original
  transport exception, request URL, or response body in exceptions.
- Outbound HTTP clients set `trust_env=False` so environment proxy settings
  cannot silently receive credentials.
- Structured logging redacts Authorization/Basic/Bearer values, API keys,
  passwords, cookies, tokens, NZB parameters, and URL userinfo, and only emits a
  whitelist of safe extra fields.
- Authorization headers, request bodies, response bodies, and NZB content are
  never logged.
- Capture recordings are shape-only and never include raw bodies, names, paths,
  URLs, credentials, cookies, or NZB data.

## Capture-mode risks

Capture mode forwards requests to a real NZBGet instance. It can mutate the real
queue and history. Therefore:

- Capture mode must run only on a trusted LAN/VPN.
- Use disposable jobs, categories, and history entries for delete/retry tests.
- Capture does not authenticate the app to NZBGet with the app’s credentials; it
  uses the separately configured `CAPTURE_NZBGET_*` credentials.
- The incoming `Authorization` header and cookies are not forwarded.
- Backend `Set-Cookie`/`WWW-Authenticate` headers are stripped from responses so
  the app cannot be tricked into adopting backend credentials.
- Only fixed RPC paths are forwarded to one fixed origin. There is no general
  proxy endpoint, no user-supplied destination, and no redirect following.
- Forwarded calls are never retried. A recorder failure does not trigger a
  replay; the backend response is returned with `X-Bridge-Capture: failed` and
  the failure is logged safely.
- Response buffering has a configurable safety limit. Oversized responses are
  refused with a bridge error rather than silently truncated.
- Fixture output is bounded by record count, total bytes, and per-record bytes.
  Retention deletion is opt-in and only removes matching capture files.
- XML inspection rejects DTD/entity declarations and uses bounded parsing. XML
  bridge translation is not provided for XML-RPC.

## Exposure guidance

Recommended:

- WireGuard/Tailscale-style VPN, or a LAN-only published port.
- TLS reverse proxy on a private network, passing `Authorization` through.
- A host firewall restricting `6790` to trusted clients.
- `BRIDGE_BIND_IP` set to a LAN/VPN address; `127.0.0.1` when only a local reverse
  proxy needs access.

Avoid:

- publishing `6790` directly to the Internet;
- port-forwarding capture mode;
- running with `ALLOW_INSECURE_NO_AUTH=true` outside isolated development;
- disabling `TLS_VERIFY` for a remote SABnzbd/NZBGet endpoint;
- putting secrets in Compose command lines, images, Git, or logs;
- bind-mounting `/data` or captures on a world-writable directory.

## Container hardening

The supplied Compose file:

- runs the application as UID/GID `10001`;
- uses a read-only root filesystem with only `/tmp` on tmpfs;
- drops all Linux capabilities;
- sets `no-new-privileges`;
- persists only `/data` (and capture overrides mount `/captures`);
- uses a healthcheck that does not require authentication or expose details.

The image contains no compiler toolchain in the final stage and uses a pinned
Python base image digest. No analytics, telemetry exporter, or external
monitoring integration is configured by this project. The capture Compose
override may run as the host UID/GID so it can write the bind-mounted fixture
directory; it still has a read-only root filesystem and no added capabilities.

## Data at rest

- `/data/bridge.sqlite3` stores only integer IDs, opaque SAB `nzo_id` strings,
  timestamps, and coarse state. It contains no NZB content or credentials.
- SQLite file permissions depend on the container user/umask and host mount.
  Protect `/data` like application state.
- Capture files are mode `0600` when created, but the containing directory
  permissions are controlled by the operator. Review them before copying or
  committing.

## Reporting a vulnerability

Do not open a public issue containing secrets, raw captures, credentials, URLs,
or NZB content.

Report privately to the repository owner/maintainer through the private contact
channel used for this project. Include:

- a short description and impact;
- the bridge version/commit;
- reproduction steps using fake credentials and isolated fake backends;
- whether the issue affects capture mode, bridge mode, or both.

Do not test against production SABnzbd/NZBGet instances or other people’s
systems. Do not attach real `.env` files, Docker secrets, Authorization headers,
cookies, NZB files, or full response bodies.
