from __future__ import annotations

from forgepulse.models import PolicyRule, PullRequestEvent
from forgepulse.triage import triage_pull_request


def _pr(**overrides: object) -> PullRequestEvent:
    data: dict[str, object] = {
        "repo": "org/payments-core",
        "pr_number": 12,
        "author": "ada",
        "author_merged_prs": 8,
        "title": "Fix settlement rounding for weekend batches",
        "body": "Adds coverage around the weekend cutoff.",
        "files_changed": 3,
        "lines_added": 40,
        "lines_deleted": 8,
        "has_tests": True,
        "has_tests_source": "explicit",
        "ai_assisted": False,
    }
    data.update(overrides)
    return PullRequestEvent.model_validate(data)


def test_trusted_contributor_with_tests_fast_tracks(policy: PolicyRule) -> None:
    decision = triage_pull_request(_pr(), policy)
    assert decision.action == "fast-track"
    assert decision.signal_score >= 0.75
    assert "Trusted contributor" in " ".join(decision.reasons)
    assert "Includes test coverage" in " ".join(decision.reasons)


def test_first_time_large_change_without_tests_quarantines(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(
            author_merged_prs=0,
            has_tests=False,
            lines_added=120,
            lines_deleted=10,
            title="update code",
        ),
        policy,
    )
    assert decision.action == "quarantine-bot-check"
    assert decision.signal_score < 0.35


def test_protected_path_without_tests_blocks(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(
            has_tests=False,
            touches_critical_paths=["auth/session.py"],
            title="Tighten session cookie flags",
        ),
        policy,
    )
    assert decision.action == "block-policy"
    assert any("Protected paths lack tests" in reason for reason in decision.reasons)
    assert "@sec-team" in decision.suggested_reviewers


def test_protected_path_with_unknown_tests_does_not_block(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(
            has_tests=None,
            has_tests_source="unknown",
            changed_paths=[],
            touches_critical_paths=["auth/session.py"],
            title="Tighten session cookie flags",
        ),
        policy,
    )
    assert decision.action != "block-policy"
    assert any("test presence unknown" in reason.lower() for reason in decision.reasons)


def test_infers_tests_and_protected_paths_from_changed_paths(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(
            has_tests=None,
            has_tests_source="unknown",
            changed_paths=["src/settlement/engine.py", "tests/test_engine.py"],
        ),
        policy,
    )
    assert decision.has_tests is True
    assert decision.has_tests_source == "inferred"
    assert decision.action == "fast-track"


def test_unknown_tests_on_large_diff_are_flagged_not_penalized(policy: PolicyRule) -> None:
    with_unknown = triage_pull_request(
        _pr(
            has_tests=None,
            has_tests_source="unknown",
            changed_paths=[],
            lines_added=200,
            lines_deleted=20,
            author_merged_prs=8,
            title="Refactor settlement weekend cutoff handling",
        ),
        policy,
    )
    confirmed_missing = triage_pull_request(
        _pr(
            has_tests=False,
            has_tests_source="explicit",
            lines_added=200,
            lines_deleted=20,
            author_merged_prs=8,
            title="Refactor settlement weekend cutoff handling",
        ),
        policy,
    )
    assert with_unknown.signal_score > confirmed_missing.signal_score
    assert any("not inferred" in reason for reason in with_unknown.reasons)


def test_new_author_large_ai_pr_quarantines(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(
            author_merged_prs=0,
            ai_assisted=True,
            has_tests=True,
            lines_added=280,
            lines_deleted=40,
            title="Implement settlement engine rewrite",
        ),
        policy,
    )
    assert decision.action == "quarantine-bot-check"
    assert any("AI-assisted" in reason for reason in decision.reasons)


def test_policy_line_threshold_is_cited(policy: PolicyRule) -> None:
    decision = triage_pull_request(
        _pr(has_tests=False, lines_added=140, lines_deleted=20, title="Adjust batch window"),
        policy,
    )
    assert any("Policy requires tests above 150" in reason for reason in decision.reasons)


def test_allow_unverified_ai_skips_quarantine() -> None:
    policy = PolicyRule(rule_id="open", allow_unverified_ai_prs=True)
    decision = triage_pull_request(
        _pr(
            author_merged_prs=0,
            ai_assisted=True,
            has_tests=True,
            lines_added=10,
            title="Document the settlement cutoff",
        ),
        policy,
    )
    assert decision.action == "standard-review"
