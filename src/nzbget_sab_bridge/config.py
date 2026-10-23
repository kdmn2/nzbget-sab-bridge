"""Application configuration loaded from environment variables and secret files."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .urls import normalize_base_url

SUPPORTED_NZBGET_PROFILES: dict[str, str] = {
    "21.1": (
        "Provisional NZBGet 21.1 JSON-RPC compatibility profile: v16 append "
        "parameter list and v18 editqueue parameter list. Not live-verified."
    )
}

_DEFAULT_CREDENTIALS = {
    "nzbget",
    "tegbzn6789",
    "admin",
    "password",
    "changeme",
    "change-me",
    "letmein",
}
_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_NZB_JSON_ENVELOPE_OVERHEAD = 64 * 1024


class ConfigError(RuntimeError):
    """Configuration is unsafe or inconsistent. Messages never contain values."""


def _env(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = env.get(name)
    if value is None:
        return default
    return value


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be one of true/false, 1/0, yes/no, or on/off")


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _read_secret(
    env: Mapping[str, str],
    name: str,
    warnings: list[str],
) -> str | None:
    """Read a secret from NAME or NAME_FILE, refusing ambiguous configuration."""

    direct = env.get(name)
    file_name = env.get(f"{name}_FILE")
    if direct is not None and file_name is not None:
        raise ConfigError(f"Set only one of {name} and {name}_FILE")
    if file_name is None:
        return direct

    path = Path(file_name)
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise ConfigError(f"{name}_FILE could not be read") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ConfigError(f"{name}_FILE must point to a regular file")
    if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        warnings.append(f"{name}_FILE is readable by group or others")
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{name}_FILE could not be read as UTF-8") from exc
    if value == "":
        raise ConfigError(f"{name}_FILE is empty")
    return value


class Settings(BaseModel):
    """Validated runtime settings.

    Secrets are stored as :class:`SecretStr` and are never included in repr
    output or logs. ``from_env`` is the only production entry point.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["bridge", "capture"] = "bridge"
    listen_host: str = "127.0.0.1"
    listen_port: int = 6789

    nzbget_username: str = ""
    nzbget_password: SecretStr = Field(default_factory=lambda: SecretStr(""))

    sabnzbd_url: str | None = None
    sabnzbd_api_key: SecretStr | None = None

    capture_nzbget_url: str | None = None
    capture_nzbget_username: str = ""
    capture_nzbget_password: SecretStr = Field(default_factory=lambda: SecretStr(""))

    data_dir: Path = Path("/data")
    advertised_nzbget_version: str = "21.1"

    request_max_bytes: int = 32 * 1024 * 1024
    nzb_max_decoded_bytes: int = 24 * 1024 * 1024

    @property
    def nzb_effective_decoded_bytes(self) -> int:
        """Decoded NZB size that also fits base64 plus the JSON envelope."""

        encoded_budget = max(0, self.request_max_bytes - _NZB_JSON_ENVELOPE_OVERHEAD)
        return min(self.nzb_max_decoded_bytes, (encoded_budget // 4) * 3)

    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    http_write_timeout: float = 30.0
    http_pool_timeout: float = 5.0

    log_level: str = "INFO"
    log_format: Literal["json", "human"] = "json"
    tls_verify: bool = True

    allow_insecure_no_auth: bool = False
    readyz_check_sab: bool = True
    readyz_timeout_seconds: float = 2.0

    capture_xmlrpc: bool = False
    capture_dir: Path = Path("/captures")
    capture_max_records: int = 500
    capture_max_total_bytes: int = 10 * 1024 * 1024
    capture_max_record_bytes: int = 64 * 1024
    capture_inspect_max_bytes: int = 1024 * 1024
    capture_max_response_bytes: int = 64 * 1024 * 1024
    capture_retention_days: int = 0

    startup_warnings: list[str] = Field(default_factory=list, exclude=True)

    @field_validator("listen_port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("LISTEN_PORT must be between 1 and 65535")
        return value

    @field_validator("nzbget_username", "capture_nzbget_username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("Basic-auth usernames must not contain ':'")
        return value

    @field_validator("advertised_nzbget_version")
    @classmethod
    def _validate_profile(cls, value: str) -> str:
        if value not in SUPPORTED_NZBGET_PROFILES:
            supported = ", ".join(sorted(SUPPORTED_NZBGET_PROFILES))
            raise ValueError(
                f"ADVERTISED_NZBGET_VERSION must name a supported profile ({supported})"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _ALLOWED_LOG_LEVELS:
            raise ValueError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
        return normalized

    @field_validator(
        "request_max_bytes",
        "nzb_max_decoded_bytes",
        "capture_max_records",
        "capture_max_total_bytes",
        "capture_max_record_bytes",
        "capture_inspect_max_bytes",
        "capture_max_response_bytes",
    )
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("size and record limits must be positive")
        return value

    @field_validator(
        "http_connect_timeout",
        "http_read_timeout",
        "http_write_timeout",
        "http_pool_timeout",
        "readyz_timeout_seconds",
    )
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if not 0 < value <= 300:
            raise ValueError("timeouts must be greater than 0 and at most 300 seconds")
        return value

    @field_validator("capture_retention_days")
    @classmethod
    def _validate_retention(cls, value: int) -> int:
        if value < 0:
            raise ValueError("CAPTURE_RETENTION_DAYS must be 0 or greater")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Settings:
        if self.allow_insecure_no_auth:
            self.startup_warnings.append(
                "ALLOW_INSECURE_NO_AUTH is enabled; RPC endpoints may be accessed "
                "without credentials"
            )
        else:
            self._require_secure_client_credentials()

        if not self.tls_verify:
            self.startup_warnings.append(
                "TLS_VERIFY is disabled; outbound HTTPS certificates are not verified"
            )

        if self.nzb_max_decoded_bytes > self.request_max_bytes:
            raise ValueError("NZB_MAX_DECODED_BYTES must not exceed REQUEST_MAX_BYTES")
        if self.nzb_effective_decoded_bytes < self.nzb_max_decoded_bytes:
            self.startup_warnings.append(
                "REQUEST_MAX_BYTES limits NZB uploads after base64 and JSON "
                "overhead; effective decoded upload limit is "
                f"{self.nzb_effective_decoded_bytes} bytes"
            )
        if self.capture_inspect_max_bytes > self.capture_max_response_bytes:
            raise ValueError("CAPTURE_INSPECT_MAX_BYTES must not exceed CAPTURE_MAX_RESPONSE_BYTES")
        if self.capture_retention_days and self.capture_retention_days < 1:
            raise ValueError("CAPTURE_RETENTION_DAYS must be 0 or at least 1")

        if self.mode == "bridge":
            if not self.sabnzbd_url:
                raise ValueError("SABNZBD_URL is required in bridge mode")
            if self.sabnzbd_api_key is None or not self.sabnzbd_api_key.get_secret_value():
                raise ValueError("SABNZBD_API_KEY or SABNZBD_API_KEY_FILE is required")
            try:
                self.sabnzbd_url = normalize_base_url(self.sabnzbd_url, strip_api_suffix=True)
            except ValueError as exc:
                raise ValueError(f"SABNZBD_URL is invalid: {exc}") from exc
        else:
            self.startup_warnings.append(
                "Capture mode is enabled; it forwards RPC requests to a real NZBGet "
                "backend and is unsafe to expose outside a trusted LAN or VPN"
            )
            if not self.capture_nzbget_url:
                raise ValueError("CAPTURE_NZBGET_URL is required in capture mode")
            try:
                self.capture_nzbget_url = normalize_base_url(
                    self.capture_nzbget_url, strip_api_suffix=False
                )
            except ValueError as exc:
                raise ValueError(f"CAPTURE_NZBGET_URL is invalid: {exc}") from exc

            backend_user = self.capture_nzbget_username
            backend_password = self.capture_nzbget_password.get_secret_value()
            if bool(backend_user) != bool(backend_password):
                raise ValueError(
                    "CAPTURE_NZBGET_USERNAME and CAPTURE_NZBGET_PASSWORD/_FILE must be set together"
                )
            if not backend_user:
                self.startup_warnings.append(
                    "Capture backend credentials are empty; requests to NZBGet will be "
                    "unauthenticated"
                )

        if self.listen_host not in {"127.0.0.1", "::1", "localhost"}:
            self.startup_warnings.append(
                "LISTEN_HOST is not loopback; ensure the service is reachable only on a "
                "trusted LAN, WireGuard network, or behind TLS"
            )
        return self

    def _require_secure_client_credentials(self) -> None:
        username = self.nzbget_username.strip()
        password = self.nzbget_password.get_secret_value()
        if not username or not password:
            raise ValueError(
                "NZBGET_USERNAME and NZBGET_PASSWORD (or _FILE) are required unless "
                "ALLOW_INSECURE_NO_AUTH=true"
            )
        lowered = {username.lower(), password.lower()}
        if lowered & _DEFAULT_CREDENTIALS:
            raise ValueError(
                "Refusing default client credentials; set a unique NZBGET_USERNAME and "
                "NZBGET_PASSWORD or explicitly set ALLOW_INSECURE_NO_AUTH=true"
            )

    @property
    def is_capture(self) -> bool:
        return self.mode == "capture"

    @property
    def auth_required(self) -> bool:
        return not self.allow_insecure_no_auth

    @property
    def client_username(self) -> str:
        return self.nzbget_username

    @property
    def client_password(self) -> str:
        return self.nzbget_password.get_secret_value()

    @property
    def capture_backend_username(self) -> str:
        return self.capture_nzbget_username

    @property
    def capture_backend_password(self) -> str:
        return self.capture_nzbget_password.get_secret_value()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source: Mapping[str, str] = os.environ if env is None else env
        warnings: list[str] = []

        def secret(name: str) -> SecretStr | None:
            value = _read_secret(source, name, warnings)
            return SecretStr(value) if value is not None else None

        client_password = secret("NZBGET_PASSWORD") or SecretStr("")
        sab_api_key = secret("SABNZBD_API_KEY")
        capture_password = secret("CAPTURE_NZBGET_PASSWORD") or SecretStr("")

        try:
            settings = cls(
                mode=_env(source, "MODE", "bridge") or "bridge",
                listen_host=_env(source, "LISTEN_HOST", "127.0.0.1") or "127.0.0.1",
                listen_port=_env_int(source, "LISTEN_PORT", 6789),
                nzbget_username=_env(source, "NZBGET_USERNAME", "") or "",
                nzbget_password=client_password,
                sabnzbd_url=_env(source, "SABNZBD_URL"),
                sabnzbd_api_key=sab_api_key,
                capture_nzbget_url=_env(source, "CAPTURE_NZBGET_URL"),
                capture_nzbget_username=_env(source, "CAPTURE_NZBGET_USERNAME", "") or "",
                capture_nzbget_password=capture_password,
                data_dir=Path(_env(source, "DATA_DIR", "/data") or "/data"),
                advertised_nzbget_version=_env(source, "ADVERTISED_NZBGET_VERSION", "21.1")
                or "21.1",
                request_max_bytes=_env_int(source, "REQUEST_MAX_BYTES", 32 * 1024 * 1024),
                nzb_max_decoded_bytes=_env_int(source, "NZB_MAX_DECODED_BYTES", 24 * 1024 * 1024),
                http_connect_timeout=_env_float(source, "HTTP_CONNECT_TIMEOUT", 5.0),
                http_read_timeout=_env_float(source, "HTTP_READ_TIMEOUT", 30.0),
                http_write_timeout=_env_float(source, "HTTP_WRITE_TIMEOUT", 30.0),
                http_pool_timeout=_env_float(source, "HTTP_POOL_TIMEOUT", 5.0),
                log_level=_env(source, "LOG_LEVEL", "INFO") or "INFO",
                log_format=_env(source, "LOG_FORMAT", "json") or "json",
                tls_verify=_env_bool(source, "TLS_VERIFY", True),
                allow_insecure_no_auth=_env_bool(source, "ALLOW_INSECURE_NO_AUTH", False),
                readyz_check_sab=_env_bool(source, "READYZ_CHECK_SAB", True),
                readyz_timeout_seconds=_env_float(source, "READYZ_TIMEOUT_SECONDS", 2.0),
                capture_xmlrpc=_env_bool(source, "CAPTURE_XMLRPC", False),
                capture_dir=Path(_env(source, "CAPTURE_DIR", "/captures") or "/captures"),
                capture_max_records=_env_int(source, "CAPTURE_MAX_RECORDS", 500),
                capture_max_total_bytes=_env_int(
                    source, "CAPTURE_MAX_TOTAL_BYTES", 10 * 1024 * 1024
                ),
                capture_max_record_bytes=_env_int(source, "CAPTURE_MAX_RECORD_BYTES", 64 * 1024),
                capture_inspect_max_bytes=_env_int(
                    source, "CAPTURE_INSPECT_MAX_BYTES", 1024 * 1024
                ),
                capture_max_response_bytes=_env_int(
                    source, "CAPTURE_MAX_RESPONSE_BYTES", 64 * 1024 * 1024
                ),
                capture_retention_days=_env_int(source, "CAPTURE_RETENTION_DAYS", 0),
                startup_warnings=warnings,
            )
        except ValidationError as exc:
            details = []
            for error in exc.errors():
                location = ".".join(str(part) for part in error.get("loc", ()))
                details.append(f"{location}: {error.get('msg', 'invalid value')}")
            raise ConfigError("; ".join(details) or "invalid configuration") from None
        except (ValueError, TypeError) as exc:
            raise ConfigError(str(exc)) from exc
        return settings

    def safe_summary(self) -> dict[str, Any]:
        """Return non-secret settings suitable for diagnostics."""

        return {
            "mode": self.mode,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "advertised_nzbget_version": self.advertised_nzbget_version,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "tls_verify": self.tls_verify,
            "auth_required": self.auth_required,
            "readyz_check_sab": self.readyz_check_sab,
            "capture_xmlrpc": self.capture_xmlrpc,
        }
