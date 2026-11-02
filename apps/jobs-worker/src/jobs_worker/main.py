"""Jobs Worker Application Main Entrypoint."""

import asyncio
import logging
import signal
from jobs_worker.worker import JobWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jobs_worker")


def main():
    logger.info("Starting Background Jobs Worker Engine v2.0...")
    worker = JobWorker.from_environment()
    logger.info(f"Worker initialized with ID: {worker.worker_id}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_shutdown(signum, frame):
        logger.info(f"Received termination signal {signum}, initiating graceful shutdown...")
        worker.stop()

    try:
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
    except (ValueError, AttributeError):
        pass  # On Windows some signals may not be registerable in child threads

    try:
        loop.run_until_complete(worker.run_forever())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped by user/system signal")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
