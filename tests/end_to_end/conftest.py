"""Pytest configuration and fixtures for end-to-end tests.

These tests build and start the craft-dashboard Docker Compose stack,
seed it with deterministic data, and run Puppeteer-based browser tests
against the live instance.

Requirements:
    - Docker Engine and Docker Compose plugin installed
    - Node.js with puppeteer installed in /tmp/node_modules/
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.end_to_end.seed_data import generate_seed_sql

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 120  # seconds (includes image build + migrations)
SEED_TIMEOUT = 30  # seconds

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = str(REPO_ROOT / "docker-compose.yml")
E2E_PROJECT = f"craft-dashboard-e2e-{os.getpid()}"


def _compose(*args: str, check: bool = True, timeout: int = 300) -> str:
    """Run a docker compose command scoped to the e2e project."""
    cmd = [
        "sudo",
        "docker",
        "compose",
        "-p",
        E2E_PROJECT,
        "-f",
        COMPOSE_FILE,
        *args,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )
    return result.stdout.strip()


def _get_app_port() -> int:
    """Discover the host port mapped to the app's container port 8000."""
    output = _compose("port", "app", "8000")
    # Output is like "0.0.0.0:32769" or "127.0.0.1:32769"
    _, _, port_str = output.rpartition(":")
    return int(port_str)


def _wait_for_health(base_url: str, timeout: int = HEALTH_TIMEOUT) -> bool:
    """Wait for the /health endpoint to return OK."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url() -> str:
    """Build and start the Docker Compose stack, return the base URL.

    Uses a unique project name to avoid conflicts with dev stacks.
    The stack is torn down (with volumes) after the test session.
    """
    logger.info("Starting Docker Compose stack (project: %s)", E2E_PROJECT)

    # Build and start — pass CRAFT_DASHBOARD_E2E through sudo
    subprocess.run(
        [
            "sudo",
            "env",
            "CRAFT_DASHBOARD_E2E=1",
            "docker",
            "compose",
            "-p",
            E2E_PROJECT,
            "-f",
            COMPOSE_FILE,
            "up",
            "--build",
            "-d",
        ],
        check=True,
        timeout=300,
        capture_output=True,
        text=True,
    )

    try:
        port = _get_app_port()
        url = f"http://localhost:{port}"
        logger.info("App available at %s", url)

        if not _wait_for_health(url):
            # Grab logs for debugging
            logs = _compose("logs", "--tail=50", check=False)
            raise RuntimeError(f"App did not become healthy at {url}\n\nLogs:\n{logs}")

        yield url
    finally:
        logger.info("Tearing down Docker Compose stack")
        _compose("down", "-v", "--remove-orphans", check=False, timeout=60)


@pytest.fixture(scope="session")
def seeded_url(base_url: str) -> str:
    """Seed the deployed app with test data and return the base URL."""
    sql = generate_seed_sql()
    resp = requests.post(
        f"{base_url}/e2e/seed",
        data=sql.encode(),
        headers={"Content-Type": "text/plain"},
        timeout=SEED_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Seeding failed: {resp.status_code} {resp.text}")
    return base_url


@pytest.fixture(scope="session")
def puppeteer_script_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a directory for Puppeteer test scripts."""
    return tmp_path_factory.mktemp("puppeteer")
