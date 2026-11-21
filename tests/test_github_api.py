from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from forgepulse import github_api


class FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_enrich_from_github_reads_files_and_merged_count(monkeypatch: Any) -> None:
    def fake_urlopen(request: Request, timeout: float = 0) -> FakeResponse:
        url = request.full_url
        assert request.get_header("Authorization") == "Bearer ghp_test"
        if "/pulls/3/files" in url:
            return FakeResponse([{"filename": "src/app.py"}, {"filename": "tests/test_app.py"}])
        if "/search/issues" in url:
            return FakeResponse({"total_count": 9})
        raise AssertionError(url)

    monkeypatch.setattr(github_api, "urlopen", fake_urlopen)
    result = github_api.enrich_from_github("ghp_test", "acme/payments", 3, "ada")
    assert result["fetched"] is True
    assert result["paths"] == ["src/app.py", "tests/test_app.py"]
    assert result["author_merged_prs"] == 9


def test_enrich_from_github_reports_http_errors(monkeypatch: Any) -> None:
    def fake_urlopen(request: Request, timeout: float = 0) -> FakeResponse:
        raise HTTPError(request.full_url, 401, "Unauthorized", None, BytesIO(b"nope"))  # type: ignore[arg-type]

    monkeypatch.setattr(github_api, "urlopen", fake_urlopen)
    result = github_api.enrich_from_github("bad", "acme/payments", 3, "ada")
    assert result["fetched"] is False
    assert "401" in result["error"]
