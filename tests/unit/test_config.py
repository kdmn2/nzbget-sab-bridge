from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from nzbget_sab_bridge.config import ConfigError, Settings

BASE_ENV = {
    "NZBGET_USERNAME": "bridge-user",
    "NZBGET_PASSWORD": "bridge-password",
    "SABNZBD_URL": "http://sab:8080",
    "SABNZBD_API_KEY": "sab-api-key",
}


def test_bridge_settings_from_env() -> None:
    settings = Settings.from_env(BASE_ENV)
    assert settings.mode == "bridge"
    assert settings.sabnzbd_url == "http://sab:8080"
    assert settings.client_password == "bridge-password"
    assert settings.advertised_nzbget_version == "21.1"
    assert settings.auth_required is True


def test_sab_url_subpath_and_api_suffix_are_normalized() -> None:
    env = dict(BASE_ENV, SABNZBD_URL="http://sab:8080/reverse/sab/api/")
    settings = Settings.from_env(env)
    assert settings.sabnzbd_url == "http://sab:8080/reverse/sab"


def test_capture_mode_does_not_require_sab_key() -> None:
    settings = Settings.from_env(
        {
            "MODE": "capture",
            "NZBGET_USERNAME": "bridge-user",
            "NZBGET_PASSWORD": "bridge-password",
            "CAPTURE_NZBGET_URL": "http://nzbget:6789/nzbget",
            "CAPTURE_NZBGET_USERNAME": "backend-user",
            "CAPTURE_NZBGET_PASSWORD": "backend-password",
        }
    )
    assert settings.is_capture
    assert settings.sabnzbd_api_key is None
    assert settings.capture_nzbget_url == "http://nzbget:6789/nzbget"
    assert any("Capture mode" in warning for warning in settings.startup_warnings)


def test_capture_requires_backend_url() -> None:
    with pytest.raises(ConfigError, match="CAPTURE_NZBGET_URL"):
        Settings.from_env(
            {
                "MODE": "capture",
                "NZBGET_USERNAME": "bridge-user",
                "NZBGET_PASSWORD": "bridge-password",
            }
        )


def test_bridge_requires_sab_url_and_key() -> None:
    with pytest.raises(ConfigError, match="SABNZBD_URL"):
        Settings.from_env(
            {
                "NZBGET_USERNAME": "bridge-user",
                "NZBGET_PASSWORD": "bridge-password",
            }
        )
    with pytest.raises(ConfigError, match="SABNZBD_API_KEY"):
        Settings.from_env(
            {
                "NZBGET_USERNAME": "bridge-user",
                "NZBGET_PASSWORD": "bridge-password",
                "SABNZBD_URL": "http://sab:8080",
            }
        )


def test_default_credentials_are_rejected() -> None:
    with pytest.raises(ConfigError, match="default client credentials"):
        Settings.from_env(dict(BASE_ENV, NZBGET_PASSWORD="tegbzn6789"))
    with pytest.raises(ConfigError, match="default client credentials"):
        Settings.from_env(dict(BASE_ENV, NZBGET_USERNAME="nzbget"))


def test_empty_credentials_need_explicit_insecure_flag() -> None:
    with pytest.raises(ConfigError, match="ALLOW_INSECURE_NO_AUTH"):
        Settings.from_env(
            {
                "SABNZBD_URL": "http://sab:8080",
                "SABNZBD_API_KEY": "sab-api-key",
            }
        )
    settings = Settings.from_env(
        {
            "ALLOW_INSECURE_NO_AUTH": "true",
            "SABNZBD_URL": "http://sab:8080",
            "SABNZBD_API_KEY": "sab-api-key",
        }
    )
    assert settings.auth_required is False
    assert any("ALLOW_INSECURE" in warning for warning in settings.startup_warnings)


def test_secret_file_loading(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("file-password\n", encoding="utf-8")
    os.chmod(password_file, 0o600)
    api_file = tmp_path / "api"
    api_file.write_text("file-api-key\n", encoding="utf-8")
    os.chmod(api_file, 0o600)
    settings = Settings.from_env(
        {
            "NZBGET_USERNAME": "bridge-user",
            "NZBGET_PASSWORD_FILE": str(password_file),
            "SABNZBD_URL": "http://sab:8080",
            "SABNZBD_API_KEY_FILE": str(api_file),
        }
    )
    assert settings.client_password == "file-password"
    assert settings.sabnzbd_api_key == SecretStr("file-api-key")


def test_secret_file_and_plain_value_are_mutually_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="only one"):
        Settings.from_env(dict(BASE_ENV, NZBGET_PASSWORD_FILE=str(path)))


def test_group_readable_secret_file_warns(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_text("x", encoding="utf-8")
    os.chmod(path, 0o640)
    settings = Settings.from_env(
        {
            "NZBGET_USERNAME": "bridge-user",
            "NZBGET_PASSWORD_FILE": str(path),
            "SABNZBD_URL": "http://sab:8080",
            "SABNZBD_API_KEY": "sab-api-key",
        }
    )
    assert any("readable by group" in warning for warning in settings.startup_warnings)


def test_unsupported_advertised_profile_is_rejected() -> None:
    with pytest.raises(ConfigError, match="ADVERTISED_NZBGET_VERSION"):
        Settings.from_env(dict(BASE_ENV, ADVERTISED_NZBGET_VERSION="99.0"))


def test_invalid_timeout_and_limits_are_rejected() -> None:
    with pytest.raises(ConfigError, match="timeouts"):
        Settings.from_env(dict(BASE_ENV, HTTP_READ_TIMEOUT="0"))
    with pytest.raises(ConfigError, match="positive"):
        Settings.from_env(dict(BASE_ENV, REQUEST_MAX_BYTES="0"))
    with pytest.raises(ConfigError, match="NZB_MAX_DECODED_BYTES"):
        Settings.from_env(dict(BASE_ENV, REQUEST_MAX_BYTES="100", NZB_MAX_DECODED_BYTES="200"))


def test_sab_response_limit_default_and_override() -> None:
    assert Settings.from_env(BASE_ENV).sab_max_response_bytes == 32 * 1024 * 1024
    assert (
        Settings.from_env(dict(BASE_ENV, SAB_MAX_RESPONSE_BYTES="1048576")).sab_max_response_bytes
        == 1_048_576
    )
    with pytest.raises(ConfigError, match="sab_max_response_bytes"):
        Settings.from_env(dict(BASE_ENV, SAB_MAX_RESPONSE_BYTES="0"))


def test_nzb_effective_upload_limit_accounts_for_base64_and_json() -> None:
    constrained = Settings.from_env(
        dict(
            BASE_ENV,
            REQUEST_MAX_BYTES="33554432",
            NZB_MAX_DECODED_BYTES="25165824",
        )
    )
    assert constrained.nzb_effective_decoded_bytes == 25_116_672
    assert any(
        "effective decoded upload limit" in warning for warning in constrained.startup_warnings
    )

    sufficient = Settings.from_env(
        dict(
            BASE_ENV,
            REQUEST_MAX_BYTES="37748736",
            NZB_MAX_DECODED_BYTES="25165824",
        )
    )
    assert sufficient.nzb_effective_decoded_bytes == 25_165_824
    assert not any(
        "effective decoded upload limit" in warning for warning in sufficient.startup_warnings
    )


def test_tls_verify_false_warns() -> None:
    settings = Settings.from_env(dict(BASE_ENV, TLS_VERIFY="false"))
    assert settings.tls_verify is False
    assert any("TLS_VERIFY" in warning for warning in settings.startup_warnings)


def test_safe_summary_does_not_contain_secrets() -> None:
    settings = Settings.from_env(BASE_ENV)
    summary = str(settings.safe_summary())
    assert "bridge-password" not in summary
    assert "sab-api-key" not in summary
    assert "sab:8080" not in summary


def test_invalid_enum_error_does_not_echo_input_value() -> None:
    with pytest.raises(ConfigError) as error:
        Settings.from_env(dict(BASE_ENV, MODE="secret-mode-value"))
    assert "secret-mode-value" not in str(error.value)


def test_invalid_url_credentials_are_rejected() -> None:
    with pytest.raises(ConfigError, match="SABNZBD_URL"):
        Settings.from_env(dict(BASE_ENV, SABNZBD_URL="http://user:pass@sab:8080"))


def test_non_loopback_listen_host_warns() -> None:
    settings = Settings.from_env(dict(BASE_ENV, LISTEN_HOST="0.0.0.0"))
    assert any("LISTEN_HOST" in warning for warning in settings.startup_warnings)
