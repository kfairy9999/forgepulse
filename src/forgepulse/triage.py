"""TriageGate: score a pull request and choose a review action."""

from __future__ import annotations

from forgepulse.infer import infer_has_tests, protected_hits
from forgepulse.models import (
    PolicyRule,
    PullRequestEvent,
    TestEvidence,
    TriageAction,
    TriageDecision,
)


def enrich_pull_request(pr: PullRequestEvent, policy: PolicyRule) -> PullRequestEvent:
    """Fill test evidence and critical paths when the caller left them blank."""
    has_tests = pr.has_tests
    source: TestEvidence = pr.has_tests_source
    if has_tests is None or source == "unknown":
        inferred, inferred_source = infer_has_tests(pr.changed_paths or None)
        if inferred_source == "inferred":
            has_tests = inferred
            source = "inferred"
        elif has_tests is None:
            source = "unknown"
        else:
            source = "explicit"
    elif source != "inferred":
        source = "explicit"

    paths = list(pr.touches_critical_paths)
    if not paths and pr.changed_paths:
        paths = protected_hits(pr.changed_paths, policy.protected_paths)

    files_changed = pr.files_changed
    if pr.changed_paths:
        files_changed = max(files_changed, len(pr.changed_paths))

    return pr.model_copy(
        update={
            "has_tests": has_tests,
            "has_tests_source": source,
            "touches_critical_paths": paths,
            "files_changed": files_changed,
        }
    )


def triage_pull_request(pr: PullRequestEvent, policy: PolicyRule) -> TriageDecision:
    event = enrich_pull_request(pr, policy)
    score = 0.5
    reasons: list[str] = []
    churn = event.lines_added + event.lines_deleted

    if event.author_merged_prs is None:
        reasons.append("Contributor history unknown")
    elif event.author_merged_prs >= 5:
        score += 0.2
        reasons.append(f"Trusted contributor ({event.author_merged_prs} merged PRs)")
    elif event.author_merged_prs == 0:
        score -= 0.15
        reasons.append("First-time contributor")

    if event.has_tests is True:
        score += 0.15
        reasons.append("Includes test coverage")
    elif event.has_tests is False and churn > 80:
        score -= 0.15
        reasons.append("Large change without tests")
    elif event.has_tests is None:
        reasons.append("Test presence not inferred from payload (no file list)")

    if event.has_tests is False and churn >= policy.require_tests_above_lines:
        reasons.append(
            f"Policy requires tests above {policy.require_tests_above_lines} changed lines"
        )

    if (
        event.ai_assisted is True
        and (event.author_merged_prs or 0) == 0
        and event.author_merged_prs is not None
        and churn > 300
    ):
        score -= 0.25
        reasons.append("Large unreviewed AI-assisted PR from new author")

    title = event.title.strip()
    if len(title) < 12 or title.lower().startswith(("fix issue", "update code")):
        score -= 0.15
        reasons.append("Low-specificity PR title")

    score = max(0.0, min(1.0, round(score, 2)))
    hits = protected_hits(event.touches_critical_paths, policy.protected_paths)
    if not hits and event.changed_paths:
        hits = protected_hits(event.changed_paths, policy.protected_paths)

    if hits and event.has_tests is False:
        return TriageDecision(
            pr_number=event.pr_number,
            repo=event.repo,
            signal_score=score,
            action="block-policy",
            reasons=[*reasons, f"Protected paths lack tests: {', '.join(hits)}"],
            suggested_reviewers=list(policy.protected_reviewers),
            has_tests=event.has_tests,
            has_tests_source=event.has_tests_source,
        )

    if hits and event.has_tests is None:
        reasons.append(f"Protected paths touched; test presence unknown: {', '.join(hits)}")

    first_time = event.author_merged_prs == 0
    if event.ai_assisted is True and not policy.allow_unverified_ai_prs and first_time:
        action: TriageAction = "quarantine-bot-check"
    elif score >= 0.75:
        action = "fast-track"
    elif score < 0.35:
        action = "quarantine-bot-check"
    else:
        action = "standard-review"

    reviewers = list(policy.protected_reviewers) if hits else list(policy.default_reviewers)
    return TriageDecision(
        pr_number=event.pr_number,
        repo=event.repo,
        signal_score=score,
        action=action,
        reasons=reasons or ["Standard baseline signals"],
        suggested_reviewers=reviewers,
        has_tests=event.has_tests,
        has_tests_source=event.has_tests_source,
    )
