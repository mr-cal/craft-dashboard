#!/usr/bin/env python3
"""Start a local, seeded craft-dashboard stack for manual verification.

The end-to-end suite already builds the compose stack and seeds it with
deterministic data, then tears it down. This exposes the same flow as a
long-lived stack so a UI change can be looked at in a browser without
touching production.

    scripts/dev_stack.py up      # build, start, seed, print the URL
    scripts/dev_stack.py seed    # re-seed a running stack
    scripts/dev_stack.py logs
    scripts/dev_stack.py down    # stop and delete the volumes

The stack uses its own compose project name, so it never collides with an
e2e run or a production deployment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.end_to_end.seed_data import generate_seed_sql  # noqa: E402

PROJECT = "craft-dashboard-dev"
COMPOSE_FILE = str(REPO_ROOT / "docker-compose.yml")
HEALTH_TIMEOUT = 180
SEED_TIMEOUT = 30
HTTP_OK = 200


def _compose_cmd() -> list[str]:
    """Return the compose invocation, honouring CONTAINER_ENGINE."""
    engine = os.environ.get("CONTAINER_ENGINE")
    if not engine:
        engine = "docker" if shutil.which("docker") else "podman"
    prefix = ["sudo"] if engine == "docker" and os.geteuid() != 0 else []
    return [*prefix, engine, "compose", "-p", PROJECT, "-f", COMPOSE_FILE]


def _compose(*args: str, check: bool = True, timeout: int = 300) -> str:
    result = subprocess.run(
        [*_compose_cmd(), *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )
    return result.stdout.strip()


def _app_url() -> str:
    output = _compose("port", "app", "8000")
    _, _, port = output.rpartition(":")
    return f"http://localhost:{int(port)}"


def _wait_for_health(url: str) -> bool:
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.status_code == HTTP_OK and resp.json().get("status") == "ok":
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def _seed(url: str) -> None:
    resp = requests.post(
        f"{url}/e2e/seed",
        data=generate_seed_sql().encode(),
        headers={"Content-Type": "text/plain"},
        timeout=SEED_TIMEOUT,
    )
    if resp.status_code != HTTP_OK:
        msg = f"Seeding failed: {resp.status_code} {resp.text}"
        raise SystemExit(msg)


def _up() -> None:
    print(f"Building and starting the {PROJECT} stack...")
    cmd = _compose_cmd()
    if cmd[0] == "sudo":
        # sudo strips the environment, so pass the flag through explicitly.
        cmd = ["sudo", "env", "CRAFT_DASHBOARD_E2E=1", *cmd[1:]]
    else:
        os.environ["CRAFT_DASHBOARD_E2E"] = "1"
    subprocess.run([*cmd, "up", "--build", "-d"], check=True, timeout=600)

    url = _app_url()
    print(f"Waiting for {url}/health ...")
    if not _wait_for_health(url):
        print(_compose("logs", "--tail=50", check=False))
        raise SystemExit("App did not become healthy")

    _seed(url)
    print(f"\nSeeded stack ready at {url}")
    print(f"Stop it with: {Path(__file__).relative_to(REPO_ROOT)} down")


def main() -> None:
    """Dispatch the requested stack command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "down", "seed", "logs", "url"])
    args = parser.parse_args()

    if args.command == "up":
        _up()
    elif args.command == "down":
        _compose("down", "-v", "--remove-orphans", check=False, timeout=180)
        print("Stack removed.")
    elif args.command == "seed":
        _seed(_app_url())
        print("Re-seeded.")
    elif args.command == "logs":
        print(_compose("logs", "--tail=200", check=False))
    elif args.command == "url":
        print(_app_url())


if __name__ == "__main__":
    main()
