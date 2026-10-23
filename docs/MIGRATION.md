# Migrating the ID map from a VM to Unraid

The bridge persists one SQLite database at `/data/bridge.sqlite3`. This is the
only file needed for ID continuity. The file is created by the implementation,
not by the Compose file, and it may have `-wal`/`-shm` companions while running.

Do not copy the source bridge's runtime `.env`, secrets, or capture files into
the public repository or into this procedure. Configure credentials separately
on Unraid.

## 1. Stop the source bridge cleanly

Use the Compose project or Docker container name that actually exists:

```bash
docker compose -f docker-compose.yml stop bridge
# or: docker stop <source-container-name>
```

Do not use `docker compose down -v`; that deletes the named data volume.

Inspect the source mount before copying. Do not assume the volume name or host
path:

```bash
docker inspect <source-container-name> --format '{{json .Mounts}}'
```

The database filename inside the mount is `bridge.sqlite3`.

## 2. Back up the stopped source data

For a named volume, copy the entire `/data` directory while the container is
stopped. This keeps the database and any WAL/SHM companions together:

```bash
mkdir -p ./bridge-backup
docker run --rm \
  -v nzbget-sab-bridge_bridge_data:/data:ro \
  -v "$PWD/bridge-backup:/backup" \
  alpine:3.22 \
  sh -c 'cp -a /data/. /backup/'
```

For a bind mount, copy the host directory instead. Record the source container's
image/version and the backup location outside the public repository.

A stopped-container copy is sufficient. If you must back up while the bridge is
running, use SQLite's backup API rather than copying a live WAL database.

## 3. Transfer and prepare Unraid appdata

Create the target directory on the Unraid host and copy the database files from
the backup. Use a non-recursive ownership change for a fresh directory:

```bash
mkdir -p /mnt/user/appdata/nzbget-sab-bridge
cp /path/to/bridge-backup/bridge.sqlite3* \
  /mnt/user/appdata/nzbget-sab-bridge/
chown 10001:10001 /mnt/user/appdata/nzbget-sab-bridge
chown 10001:10001 /mnt/user/appdata/nzbget-sab-bridge/bridge.sqlite3*
chmod 750 /mnt/user/appdata/nzbget-sab-bridge
chmod 600 /mnt/user/appdata/nzbget-sab-bridge/bridge.sqlite3*
```

If the appdata directory already contains files from another install, inspect
them before changing ownership. Do not recursively chown unrelated paths.

## 4. Start the published image and verify ID continuity

Install the template from [UNRAID.md](UNRAID.md), point `/data` at
`/mnt/user/appdata/nzbget-sab-bridge`, and start the container.

Verify:

```bash
curl -fsS http://127.0.0.1:6790/healthz
curl -fsS http://127.0.0.1:6790/readyz
```

Then compare a known queue/history NZBID from the source with the same item on
the target. The integer IDs should be stable when the database was migrated.
`/readyz` is intentionally unauthenticated; RPC calls still require the bridge
Basic-auth credentials.

## 5. Roll back without touching SAB jobs

Stop the Unraid container, restore the previous appdata backup to the same
`/data` mount, correct ownership, and start the previous image/template. This
only changes the bridge's local ID map; it does not change live SAB queue or
history jobs.

## Fresh database behavior

If you intentionally start with an empty `/data`, the bridge creates a new
database and begins a new ID namespace at `1`. Existing SAB jobs are not changed,
but the client may see different NZBIDs until it refreshes. Back up the old
database before choosing this path.

## Backup and restore of a running Unraid install

For a routine backup, stop the container first and copy `/data` as above. For a
restore, stop the container, replace the appdata contents from the backup,
correct ownership, and start it again. Do not delete or recreate a damaged
database automatically; preserve it for inspection.
