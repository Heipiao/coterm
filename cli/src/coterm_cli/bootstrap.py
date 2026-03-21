from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, parse, request
from uuid import uuid4

from .config import resolve_agent_name, resolve_agent_type, resolve_hub_base_url, resolve_workdir
from .qr import render_ascii_qr
from .user_config import resolve_auth_token_from_sources


@dataclass(frozen=True, slots=True)
class StartupInfo:
    agent_name: str
    agent_type: str
    device_id: str
    session_id: str
    hub_base_url: str
    ws_cli_url: str
    workdir: str
    pairing_code: str
    session_url: str
    qr_payload: str


def bootstrap_startup(args) -> StartupInfo:
    hub_base_url = _normalize_base_url(resolve_hub_base_url(args))
    auth_token = resolve_auth_token_from_sources(args.auth_token)
    headers = _build_headers(auth_token)
    device_id = resolve_device_id(args.device_id)
    agent_name = resolve_agent_name(args)
    agent_type = resolve_agent_type(args)
    workdir = resolve_workdir(args)

    session = _post_json(
        _join_url(hub_base_url, "/api/v1/sessions"),
        headers=headers,
        payload={"agent_type": agent_type, "device_id": device_id},
    )
    session_id = str(session["session_id"])
    pairing = _post_json(
        _join_url(hub_base_url, f"/api/v1/sessions/{session_id}/pairings"),
        headers=headers,
        payload={"client_platform": "web"},
    )
    ws_cli_url = _resolve_ws_url(hub_base_url, str(session["ws_cli_url"]))
    return StartupInfo(
        agent_name=agent_name,
        agent_type=agent_type,
        device_id=device_id,
        session_id=session_id,
        hub_base_url=hub_base_url,
        ws_cli_url=ws_cli_url,
        workdir=workdir,
        pairing_code=str(pairing["pairing_code"]),
        session_url=str(pairing["qr_url"]),
        qr_payload=str(pairing["qr_payload"]),
    )


def print_startup_banner(info: StartupInfo) -> None:
    print(f"Agent: {info.agent_name}")
    print(f"Workdir: {info.workdir}")
    print(f"Session ID: {info.session_id}")
    print(f"Session URL: {info.session_url}")
    print(f"Pairing Code: {info.pairing_code}")
    print("")
    print(render_ascii_qr(info.qr_payload))
    print("")


def resolve_device_id(device_id: str | None) -> str:
    if device_id:
        return device_id
    env_device_id = os.getenv("COTERM_DEVICE_ID", "").strip()
    if env_device_id:
        return env_device_id

    device_id_path = _coterm_home() / "device_id"
    if device_id_path.exists():
        stored = device_id_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    new_device_id = f"dev_{uuid4().hex[:8]}"
    device_id_path.parent.mkdir(parents=True, exist_ok=True)
    device_id_path.write_text(f"{new_device_id}\n", encoding="utf-8")
    return new_device_id


def _coterm_home() -> Path:
    return Path(os.path.expanduser(os.getenv("COTERM_HOME", "~/.coterm")))


def _build_headers(auth_token: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _post_json(url: str, *, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"hub request failed at {url}: {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"failed to reach hub at {url}: {exc.reason}") from exc

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected hub response from {url}")
    return data


def _normalize_base_url(base_url: str) -> str:
    parsed = parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"--hub must use http or https, got: {base_url}")
    return base_url.rstrip("/")


def _join_url(base_url: str, path: str) -> str:
    return parse.urljoin(f"{base_url}/", path.lstrip("/"))


def _resolve_ws_url(base_url: str, ws_url: str) -> str:
    if ws_url.startswith("ws://") or ws_url.startswith("wss://"):
        return ws_url

    parsed = parse.urlparse(base_url)
    ws_base = parsed._replace(scheme="wss" if parsed.scheme == "https" else "ws", path="", params="", query="", fragment="")
    return parse.urljoin(parse.urlunparse(ws_base), ws_url)
