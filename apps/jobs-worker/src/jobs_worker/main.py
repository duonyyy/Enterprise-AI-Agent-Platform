"""Jobs Worker Application Main Entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

from jobs_worker.invitation_consumer import InvitationOutboxConsumer
from jobs_worker.worker import JobWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jobs_worker")


def _parse_bool_env(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enterprise Agent Platform background worker.")
    parser.add_argument(
        "--mode",
        choices=("all", "jobs", "invitations"),
        default=os.environ.get("WORKER_MODE", "all").strip().lower() or "all",
        help="Worker loop mode: 'all', 'jobs', or 'invitations'.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process currently due work once and exit cleanly.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    invitations_enabled = _parse_bool_env("INVITATIONS_ENABLED", default=False)
    run_jobs = args.mode in {"all", "jobs"}
    run_invitations = args.mode == "invitations" or (args.mode == "all" and invitations_enabled)

    if args.mode == "invitations" and not invitations_enabled:
        raise RuntimeError("WORKER_MODE=invitations requires INVITATIONS_ENABLED=true")

    logger.info("Starting Background Jobs Worker Engine v2.0 (mode=%s)...", args.mode)

    job_worker = JobWorker.from_environment() if run_jobs else None
    if job_worker is not None:
        logger.info("JobWorker initialized with ID: %s", job_worker.worker_id)

    invitation_consumer = (
        InvitationOutboxConsumer.from_environment() if run_invitations else None
    )
    if invitation_consumer is not None:
        logger.info(
            "InvitationOutboxConsumer initialized with ID: %s",
            invitation_consumer.worker_id,
        )

    if args.once:
        if job_worker is not None:
            job_worker.reclaim_expired_leases()
            while True:
                job = job_worker.lease_next_job()
                if job is None:
                    break
                job_worker.process_job(job)
        if invitation_consumer is not None:
            processed = invitation_consumer.run_once()
            logger.info("InvitationOutboxConsumer --once processed %d event(s)", processed)
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_shutdown(signum: int, _frame: object) -> None:
        logger.info("Received termination signal %s, initiating graceful shutdown...", signum)
        if job_worker is not None:
            job_worker.stop()
        if invitation_consumer is not None:
            invitation_consumer.stop()

    try:
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
    except (ValueError, AttributeError):
        pass  # On Windows some signals may not be registerable in child threads

    async def _run_loops() -> None:
        tasks = []
        if job_worker is not None:
            tasks.append(job_worker.run_forever())
        if invitation_consumer is not None:
            tasks.append(invitation_consumer.run_forever())
        if tasks:
            await asyncio.gather(*tasks)

    try:
        loop.run_until_complete(_run_loops())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped by user/system signal")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
