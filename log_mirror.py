import logging
import subprocess
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from config import TRAVELNET_TAILSCALE_HOST, TRAVELNET_SSH_USER

MIRROR_CONTAINERS = [
    "travelnet",
    "server-prefect-worker-1",
    "prefect-server",
]

MIRROR_LOG_DIR = "/home/dan/watchdog/mirror_logs"
os.makedirs(MIRROR_LOG_DIR, exist_ok=True)


def _make_logger(container_name: str) -> logging.Logger:
    logger = logging.getLogger(f"mirror.{container_name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = RotatingFileHandler(
        f"{MIRROR_LOG_DIR}/{container_name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger

_loggers: dict[str, logging.Logger] = {
    name: _make_logger(name) for name in MIRROR_CONTAINERS
}

_loggers: dict[str, logging.Logger] = {
    name: _make_logger(name) for name in MIRROR_CONTAINERS
}


def mirror_travelnet_logs() -> None:
    ts = datetime.now(timezone.utc).isoformat()

    for container in MIRROR_CONTAINERS:
        result = subprocess.run(
            [
                "ssh",
                "-i", "/home/dan/.ssh/watchdog_id",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
                f"docker logs --since 6m {container} 2>&1 | tail -200",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

        log = _loggers[container]
        log.info(f"\n=== {ts} ===")

        if result.returncode == 0:
            log.info(result.stdout.strip() or "(no output)")
        else:
            log.warning(f"[SSH FAILED] {result.stderr.strip()}")