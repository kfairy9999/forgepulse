#!/usr/bin/env python3
"""GET-only smoke checks against the live Worker.

This file is also a CLI used by CI: `python tests/test_live_smoke.py --get-only`.
Mutation coverage for the live site is run locally after deploy, not in CI,
so production is not filled with test workspaces on every push.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://forgepulse.kfairley9999.workers.dev"


def live_url() -> str:
    return os.environ.get("FORGEPULSE_LIVE_URL", DEFAULT_URL).rstrip("/")


def get_json(path: str) -> tuple[int, dict[str, object] | str]:
    request = urllib.request.Request(
        live_url() + path,
        headers={"User-Agent": "ForgePulse-live-smoke/0.2", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def get_text(path: str) -> tuple[int, str]:
    request = urllib.request.Request(
        live_url() + path,
        headers={"User-Agent": "ForgePulse-live-smoke/0.2"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.read().decode("utf-8")


def run_get_only() -> int:
    status, health = get_json("/healthz")
    if status != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        print("healthz failed:", status, health)
        return 1
    if health.get("storage") not in {"d1", "sqlite"}:
        print("unexpected storage:", health)
        return 1
    if health.get("mode") != "durable":
        print("unexpected mode:", health)
        return 1
    home_status, home = get_text("/")
    if home_status != 200 or "ForgePulse" not in home:
        print("homepage failed:", home_status)
        return 1
    robots_status, robots = get_text("/robots.txt")
    if robots_status != 200 or "Allow: /" not in robots:
        print("robots failed:", robots_status, robots)
        return 1
    print("live GET smoke passed against", live_url(), "version", health.get("version"))
    return 0


def test_live_get_smoke() -> None:
    import pytest

    if os.environ.get("FORGEPULSE_LIVE_SMOKE") != "1":
        pytest.skip("Set FORGEPULSE_LIVE_SMOKE=1 to hit the deployed Worker from pytest")
    assert run_get_only() == 0


def main(argv: list[str]) -> int:
    if "--get-only" in argv:
        return run_get_only()
    print("Usage: python tests/test_live_smoke.py --get-only")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
