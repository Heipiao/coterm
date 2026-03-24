from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import threading

from .app import MockHubApp
from .http_api import build_http_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coterm mock hub")
    parser.add_argument("--ws-host", default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--http-host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--log-level", default="INFO")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def async_main() -> None:
    args = build_parser().parse_args()
    configure_logging(args.log_level)
    logger = logging.getLogger("coterm_mock_hub")

    app = MockHubApp(
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        http_host=args.http_host,
        http_port=args.http_port,
        logger=logger.getChild("app"),
    )
    await app.start_ws()

    http_server = build_http_server(app, args.http_host, args.http_port, logger.getChild("http"))
    http_thread = threading.Thread(target=http_server.serve_forever, name="coterm-mock-hub-http", daemon=True)
    http_thread.start()
    logger.info("mock hub http listening on http://%s:%s", args.http_host, args.http_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stop_event.set)

    await stop_event.wait()

    http_server.shutdown()
    http_server.server_close()
    http_thread.join(timeout=2)
    await app.stop_ws()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
