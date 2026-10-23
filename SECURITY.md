# Security policy

This is an early, security-sensitive compatibility bridge. Do not expose its
RPC port directly to the public Internet. Use a trusted LAN, WireGuard/Tailscale
style VPN, or a TLS reverse proxy with host firewalling.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the repository
owner's GitHub security advisory/contact channel. Do not open a public issue
containing secrets, real captures, credentials, URLs, NZB content, or logs from
a production downloader.

Include:

- a short description and impact;
- the bridge version or commit;
- reproduction steps using fake credentials and isolated fake backends;
- whether the issue affects bridge mode, capture mode, or both.

Do not test against other people's systems or production downloaders. Do not
attach real `.env` files, Docker secrets, Authorization headers, cookies, NZB
files, or full response bodies.

CI runs `pip-audit` against the pinned runtime requirements and a Trivy
HIGH/CRITICAL container scan. Findings are triaged; fixes are not blindly
suppressed or upgraded without verification.

The full threat model and operational guidance are in
[docs/SECURITY.md](docs/SECURITY.md).
