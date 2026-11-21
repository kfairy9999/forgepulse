"""Shared domain models. These are the public HTTP shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TriageAction = Literal[
    "fast-track",
    "standard-review",
    "quarantine-bot-check",
    "block-policy",
]
TestEvidence = Literal["explicit", "inferred", "unknown"]
RunnerPool = Literal["ephemeral-cloud", "bare-metal", "fallback-alt"]


class PolicyRule(BaseModel):
    """Portable policy evaluated outside GitHub-only UI settings."""

    rule_id: str
    require_tests_above_lines: int = 150
    protected_paths: list[str] = Field(default_factory=lambda: ["infra/", "auth/", "billing/"])
    allow_unverified_ai_prs: bool = False
    default_reviewers: list[str] = Field(default_factory=lambda: ["@maintainers"])
    protected_reviewers: list[str] = Field(default_factory=lambda: ["@sec-team", "@staff-eng"])

    @field_validator("rule_id")
    @classmethod
    def _rule_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("rule_id is required")
        return cleaned

    @field_validator("require_tests_above_lines")
    @classmethod
    def _non_negative_lines(cls, value: int) -> int:
        if value < 0:
            raise ValueError("require_tests_above_lines must be >= 0")
        return value

    @field_validator("protected_paths")
    @classmethod
    def _normalize_paths(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class PullRequestEvent(BaseModel):
    repo: str
    pr_number: int
    author: str
    author_merged_prs: int | None = None
    title: str
    body: str = ""
    files_changed: int = 1
    lines_added: int = 0
    lines_deleted: int = 0
    has_tests: bool | None = None
    has_tests_source: TestEvidence = "unknown"
    ai_assisted: bool | None = None
    touches_critical_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)


class TriageDecision(BaseModel):
    pr_number: int
    repo: str
    signal_score: float
    action: TriageAction
    reasons: list[str]
    suggested_reviewers: list[str]
    has_tests: bool | None = None
    has_tests_source: TestEvidence = "unknown"


class CIJobRequest(BaseModel):
    job_id: str
    repo: str
    branch: str
    priority: int = Field(default=50, ge=1, le=100)
    estimated_seconds: int = 120
    preferred_pool: RunnerPool = "ephemeral-cloud"
    github_hosted_healthy: bool = True


class QueuedJob(BaseModel):
    job_id: str
    repo: str
    branch: str
    priority: int
    routed_pool: RunnerPool
    route_reason: str
    enqueue_seq: int
    enqueued_at: str


class SymbolIndexEntry(BaseModel):
    repo: str
    symbol: str
    file_path: str
    owners: list[str]
    related_prs: list[int] = Field(default_factory=list)


class BlastRadius(BaseModel):
    source_repo: str
    downstream_repos: list[str]
    total_impacted: int
