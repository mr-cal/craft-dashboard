"""Puppeteer test helper utilities.

Provides functions to run Puppeteer scripts against a live deployment
and parse their output.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

# puppeteer is installed here (shared across tests)
NODE_MODULES = Path("/tmp/node_modules")

# This machine is a shared/resource-constrained host: memory and CPU are
# frequently contended by other users' processes, which can cause the
# headless Chrome subprocess to be OOM-killed or simply too slow to respond
# within the normal timeout (observed as a `-9` return code / TimeoutExpired,
# not a real product bug). Retrying a couple of times absorbs that
# environmental flakiness without masking genuine failures, which still fail
# after exhausting the retries.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 5


def run_puppeteer(
    script: str,
    *,
    base_url: str,
    timeout: int = 30,
) -> dict:
    """Run a Puppeteer script and return the parsed JSON result.

    The script should print a JSON object as its last line of output.
    The ``BASE_URL`` placeholder in the script is replaced with the
    actual base URL.

    Retries a couple of times on a subprocess timeout, since this can be
    the headless Chrome process getting OOM-killed or starved on a
    resource-constrained shared host rather than an actual test failure.

    Args:
        script: JavaScript (ESM) source code.
        base_url: The base URL of the deployed app.
        timeout: Maximum seconds to wait for the script.

    Returns:
        Parsed JSON from the script's stdout.

    Raises:
        RuntimeError: If the script fails or produces no JSON.
    """
    script = script.replace("BASE_URL", json.dumps(base_url))

    # Use a unique filename per invocation so concurrent/retried runs never
    # collide on the same temp path.
    script_path = Path(f"/tmp/e2e_puppeteer_test_{uuid.uuid4().hex}.mjs")
    script_path.write_text(script)

    try:
        last_timeout_error: subprocess.TimeoutExpired | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                result = subprocess.run(
                    ["node", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    env={
                        "PATH": "/usr/bin:/bin:/usr/local/bin",
                        "HOME": "/tmp",
                        "NODE_PATH": str(NODE_MODULES),
                    },
                )
            except subprocess.TimeoutExpired as exc:
                last_timeout_error = exc
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise RuntimeError(
                    f"Puppeteer script timed out after {timeout}s on all"
                    f" {_MAX_ATTEMPTS} attempts (likely Chrome OOM-killed or"
                    " starved on a resource-constrained host)"
                ) from last_timeout_error

            if result.returncode != 0:
                raise RuntimeError(
                    f"Puppeteer script failed (exit {result.returncode}):\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )

            # Parse the last JSON line from stdout
            lines = result.stdout.strip().splitlines()
            for output_line in reversed(lines):
                stripped_line = output_line.strip()
                if stripped_line.startswith(("{", "[")):
                    try:
                        return json.loads(stripped_line)
                    except json.JSONDecodeError:
                        continue

            raise RuntimeError(
                f"No JSON output from Puppeteer script:\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )
        # Unreachable: the loop above always returns or raises.
        raise RuntimeError("Puppeteer script did not run")
    finally:
        script_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Common Puppeteer script templates
# ---------------------------------------------------------------------------

PUPPETEER_PREAMBLE = textwrap.dedent("""\
    import puppeteer from 'puppeteer';

    const BASE = BASE_URL;
    const browser = await puppeteer.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        // Reduce Chrome's memory footprint on this resource-constrained
        // shared host, where headless Chrome has been observed getting
        // OOM-killed under memory pressure from other processes.
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--single-process',
        '--no-zygote',
        '--js-flags=--max-old-space-size=256',
      ],
    });
    const page = await browser.newPage();

""")

PUPPETEER_TEARDOWN = textwrap.dedent("""\

    await browser.close();
""")


def make_script(body: str) -> str:
    """Wrap a Puppeteer test body with browser launch/close boilerplate."""
    return PUPPETEER_PREAMBLE + body + PUPPETEER_TEARDOWN
