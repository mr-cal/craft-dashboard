"""Puppeteer test helper utilities.

Provides functions to run Puppeteer scripts against a live deployment
and parse their output.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

# puppeteer is installed here (shared across tests)
NODE_MODULES = Path("/tmp/node_modules")


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

    # Write script to a temp file
    script_path = Path("/tmp/e2e_puppeteer_test.mjs")
    script_path.write_text(script)

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
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    });
    const page = await browser.newPage();

""")

PUPPETEER_TEARDOWN = textwrap.dedent("""\

    await browser.close();
""")


def make_script(body: str) -> str:
    """Wrap a Puppeteer test body with browser launch/close boilerplate."""
    return PUPPETEER_PREAMBLE + body + PUPPETEER_TEARDOWN
