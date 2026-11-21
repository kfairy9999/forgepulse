"""Fetch PR files and contributor history from the GitHub API."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

USER_AGENT = "ForgePulse/0.2"


def enrich_from_github(
    token: str, repo: str, pr_number: int, author: str, timeout: float = 8.0
) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        files = _get_json(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files?per_page=100",
            headers,
            timeout,
        )
        if not isinstance(files, list):
            return _fail("Unexpected GitHub files payload")
        paths = [
            str(item["filename"])
            for item in files
            if isinstance(item, dict) and item.get("filename")
        ]
        merged: int | None = None
        if author and author != "unknown":
            query = quote(f"repo:{repo} author:{author} is:pr is:merged")
            search = _get_json(
                f"https://api.github.com/search/issues?q={query}&per_page=1",
                headers,
                timeout,
            )
            if isinstance(search, dict) and isinstance(search.get("total_count"), int):
                merged = search["total_count"]
        return {"paths": paths, "author_merged_prs": merged, "fetched": True}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:180]
        return _fail(f"GitHub files request failed ({exc.code}): {body}")
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return _fail(str(exc) or "GitHub request failed")


def _fail(message: str) -> dict[str, Any]:
    return {"paths": [], "author_merged_prs": None, "fetched": False, "error": message}


def _get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
