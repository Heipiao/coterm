from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class ManagedProcess:
    command: list[str]
    process: asyncio.subprocess.Process
    stdout_queue: asyncio.Queue[str | None]
    stderr_queue: asyncio.Queue[str | None]
    stdin_queue: asyncio.Queue[str | None]
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class ProcessManager:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    async def start(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ManagedProcess:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        managed = ManagedProcess(
            command=command,
            process=process,
            stdout_queue=asyncio.Queue(),
            stderr_queue=asyncio.Queue(),
            stdin_queue=asyncio.Queue(),
        )
        managed.tasks.extend(
            [
                asyncio.create_task(self._pump_stream(process.stdout, managed.stdout_queue, "stdout")),
                asyncio.create_task(self._pump_stream(process.stderr, managed.stderr_queue, "stderr")),
                asyncio.create_task(self._stdin_writer(managed)),
            ]
        )
        return managed

    async def stop(self, managed: ManagedProcess) -> int:
        await managed.stdin_queue.put(None)

        if managed.process.returncode is None:
            managed.process.terminate()

        try:
            returncode = await asyncio.wait_for(managed.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._logger.warning("process did not exit after terminate, killing it")
            managed.process.kill()
            returncode = await managed.process.wait()

        for task in managed.tasks:
            task.cancel()
        await asyncio.gather(*managed.tasks, return_exceptions=True)
        return returncode

    async def _pump_stream(
        self,
        stream: asyncio.StreamReader | None,
        queue: asyncio.Queue[str | None],
        stream_name: str,
    ) -> None:
        if stream is None:
            await queue.put(None)
            return

        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                await queue.put(line.decode("utf-8", errors="replace").rstrip("\n"))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("failed to read %s from child process", stream_name)
        finally:
            await queue.put(None)

    async def _stdin_writer(self, managed: ManagedProcess) -> None:
        writer = managed.process.stdin
        if writer is None:
            return

        try:
            while True:
                item = await managed.stdin_queue.get()
                if item is None:
                    try:
                        writer.close()
                    except Exception:
                        self._logger.debug("failed to close child stdin cleanly", exc_info=True)
                    break
                writer.write(item.encode("utf-8"))
                await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("failed to write to child stdin")
