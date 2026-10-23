#!/usr/bin/env python3
"""Decide whether a release tag may advance the moving ``latest`` image tag.

The registry's ``latest`` image is the source of truth. Promotion is refused
when the current latest release is newer than the candidate, which protects
against out-of-order or older maintenance releases.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def version_key(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.fullmatch(tag.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def should_promote(current: str | None, candidate: str) -> bool:
    candidate_key = version_key(candidate)
    if candidate_key is None:
        raise ValueError("candidate must be a vMAJOR.MINOR.PATCH tag")
    if current is None:
        return True
    current_key = version_key(current)
    if current_key is None:
        # A non-semver latest is not a verified release; allow a release to
        # replace it, but never move from one release to an older one.
        return True
    return candidate_key > current_key


def _registry_parts(image: str) -> tuple[str, str]:
    if not image.startswith("ghcr.io/") or image.count("/") != 2:
        raise ValueError("image must be ghcr.io/OWNER/NAME")
    return "ghcr.io", image.removeprefix("ghcr.io/")


def _pull_token(repository: str) -> str:
    url = f"https://ghcr.io/token?scope=repository:{repository}:pull&service=ghcr.io"
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.load(response)
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("registry did not return a pull token")
    return token


def _get_json(url: str, token: str, accept: str | None = None) -> dict[str, object] | None:
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return payload if isinstance(payload, dict) else None


def latest_release(image: str) -> str | None:
    _, repository = _registry_parts(image)
    token = _pull_token(repository)
    accept = ", ".join(
        (
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        )
    )
    manifest = _get_json(
        f"https://ghcr.io/v2/{repository}/manifests/latest",
        token,
        accept,
    )
    if manifest is None:
        return None

    candidates: list[dict[str, object]] = []
    manifests = manifest.get("manifests")
    if isinstance(manifests, list):
        candidates = [item for item in manifests if isinstance(item, dict)]
        for item in candidates:
            platform = item.get("platform")
            if (
                isinstance(platform, dict)
                and platform.get("os") == "linux"
                and platform.get("architecture") == "amd64"
            ):
                digest = item.get("digest")
                if isinstance(digest, str):
                    selected = _get_json(
                        f"https://ghcr.io/v2/{repository}/manifests/{digest}",
                        token,
                        accept,
                    )
                    if selected is not None:
                        manifest = selected
                        break
        else:
            return None
    config = manifest.get("config")
    if not isinstance(config, dict):
        return None
    config_digest = config.get("digest")
    if not isinstance(config_digest, str):
        return None
    config_payload = _get_json(f"https://ghcr.io/v2/{repository}/blobs/{config_digest}", token)
    if config_payload is None:
        return None
    labels = config_payload.get("config")
    if not isinstance(labels, dict):
        return None
    label_map = labels.get("Labels")
    if not isinstance(label_map, dict):
        return None
    version = label_map.get("org.opencontainers.image.version")
    return version if isinstance(version, str) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="ghcr.io/OWNER/NAME")
    parser.add_argument("--candidate", required=True, help="release tag, e.g. v0.1.2")
    args = parser.parse_args()

    current = latest_release(args.image)
    promote = should_promote(current, args.candidate)
    print(f"current_latest={current or 'none'}")
    print(f"candidate={args.candidate}")
    print(f"promote={'true' if promote else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
