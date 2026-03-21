from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from importlib import metadata
from urllib import error, parse, request

from .user_config import (
    DEFAULT_HUB_BASE_URL,
    config_path,
    load_config,
    resolve_auth_token_from_sources,
    resolve_auth_token_with_source,
    resolve_hub_from_sources,
    resolve_hub_with_source,
    save_config,
    update_config,
)


ROOT_COMMANDS = ("start", "hub", "auth", "doctor", "version", "help")


def build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coterm",
        description="Coterm CLI runs a local agent runtime and connects it to a Coterm Hub.",
        epilog=(
            "Examples:\n"
            "  coterm\n"
            "  coterm claude --workdir ~/project/foo\n"
            "  coterm hub start\n"
            "  coterm auth login --hub http://127.0.0.1:18083\n\n"
            f"Hub resolution order: --hub -> $COTERM_HUB -> $COTERM_HUB_BASE_URL -> saved config -> {DEFAULT_HUB_BASE_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="?", choices=ROOT_COMMANDS, help="Command to run.")
    return parser


def build_hub_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coterm hub", description="Manage a local Coterm hub.")
    subparsers = parser.add_subparsers(dest="hub_command", required=True)

    start = subparsers.add_parser("start", help="Start the local hub.")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=18083)
    start.add_argument("--log-level", default="INFO")
    start.add_argument("--reload", action="store_true")

    status = subparsers.add_parser("status", help="Check hub health.")
    status.add_argument("--hub", help=f"Hub base URL. Defaults to {DEFAULT_HUB_BASE_URL}.")
    return parser


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coterm auth", description="Manage Coterm hub authentication.")
    subparsers = parser.add_subparsers(dest="auth_command", required=True)

    login = subparsers.add_parser("login", help="Save hub and token locally.")
    login.add_argument("--hub", help=f"Hub base URL. Defaults to {DEFAULT_HUB_BASE_URL}.")
    login.add_argument("--token", help="Hub auth token. If omitted, prompt interactively.")

    subparsers.add_parser("status", help="Show saved auth status.")
    logout = subparsers.add_parser("logout", help="Remove the saved auth token.")
    logout.add_argument("--keep-hub", action="store_true", help="Keep the saved hub URL.")
    return parser


def dispatch_command(argv: list[str]) -> int:
    if not argv:
        build_root_parser().print_help()
        return 0

    command = argv[0]
    if command == "help":
        build_root_parser().print_help()
        return 0
    if command == "version":
        return run_version()
    if command == "doctor":
        return run_doctor()
    if command == "hub":
        return run_hub(argv[1:])
    if command == "auth":
        return run_auth(argv[1:])
    if command == "start":
        return 101
    raise SystemExit(f"unsupported command: {command}")


def run_version() -> int:
    try:
        version = metadata.version("coterm")
    except metadata.PackageNotFoundError:
        version = "0.1.0"
    print(version)
    return 0


def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    cfg = load_config()
    hub, hub_source = resolve_hub_with_source()
    token, token_source = resolve_auth_token_with_source()

    checks.append(("config", True, str(config_path())))
    checks.append(("hub", True, f"{hub} ({hub_source})"))
    checks.append(("auth", bool(token), f"{'token found' if token else 'no token saved'} ({token_source})"))

    claude_bin = shutil.which("claude")
    checks.append(("claude", claude_bin is not None, claude_bin or "claude not found in PATH"))

    hub_cmd, _ = resolve_hub_launcher()
    checks.append(("coterm-hub", hub_cmd is not None, " ".join(hub_cmd) if hub_cmd else "coterm-hub not found"))

    health_ok, health_detail = _check_hub_health(hub)
    checks.append(("hub health", health_ok, health_detail))

    ok = True
    for name, passed, detail in checks:
        status = "OK" if passed else "FAIL"
        print(f"{status:4} {name}: {detail}")
        ok = ok and passed
    return 0 if ok else 1


def run_hub(argv: list[str]) -> int:
    parser = build_hub_parser()
    args = parser.parse_args(argv)
    if args.hub_command == "status":
        hub = resolve_hub_from_sources(args.hub)
        ok, detail = _check_hub_health(hub)
        print(f"hub: {hub}")
        print(f"status: {'ok' if ok else 'down'}")
        print(f"detail: {detail}")
        return 0 if ok else 1

    hub_cmd, extra_env = resolve_hub_launcher()
    if hub_cmd is None:
        print("coterm-hub executable not found", file=sys.stderr)
        return 1

    command = [
        *hub_cmd,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--log-level",
        args.log_level,
    ]
    if args.reload:
        command.append("--reload")
    print(f"starting hub: http://{args.host}:{args.port}")
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.run(command, check=False, env=env).returncode


def run_auth(argv: list[str]) -> int:
    parser = build_auth_parser()
    args = parser.parse_args(argv)

    if args.auth_command == "status":
        cfg = load_config()
        hub, hub_source = resolve_hub_with_source()
        token, token_source = resolve_auth_token_with_source()
        print(f"config: {config_path()}")
        print(f"hub: {hub} ({hub_source})")
        print(f"token: {_mask_secret(token) if token else 'not set'} ({token_source})")
        print(f"saved keys: {', '.join(sorted(cfg)) if cfg else '(empty)'}")
        return 0

    if args.auth_command == "logout":
        if args.keep_hub:
            update_config(auth_token=None)
        else:
            update_config(auth_token=None, hub=None)
        print(f"updated config: {config_path()}")
        return 0

    hub = resolve_hub_from_sources(args.hub)
    token = args.token
    if token is None:
        token = getpass.getpass("Hub token: ").strip()
    update_config(hub=hub, auth_token=token or None)
    print(f"saved hub: {hub}")
    print(f"saved token: {_mask_secret(token)}" if token else "saved token: (empty)")
    print(f"config: {config_path()}")
    return 0


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _check_hub_health(hub: str) -> tuple[bool, str]:
    try:
        health = _get_json(_join_url(hub, "/health"))
    except RuntimeError as exc:
        return False, str(exc)
    if health.get("ok") is True:
        return True, json.dumps(health, ensure_ascii=False)
    return False, json.dumps(health, ensure_ascii=False)


def _get_json(url: str) -> dict[str, object]:
    opener = request.build_opener(request.ProxyHandler({}))
    req = request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with opener.open(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("unexpected response")
    return data


def _join_url(base_url: str, path: str) -> str:
    normalized = base_url.rstrip("/") + "/"
    return parse.urljoin(normalized, path.lstrip("/"))


def resolve_hub_launcher() -> tuple[list[str] | None, dict[str, str]]:
    hub_bin = shutil.which("coterm-hub")
    if hub_bin is not None:
        return [hub_bin], {}

    repo_src = _repo_hub_src()
    if repo_src is not None:
        env = {}
        current = os.environ.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_src}:{current}" if current else str(repo_src)
        return [sys.executable, "-m", "coterm_hub.main"], env

    return None, {}


def _repo_hub_src() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "coterm-hub" / "src"
        if candidate.exists():
            return candidate
    return None
