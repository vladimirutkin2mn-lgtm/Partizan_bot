from __future__ import annotations

# Phase 8 may use this manifest as an allow/deny boundary for its isolated worktree.
# These files define benchmark truth, evaluation and production safety and must not be
# editable by the autonomous self-research candidate loop.
SELF_RESEARCH_PROTECTED_PATHS = frozenset(
    {
        "app/self_research_benchmark.py",
        "app/self_research_benchmark_schemas.py",
        "app/self_research_evaluator.py",
        "app/self_research_policy.py",
        ".github/workflows/ci.yml",
        ".github/workflows/deploy-production.yml",
        "app/autonomy_service.py",
        "app/paid_control.py",
        "app/runtime_store.py",
    }
)

# The first Phase 8 implementation should begin with a very small allowlist and expand
# only through human-reviewed changes to this protected file.
SELF_RESEARCH_INITIAL_EDITABLE_PATHS = frozenset(
    {
        "app/distribution_play_planner.py",
    }
)


def is_self_research_path_protected(path: str) -> bool:
    return path.strip().replace("\\", "/") in SELF_RESEARCH_PROTECTED_PATHS


def is_self_research_path_editable(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    return (
        normalized in SELF_RESEARCH_INITIAL_EDITABLE_PATHS
        and normalized not in SELF_RESEARCH_PROTECTED_PATHS
    )
