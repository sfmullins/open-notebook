"""CLI worker for the PostgreSQL command queue."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import pkgutil
import signal
import socket
import os

from loguru import logger

from surreal_commands import claim_job, run_claimed_job


def import_command_modules(package_name: str) -> None:
    """Import a package and all importable submodules so decorators register."""
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return
    prefix = f"{package.__name__}."
    for module in pkgutil.walk_packages(package_path, prefix):
        importlib.import_module(module.name)


async def worker_loop(worker_id: str, lease_seconds: int, poll_seconds: float, stop: asyncio.Event) -> None:
    while not stop.is_set():
        job = await claim_job(worker_id, lease_seconds)
        if not job:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue
            break
        await run_claimed_job(job, worker_id, lease_seconds)


async def run_worker(max_tasks: int, lease_seconds: int, poll_seconds: float) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    host = socket.gethostname()
    pid = os.getpid()
    logger.info(
        f"Starting PostgreSQL command worker: concurrency={max_tasks}, lease={lease_seconds}s"
    )
    tasks = [
        asyncio.create_task(
            worker_loop(f"{host}:{pid}:{idx}", lease_seconds, poll_seconds, stop)
        )
        for idx in range(max_tasks)
    ]
    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Open Notebook PostgreSQL command worker")
    parser.add_argument("--import-modules", default="commands")
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    args = parser.parse_args()

    for package in [part.strip() for part in args.import_modules.split(",") if part.strip()]:
        import_command_modules(package)

    asyncio.run(
        run_worker(
            max(1, args.max_tasks),
            max(30, args.lease_seconds),
            max(0.1, args.poll_seconds),
        )
    )


if __name__ == "__main__":
    main()
