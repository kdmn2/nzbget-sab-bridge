# Unraid installation

This project is an **API service**, not a dashboard application. The container
has no WebUI and the template intentionally does not advertise one.

The supplied template is prepared for manual installation. This project is not
listed in Community Applications and has not been submitted for CA approval.

## Files

- Template:
  [unraid/nzbget-sab-bridge.xml](../unraid/nzbget-sab-bridge.xml)
- Raw template:
  `https://raw.githubusercontent.com/kdmn2/nzbget-sab-bridge/main/unraid/nzbget-sab-bridge.xml`
- Icon:
  `https://raw.githubusercontent.com/kdmn2/nzbget-sab-bridge/main/assets/icon-512.png`
- Image:
  `ghcr.io/kdmn2/nzbget-sab-bridge:latest`
- VM-to-Unraid ID migration and backup/restore:
  [MIGRATION.md](MIGRATION.md)

## Appdata permissions

The image runs as UID/GID `10001:10001`. It does **not** implement `PUID` or
`PGID`, and it should not be run as root just to avoid a permission setup step.

Create and prepare the appdata directory on the Unraid host:

```bash
mkdir -p /mnt/user/appdata/nzbget-sab-bridge
chown 10001:10001 /mnt/user/appdata/nzbget-sab-bridge
chmod 750 /mnt/user/appdata/nzbget-sab-bridge
```

Keep this on local appdata storage. Do not place the SQLite database on a
network share. Do not use `chmod 777` or recursive chown of unrelated paths.

The container writes `/data/bridge.sqlite3`, its WAL/SHM files, and nothing else
outside `/tmp`. A fresh host bind mount must be prepared before first start.

## Install manually

1. Download or copy the template to the Unraid flash drive:

   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user
   install -m 600 nzbget-sab-bridge.xml \
     /boot/config/plugins/dockerMan/templates-user/my-nzbget-sab-bridge.xml
   ```

   The `my-<Name>.xml` filename is what dockerMan uses for user templates.

2. In the Unraid web UI, open **Docker**, choose **Add Container**, and select
   the saved template from the template selector. Review every mapping before
   starting it.

3. Set at least:

   - **SABnzbd URL** — a URL reachable from inside the container, for example
     `http://sabnzbd:8080` or a reverse-proxy URL. Do not put credentials in the
     URL.
   - **SABnzbd API key** — the full API key, not the NZB key.
   - **Bridge username** and **Bridge password** — credentials the client uses
     for the bridge, not SABnzbd credentials.
   - **Appdata** — `/mnt/user/appdata/nzbget-sab-bridge`.
   - **API port** — host port `6790`, container port `6789`.

4. Keep **Mode** at `bridge`. Capture mode is for isolated development only and
   is not configured by this template.

5. Apply the template and check:

   ```bash
   curl -fsS http://127.0.0.1:6790/healthz
   curl -fsS http://127.0.0.1:6790/readyz
   ```

   `readyz` should report storage and SAB components as `ok`. It is normal for a
   first start to fail readiness until `SABNZBD_URL` and the API key are valid.

6. Point the client at the Unraid host and port `6790` using the bridge
   username/password. Do not point it directly at SABnzbd.

## Security flags

The template requests:

- read-only container root filesystem;
- `/tmp` as a small tmpfs;
- `no-new-privileges`;
- all Linux capabilities dropped;
- non-root runtime UID/GID `10001:10001`;
- bridge networking and a single published API port.

These were tested with the same `docker run` flags used by the template and a
fresh host bind mount. They do not require privileged mode.

Masked template fields hide values in the Unraid UI; they do not encrypt the
container environment. Host administrators can inspect container configuration
and should protect access to the Unraid host and Docker daemon.

## No WebUI

The bridge has no `/` dashboard. Do not add a WebUI link that implies one. The
health and readiness endpoints are:

- `http://HOST:6790/healthz`
- `http://HOST:6790/readyz`

The NZBGet-compatible RPC endpoint is:

- `http://HOST:6790/jsonrpc/`
- `http://HOST:6790/jsonprpc`

## Updating

The template uses the moving `:latest` tag so Unraid can detect updates. Pin a
versioned tag such as `:0.1.1` if you prefer reproducible upgrades. Preserve the
same `/data` appdata path when recreating the container.

## Removing

Stop and remove the container through the Unraid Docker UI. Remove the appdata
directory only if you intentionally want to discard the bridge ID map.

## Troubleshooting

| Symptom | Check |
|---|---|
| Container restarts or exits at startup | Read the safe configuration error in the container log; no secrets are printed |
| `/readyz` reports storage error | Appdata is not writable by UID/GID `10001:10001` |
| `/readyz` reports SAB error | `SABNZBD_URL` is unreachable from the container, the API key is wrong, or TLS verification is failing |
| Client gets `401` | The client is using the bridge username/password, not SAB credentials |
| Upload returns `413` | Increase `REQUEST_MAX_BYTES` and, if needed, `NZB_MAX_DECODED_BYTES` |
| A feature fails | Check the compatibility matrix; unsupported methods return explicit errors |

Do not expose port `6790` directly to the public Internet. Use a trusted LAN,
WireGuard/Tailscale-style VPN, or a TLS reverse proxy with host firewalling.
