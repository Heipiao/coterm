from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from .user_config import DEFAULT_HUB_BASE_URL, resolve_saved_auth_token, resolve_saved_hub


DEFAULT_AGENT = "claude"
DEFAULT_AGENT_TYPE = "claude_code"
AGENT_NAME_TO_TYPE = {
    "claude": "claude_code",
    "codex": "codex",
}


@dataclass(frozen=True, slots=True)
class CLIConfig:
    hub_url: str
    auth_token: str
    session_id: str
    device_id: str
    agent_type: str = DEFAULT_AGENT_TYPE
    claude_bin: str = "claude"
    heartbeat_interval_sec: int = 10
    reconnect_base_sec: float = 3.0
    reconnect_max_sec: float = 30.0
    log_level: str = "INFO"
    permission_mode: str | None = None
    working_dir: str | None = None
    allowed_tools: tuple[str, ...] = ()
    verbose: bool = False

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        hub_url: str,
        session_id: str,
        device_id: str | None,
    ) -> "CLIConfig":
        allowed_tools: tuple[str, ...]
        if args.allowed_tool:
            allowed_tools = tuple(args.allowed_tool)
        else:
            env_tools = os.getenv("COTERM_ALLOWED_TOOLS", "").strip()
            allowed_tools = tuple(tool for tool in env_tools.split(",") if tool)

        return cls(
            hub_url=hub_url,
            auth_token=(
                args.auth_token
                if args.auth_token is not None
                else os.getenv("COTERM_AUTH_TOKEN", "") or resolve_saved_auth_token() or ""
            ),
            session_id=session_id,
            device_id=device_id or os.getenv("COTERM_DEVICE_ID", ""),
            agent_type=resolve_agent_type(args),
            claude_bin=args.claude_bin or os.getenv("COTERM_CLAUDE_BIN", "claude"),
            heartbeat_interval_sec=int(
                args.heartbeat_interval_sec or os.getenv("COTERM_HEARTBEAT_INTERVAL_SEC", "10")
            ),
            reconnect_base_sec=float(
                args.reconnect_base_sec or os.getenv("COTERM_RECONNECT_BASE_SEC", "3")
            ),
            reconnect_max_sec=float(
                args.reconnect_max_sec or os.getenv("COTERM_RECONNECT_MAX_SEC", "30")
            ),
            log_level=args.log_level or os.getenv("COTERM_LOG_LEVEL", "DEBUG" if args.verbose else "INFO"),
            permission_mode=args.permission_mode or os.getenv("COTERM_PERMISSION_MODE"),
            working_dir=resolve_workdir(args),
            allowed_tools=allowed_tools,
            verbose=bool(args.verbose) and not args.no_verbose,
        )


def resolve_agent_name(args: argparse.Namespace) -> str:
    candidate = args.agent or _agent_name_from_type(args.agent_type) or os.getenv("COTERM_AGENT", DEFAULT_AGENT)
    if candidate not in AGENT_NAME_TO_TYPE:
        raise SystemExit(f"unsupported agent '{candidate}'. Available agents: {', '.join(AGENT_NAME_TO_TYPE)}")
    return candidate


def resolve_agent_type(args: argparse.Namespace) -> str:
    return AGENT_NAME_TO_TYPE[resolve_agent_name(args)]


def resolve_workdir(args: argparse.Namespace) -> str:
    return args.workdir or args.working_dir or os.getenv("COTERM_WORKING_DIR") or os.getcwd()


def resolve_hub_base_url(args: argparse.Namespace) -> str:
    return (
        args.hub
        or os.getenv("COTERM_HUB")
        or os.getenv("COTERM_HUB_BASE_URL")
        or resolve_saved_hub()
        or DEFAULT_HUB_BASE_URL
    )


def _agent_name_from_type(agent_type: str | None) -> str | None:
    if agent_type == "claude_code":
        return "claude"
    if agent_type in AGENT_NAME_TO_TYPE:
        return agent_type
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coterm",
        description="Run Coterm locally and connect it to a Coterm hub.",
        epilog="Examples:\n  coterm\n  coterm claude --workdir ~/project/foo\n  coterm --hub http://127.0.0.1:18083",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "agent",
        nargs="?",
        choices=tuple(AGENT_NAME_TO_TYPE),
        help="Agent to run. Defaults to claude.",
    )
    parser.add_argument(
        "--hub",
        help=f"Hub base URL. Defaults to $COTERM_HUB or {DEFAULT_HUB_BASE_URL}.",
    )
    parser.add_argument(
        "--workdir",
        help="Working directory for the agent. Defaults to the current directory.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose startup and runtime logs.",
    )

    parser.add_argument("--hub-url", help=argparse.SUPPRESS)
    parser.add_argument("--auth-token", help=argparse.SUPPRESS)
    parser.add_argument("--session-id", help=argparse.SUPPRESS)
    parser.add_argument("--device-id", help=argparse.SUPPRESS)
    parser.add_argument("--agent-type", help=argparse.SUPPRESS)
    parser.add_argument("--claude-bin", help=argparse.SUPPRESS)
    parser.add_argument("--permission-mode", help=argparse.SUPPRESS)
    parser.add_argument("--working-dir", help=argparse.SUPPRESS)
    parser.add_argument("--log-level", help=argparse.SUPPRESS)
    parser.add_argument("--heartbeat-interval-sec", help=argparse.SUPPRESS)
    parser.add_argument("--reconnect-base-sec", help=argparse.SUPPRESS)
    parser.add_argument("--reconnect-max-sec", help=argparse.SUPPRESS)
    parser.add_argument("--allowed-tool", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--no-verbose", action="store_true", help=argparse.SUPPRESS)
    return parser
