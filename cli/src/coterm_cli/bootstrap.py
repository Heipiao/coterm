from __future__ import annotations

import json
import os
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import error, parse, request
from uuid import uuid4

from .config import resolve_agent_name, resolve_agent_type, resolve_hub_base_url, resolve_workdir
from .qr import render_ascii_qr


@dataclass(frozen=True, slots=True)
class StartupInfo:
    agent_name: str
    agent_type: str
    device_id: str
    bootstrap_id: str
    hub_base_url: str
    workdir: str
    pairing_code: str
    session_url: str
    qr_payload: str
    expires_at: int
    cli_bootstrap_token: str


@dataclass(frozen=True, slots=True)
class ActivationInfo:
    session_id: str
    ws_cli_url: str
    cli_connect_token: str


def bootstrap_startup(args) -> StartupInfo:
    hub_base_url = _normalize_base_url(resolve_hub_base_url(args))
    device_id = resolve_device_id(args.device_id)
    agent_name = resolve_agent_name(args)
    agent_type = resolve_agent_type(args)
    workdir = resolve_workdir(args)

    bootstrap = _post_json(
        _join_url(hub_base_url, "/api/v1/bootstrap/pairings"),
        headers=_build_headers(""),
        payload={"agent_type": agent_type, "device_id": device_id},
    )
    return StartupInfo(
        agent_name=agent_name,
        agent_type=agent_type,
        device_id=device_id,
        bootstrap_id=str(bootstrap["bootstrap_id"]),
        hub_base_url=hub_base_url,
        workdir=workdir,
        pairing_code=str(bootstrap["pairing_code"]),
        session_url=str(bootstrap["qr_url"]),
        qr_payload=str(bootstrap["qr_payload"]),
        expires_at=int(bootstrap["expires_at"]),
        cli_bootstrap_token=str(bootstrap["cli_bootstrap_token"]),
    )


def print_startup_banner(info: StartupInfo) -> None:
    print(f"Agent: {info.agent_name}")
    print(f"Workdir: {info.workdir}")
    print(f"Bootstrap ID: {info.bootstrap_id}")
    print(f"Session URL: {info.session_url}")
    print(f"Pairing Code: {info.pairing_code}")
    print("Waiting for a mobile client to scan or enter the code...")
    print("")
    print(render_ascii_qr(info.qr_payload))
    print("")


async def wait_for_pairing_activation(info: StartupInfo, *, poll_interval_sec: float = 2.0) -> ActivationInfo:
    headers = _build_headers(info.cli_bootstrap_token)
    status_url = _join_url(info.hub_base_url, f"/api/v1/bootstrap/pairings/{info.bootstrap_id}")

    while True:
        status = _get_json(status_url, headers=headers)
        pairing_status = str(status.get("status", ""))
        if pairing_status == "ACTIVATED":
            session_id = str(status.get("session_id", ""))
            ws_cli_url = _resolve_ws_url(info.hub_base_url, str(status.get("ws_cli_url", "")))
            cli_connect_token = str(status.get("cli_connect_token", ""))
            if not session_id or not ws_cli_url or not cli_connect_token:
                raise RuntimeError(f"incomplete activation payload from {status_url}")
            return ActivationInfo(
                session_id=session_id,
                ws_cli_url=ws_cli_url,
                cli_connect_token=cli_connect_token,
            )
        if pairing_status == "EXPIRED":
            raise RuntimeError("pairing expired before a client connected")
        if int(time.time() * 1000) >= info.expires_at:
            raise RuntimeError("pairing expired before activation completed")
        await asyncio.sleep(poll_interval_sec)


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
    return _request_json(req, url)


def _get_json(url: str, *, headers: dict[str, str]) -> dict[str, object]:
    req = request.Request(url, headers=headers, method="GET")
    return _request_json(req, url)


def _request_json(req: request.Request, url: str) -> dict[str, object]:
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
