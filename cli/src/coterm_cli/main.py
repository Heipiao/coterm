from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .commands import ROOT_COMMANDS, build_root_parser, dispatch_command


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def async_main() -> None:
    from .bootstrap import bootstrap_startup, print_startup_banner, resolve_device_id
    from .claude_adapter import ClaudeRemoteAdapter
    from .config import CLIConfig, build_parser, resolve_agent_name
    from .hub_client import HubClient
    from .process import ProcessManager
    from .runtime import AgentRuntime
    from .session import SessionManager

    args = build_parser().parse_args()
    agent_name = resolve_agent_name(args)
    if agent_name != "claude":
        raise SystemExit(f"agent '{agent_name}' is not implemented yet; only 'claude' is currently supported")

    if bool(args.hub_url) != bool(args.session_id):
        raise SystemExit("legacy mode requires both --hub-url and --session-id")

    if args.hub_url and args.session_id:
        config = CLIConfig.from_args(
            args,
            hub_url=args.hub_url,
            session_id=args.session_id,
            device_id=resolve_device_id(args.device_id),
        )
    else:
        startup = bootstrap_startup(args)
        print_startup_banner(startup)
        config = CLIConfig.from_args(
            args,
            hub_url=startup.ws_cli_url,
            session_id=startup.session_id,
            device_id=startup.device_id,
        )

    configure_logging(config.log_level)

    logger = logging.getLogger("coterm_cli")
    process_manager = ProcessManager(logger.getChild("process"))
    session_manager = SessionManager(config.session_id, config.device_id, config.agent_type)
    hub_client = HubClient(config, logger.getChild("hub"))
    adapter = ClaudeRemoteAdapter(config, process_manager, logger.getChild("adapter"))
    runtime = AgentRuntime(session_manager, hub_client, adapter, logger.getChild("runtime"))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stop_event.set)

    await runtime.start()
    hub_task = asyncio.create_task(hub_client.run_forever())

    done, pending = await asyncio.wait(
        [hub_task, asyncio.create_task(stop_event.wait()), asyncio.create_task(runtime.shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await runtime.stop()
    await hub_client.stop()
    await asyncio.gather(*done, return_exceptions=True)


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        asyncio.run(async_main())
        return

    first = argv[0]
    if first in {"-h", "--help", "help"}:
        build_root_parser().print_help()
        return

    if first in ROOT_COMMANDS:
        exit_code = dispatch_command(argv)
        if exit_code == 101:
            sys.argv = [sys.argv[0], *argv[1:]]
            asyncio.run(async_main())
            return
        raise SystemExit(exit_code)

    asyncio.run(async_main())


if __name__ == "__main__":
    main()
