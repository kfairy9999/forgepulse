"""GitHub webhook signature checks and pull-request mapping."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from forgepulse.config import Settings
from forgepulse.infer import extract_filenames, infer_ai_assisted, infer_has_tests, protected_hits
from forgepulse.models import PolicyRule, PullRequestEvent


class WebhookAuthError(Exception):
    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def verify_github_signature(
    raw_body: bytes,
    signature_header: str | None,
    settings: Settings,
) -> None:
    """Reject unsigned or badly signed webhooks unless unsigned dev mode is on.

    The unsigned bypass is disabled whenever a secret is configured.
    """
    secret = settings.webhook_secret
    if secret:
        _require_matching_signature(raw_body, signature_header, secret)
        return
    if settings.allow_unsigned:
        return
    raise WebhookAuthError("Webhook secret is not configured")


def _require_matching_signature(raw_body: bytes, signature_header: str | None, secret: str) -> None:
    if not signature_header or not signature_header.startswith("sha256="):
        raise WebhookAuthError("Missing or malformed signature")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.strip()
    if len(provided) != len(expected) or not hmac.compare_digest(expected, provided):
        raise WebhookAuthError("Invalid webhook signature")


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def pull_request_from_webhook(
    payload: dict[str, Any], policy: PolicyRule
) -> PullRequestEvent | None:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        return None
    repository = payload.get("repository")
    repo_name = "unknown/repo"
    if isinstance(repository, dict) and isinstance(repository.get("full_name"), str):
        repo_name = repository["full_name"]

    paths = extract_filenames(payload) or []
    has_tests, source = infer_has_tests(paths or None)
    user = pull_request.get("user")
    author = "unknown"
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        author = user["login"]

    merged = _optional_int(pull_request.get("author_merged_prs", payload.get("author_merged_prs")))
    additions = _int(pull_request.get("additions"), 0)
    deletions = _int(pull_request.get("deletions"), 0)
    files_changed = _int(pull_request.get("changed_files"), len(paths) or 1)
    critical = protected_hits(paths, policy.protected_paths)

    return PullRequestEvent(
        repo=repo_name,
        pr_number=_int(pull_request.get("number"), 0),
        author=author,
        author_merged_prs=merged,
        title=str(pull_request.get("title") or ""),
        body=str(pull_request.get("body") or ""),
        files_changed=files_changed,
        lines_added=additions,
        lines_deleted=deletions,
        has_tests=has_tests,
        has_tests_source=source,
        ai_assisted=infer_ai_assisted(payload, None),
        touches_critical_paths=critical,
        changed_paths=paths,
    )


def _int(value: object, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
