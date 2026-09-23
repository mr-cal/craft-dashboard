#!/usr/bin/env python3
"""Benchmark prefill and generation speed for models on the LLM server.

Usage:
    uv run scripts/benchmark_llm.py

Reads server URL and optional credentials from .env:
    LOCAL_LLM_URL (default: http://localhost:11434/v1)
    LOCAL_LLM_API_KEY (optional bearer token)
    LOCAL_LLM_CA_CERT (optional custom CA certificate file)
"""

from __future__ import annotations

import os
import pathlib
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

BASE_URL: str = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434/v1").rstrip("/")
API_KEY: str = os.environ.get("LOCAL_LLM_API_KEY", "")
CA_CERT: str = os.environ.get("LOCAL_LLM_CA_CERT", "")


def get_client() -> httpx.Client:
    """Create an HTTP client configured for the LLM endpoint."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    verify: bool | str = CA_CERT if CA_CERT else True
    return httpx.Client(headers=headers, verify=verify, follow_redirects=True)


def main() -> None:
    """Fetch available models and benchmark prefill and generation tokens/second."""
    print(f"Connecting to {BASE_URL}...")
    with get_client() as client:
        try:
            resp = client.get(f"{BASE_URL}/models", timeout=15.0)
            resp.raise_for_status()
            models_resp: dict[str, Any] = resp.json()
            models: list[str] = [m["id"] for m in models_resp.get("data", [])]
        except Exception as exc:
            print(f"Failed to fetch models: {exc}")
            return

        if not models:
            print("No models reported by server.")
            return

        print(f"Found {len(models)} model(s): {', '.join(models)}\n")
        results: list[tuple[str, str, str, str]] = []
        prompt = "The quick brown fox jumps over the lazy dog. " * 50

        for model in models:
            print(f"Testing '{model}'...", end=" ", flush=True)
            t0 = time.perf_counter()
            try:
                resp = client.post(
                    f"{BASE_URL}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 30,
                        "temperature": 0.0,
                    },
                    timeout=60.0,
                )
                resp.raise_for_status()
                res = resp.json()
                elapsed = time.perf_counter() - t0
                timings = res.get("timings", {})
                usage = res.get("usage", {})

                prefill = timings.get("prompt_per_second")
                if prefill is None and usage.get("prompt_tokens"):
                    prefill = usage["prompt_tokens"] / (elapsed or 1)

                gen = timings.get("predicted_per_second")
                if gen is None and usage.get("completion_tokens"):
                    gen = usage["completion_tokens"] / (elapsed or 1)

                prefill_str = f"{prefill:.1f} t/s" if prefill is not None else "—"
                gen_str = f"{gen:.1f} t/s" if gen is not None else "—"

                print("Done.")
                results.append((model, prefill_str, gen_str, "OK"))
            except httpx.TimeoutException:
                print("Timeout (>60s)")
                results.append((model, "—", "—", "Timeout (>60s)"))
            except httpx.HTTPStatusError as exc:
                print(f"HTTP {exc.response.status_code}")
                results.append((model, "—", "—", f"HTTP {exc.response.status_code}"))
            except Exception as exc:
                print(f"Error: {exc}")
                results.append((model, "—", "—", str(exc)[:30]))

    print("\n" + "=" * 65)
    print(f"{'Model':<24} | {'Prefill':<12} | {'Generation':<12} | {'Status'}")
    print("-" * 65)
    for model, prefill_str, gen_str, status in results:
        print(f"{model:<24} | {prefill_str:<12} | {gen_str:<12} | {status}")
    print("=" * 65)


if __name__ == "__main__":
    main()
