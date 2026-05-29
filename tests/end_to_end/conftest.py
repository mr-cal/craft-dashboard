"""Pytest configuration and fixtures for end-to-end tests.

These tests launch an ephemeral LXD VM, deploy the craft-dashboard
application, seed it with deterministic data, and run Puppeteer-based
browser tests against the live instance.

Requirements:
    - LXD installed and configured (``lxc`` CLI available)
    - ``make deploy-vm`` must work (Ansible + secrets.env configured)
    - Node.js with puppeteer installed in /tmp/node_modules/
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.end_to_end.seed_data import generate_seed_sql

logger = logging.getLogger(__name__)

# VM and image configuration
VM_NAME = "craft-dashboard-e2e"
VM_IMAGE = "ubuntu:24.04"
DEPLOY_TIMEOUT = 300  # seconds
HEALTH_TIMEOUT = 60  # seconds
SEED_TIMEOUT = 30  # seconds

# Path to the repository root
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], *, check: bool = True, timeout: int = 120) -> str:
    """Run a subprocess and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )
    return result.stdout.strip()


def _vm_exists(name: str) -> bool:
    """Check if a VM with the given name exists."""
    try:
        output = _run(["lxc", "list", name, "--format=json"])
        vms = json.loads(output)
        return any(vm["name"] == name for vm in vms)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return False


def _get_vm_ip(name: str) -> str | None:
    """Get the IPv4 address of a VM."""
    try:
        output = _run(["lxc", "list", name, "--format=json"])
        vms = json.loads(output)
        for vm in vms:
            if vm["name"] == name:
                state = vm.get("state") or {}
                network = state.get("network") or {}
                for net_name, net in network.items():
                    if net_name == "lo":
                        continue
                    for addr in net.get("addresses", []):
                        if addr["family"] == "inet" and addr["scope"] == "global":
                            return addr["address"]
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        pass
    return None


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


def _deploy_to_vm(vm_name: str) -> str:
    """Deploy the app to a VM and return its base URL.

    This modifies the Ansible inventory to point at the E2E VM, sets the
    CRAFT_DASHBOARD_E2E=1 env var, deploys, and waits for health.
    """
    ip = _get_vm_ip(vm_name)
    if not ip:
        raise RuntimeError(f"Could not get IP for VM {vm_name}")

    # Create a temporary inventory pointing at this VM
    inventory_path = REPO_ROOT / "provisioning" / "inventory_e2e.ini"
    inventory_path.write_text(f"[dashboard]\n{vm_name} ansible_connection=lxd\n")

    try:
        # Set E2E env var in the deploy environment
        env = os.environ.copy()
        # Source secrets.env
        secrets_path = REPO_ROOT / "provisioning" / "secrets.env"
        if secrets_path.exists():
            for raw_line in secrets_path.read_text().splitlines():
                stripped_line = raw_line.strip()
                if (
                    stripped_line
                    and not stripped_line.startswith("#")
                    and "=" in stripped_line
                ):
                    key, _, val = stripped_line.partition("=")
                    env[key.strip()] = val.strip()

        env["CRAFT_DASHBOARD_E2E"] = "1"

        subprocess.run(
            [
                "ansible-playbook",
                "playbook.yml",
                "--skip-tags",
                "ssl",
                "-i",
                str(inventory_path),
            ],
            cwd=str(REPO_ROOT / "provisioning"),
            env=env,
            check=True,
            timeout=DEPLOY_TIMEOUT,
            capture_output=True,
            text=True,
        )
    finally:
        inventory_path.unlink(missing_ok=True)

    base_url = f"http://{ip}"
    if not _wait_for_health(base_url):
        raise RuntimeError(f"App did not become healthy at {base_url}")

    return base_url


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_vm() -> str:
    """Launch (or reuse) an ephemeral LXD VM and return its name.

    The VM is deleted at the end of the test session.
    """
    name = VM_NAME

    if _vm_exists(name):
        logger.info("Reusing existing VM %s", name)
    else:
        logger.info("Launching ephemeral VM %s", name)
        _run(
            [
                "lxc",
                "launch",
                VM_IMAGE,
                name,
                "--vm",
                "--ephemeral",
                "-c",
                "limits.cpu=2",
                "-c",
                "limits.memory=4GB",
            ],
            timeout=120,
        )
        # Wait for the VM to get an IP
        deadline = time.time() + 120
        while time.time() < deadline:
            if _get_vm_ip(name):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"VM {name} did not get an IP within 120s")

        # Wait for cloud-init to finish
        with contextlib.suppress(subprocess.CalledProcessError):
            _run(
                ["lxc", "exec", name, "--", "cloud-init", "status", "--wait"],
                timeout=300,
            )

    yield name

    # Cleanup: stop ephemeral VM (auto-deletes)
    if _vm_exists(name):
        logger.info("Stopping ephemeral VM %s", name)
        with contextlib.suppress(subprocess.TimeoutExpired):
            _run(["lxc", "stop", name, "--force"], check=False, timeout=30)


@pytest.fixture(scope="session")
def base_url(e2e_vm: str) -> str:
    """Deploy the app to the E2E VM and return the base URL."""
    return _deploy_to_vm(e2e_vm)


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
