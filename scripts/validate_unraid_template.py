#!/usr/bin/env python3
"""Validate the public Unraid template and original icon assets."""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "unraid" / "nzbget-sab-bridge.xml"
ICON_256 = ROOT / "assets" / "icon-256.png"
ICON_512 = ROOT / "assets" / "icon-512.png"
ICON_SVG = ROOT / "assets" / "icon.svg"
EXPECTED_RAW = "https://raw.githubusercontent.com/kdmn2/nzbget-sab-bridge/main/"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def text(root: ElementTree.Element, name: str) -> str:
    element = root.find(name)
    if element is None or not (element.text or "").strip():
        fail(f"missing or empty <{name}>")
    return (element.text or "").strip()


def png_dimensions(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.stat().st_size < 64:
        fail(f"missing or empty PNG: {path.relative_to(ROOT)}")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        fail(f"not a PNG: {path.relative_to(ROOT)}")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def main() -> int:
    if not TEMPLATE.is_file():
        fail("missing unraid/nzbget-sab-bridge.xml")
    # The input is a trusted repository asset, not user-supplied XML.
    root = ElementTree.parse(TEMPLATE).getroot()  # noqa: S314
    if root.tag != "Container" or root.attrib.get("version") != "2":
        fail('template must be <Container version="2">')

    required = {
        "Name",
        "Repository",
        "Registry",
        "Network",
        "Shell",
        "Support",
        "Project",
        "Overview",
        "TemplateURL",
        "Icon",
        "Category",
    }
    for name in sorted(required):
        value = text(root, name)
        if "YOUR_" in value or "example-app" in value:
            fail(f"placeholder in <{name}>")

    if text(root, "Name") != "nzbget-sab-bridge":
        fail("unexpected <Name>")
    if text(root, "Repository") != "ghcr.io/kdmn2/nzbget-sab-bridge:latest":
        fail("unexpected <Repository>")
    if text(root, "Network") != "bridge":
        fail("Network must be bridge")
    if text(root, "Shell") != "sh":
        fail("Shell must be sh")
    if text(root, "TemplateURL") != EXPECTED_RAW + "unraid/nzbget-sab-bridge.xml":
        fail("TemplateURL must be the public raw XML URL")
    if text(root, "Icon") != EXPECTED_RAW + "assets/icon-512.png":
        fail("Icon must be the public raw 512px PNG URL")
    if text(root, "Support") != "https://github.com/kdmn2/nzbget-sab-bridge/issues":
        fail("Support must point to GitHub Issues")
    if text(root, "Project") != "https://github.com/kdmn2/nzbget-sab-bridge":
        fail("Project must point to the public repository")
    if root.find("WebUI") is not None:
        fail("this API-only service must not advertise a WebUI")
    if (root.findtext("Privileged") or "").strip().lower() != "false":
        fail("Privileged must be false")

    configs = root.findall("Config")
    if not configs:
        fail("no <Config> entries")
    by_target = {(entry.get("Target") or ""): entry for entry in configs}
    required_targets = {
        "6789",
        "/data",
        "MODE",
        "LISTEN_HOST",
        "LISTEN_PORT",
        "SABNZBD_URL",
        "SABNZBD_API_KEY",
        "NZBGET_USERNAME",
        "NZBGET_PASSWORD",
        "TLS_VERIFY",
        "READYZ_CHECK_SAB",
        "SAB_MAX_RESPONSE_BYTES",
    }
    missing = required_targets - set(by_target)
    if missing:
        fail("missing Config targets: " + ", ".join(sorted(missing)))
    if by_target["6789"].get("Type") != "Port":
        fail("6789 must be a Port Config")
    if by_target["6789"].get("Default") != "6790":
        fail("host port default must be 6790")
    if by_target["/data"].get("Type") != "Path":
        fail("/data must be a Path Config")
    if by_target["/data"].get("Default") != "/mnt/user/appdata/nzbget-sab-bridge":
        fail("appdata default must be /mnt/user/appdata/nzbget-sab-bridge")
    if by_target["MODE"].get("Default") != "bridge":
        fail("MODE must default to bridge")
    if by_target["LISTEN_HOST"].get("Default") != "0.0.0.0":
        fail("LISTEN_HOST must default to 0.0.0.0")
    if by_target["LISTEN_PORT"].get("Default") != "6789":
        fail("LISTEN_PORT must default to 6789")
    if by_target["TLS_VERIFY"].get("Default") != "true":
        fail("TLS_VERIFY must default to true")
    if by_target["READYZ_CHECK_SAB"].get("Default") != "true":
        fail("READYZ_CHECK_SAB must default to true")
    if by_target["SAB_MAX_RESPONSE_BYTES"].get("Default") != "33554432":
        fail("SAB_MAX_RESPONSE_BYTES must default to 33554432")
    for secret in ("SABNZBD_API_KEY", "NZBGET_PASSWORD"):
        if by_target[secret].get("Mask") != "true":
            fail(f"{secret} must be masked")
        if by_target[secret].get("Default"):
            fail(f"{secret} must not have a default secret")
    for entry in configs:
        if entry.get("Type") not in {"Path", "Variable", "Port", "Device", "Label"}:
            fail(f"invalid Config type for {entry.get('Name')}")
        if entry.get("Display") not in {"always", "always-hide", "advanced", "advanced-hide"}:
            fail(f"invalid Display for {entry.get('Name')}")
    if any((entry.get("Target") or "").upper() in {"PUID", "PGID"} for entry in configs):
        fail("the image does not implement PUID/PGID")
    if any((entry.get("Target") or "").startswith("CAPTURE_") for entry in configs):
        fail("capture settings must not be in the default template")

    if png_dimensions(ICON_256) != (256, 256):
        fail("icon-256.png must be 256x256")
    if png_dimensions(ICON_512) != (512, 512):
        fail("icon-512.png must be 512x512")
    if not ICON_SVG.is_file() or ICON_SVG.stat().st_size == 0:
        fail("missing icon.svg")
    if "<svg" not in ICON_SVG.read_text(encoding="utf-8"):
        fail("icon.svg is not SVG")
    print("Unraid template and icon assets validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
