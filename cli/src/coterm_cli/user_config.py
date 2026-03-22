from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_HUB_BASE_URL = "https://hub.coterm.aigcteacher.top"
LEGACY_LOCAL_HUB_BASE_URL = "http://127.0.0.1:18083"


def coterm_home() -> Path:
    return Path(os.path.expanduser(os.getenv("COTERM_HOME", "~/.coterm")))


def config_path() -> Path:
    return coterm_home() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_config(**values: Any) -> dict[str, Any]:
    data = load_config()
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    save_config(data)
    return data


def resolve_saved_hub() -> str | None:
    value = load_config().get("hub")
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or trimmed == LEGACY_LOCAL_HUB_BASE_URL:
        return None
    return trimmed


def resolve_saved_auth_token() -> str | None:
    value = load_config().get("auth_token")
    return value if isinstance(value, str) and value.strip() else None


def resolve_hub_with_source(cli_value: str | None = None) -> tuple[str, str]:
    if cli_value:
        return cli_value, "cli flag"
    env_hub = os.getenv("COTERM_HUB")
    if env_hub:
        return env_hub, "env:COTERM_HUB"
    legacy_env_hub = os.getenv("COTERM_HUB_BASE_URL")
    if legacy_env_hub:
        return legacy_env_hub, "env:COTERM_HUB_BASE_URL"
    saved_hub = resolve_saved_hub()
    if saved_hub:
        return saved_hub, f"saved config:{config_path()}"
    return DEFAULT_HUB_BASE_URL, "built-in default"


def resolve_hub_from_sources(cli_value: str | None = None) -> str:
    return resolve_hub_with_source(cli_value)[0]


def resolve_auth_token_with_source(cli_value: str | None = None) -> tuple[str, str]:
    if cli_value:
        return cli_value, "cli flag"
    env_token = os.getenv("COTERM_AUTH_TOKEN", "")
    if env_token:
        return env_token, "env:COTERM_AUTH_TOKEN"
    saved_token = resolve_saved_auth_token()
    if saved_token:
        return saved_token, f"saved config:{config_path()}"
    return "", "not set"


def resolve_auth_token_from_sources(cli_value: str | None = None) -> str:
    return resolve_auth_token_with_source(cli_value)[0]
