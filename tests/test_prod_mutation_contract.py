from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "check_prod_mutation_contract.sh"
WRAPPER = ROOT / "tools" / "compose_shared_host.sh"
WATCHDOG = ROOT / ".github" / "workflows" / "edge-watchdog.yml"

PROD_FACING_WORKFLOW = """
name: Example
on:
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ssh "${{ secrets.DEPLOY_HOST }}" 'cd x && bash tools/compose_shared_host.sh ps'
"""


def _fixture_repo(tmp_path: Path, workflow: str, tool: str | None = None) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / ".github" / "workflows" / "example.yml").write_text(workflow, encoding="utf-8")
    if tool is not None:
        (tmp_path / "tools" / "example.sh").write_text(tool, encoding="utf-8")
    return tmp_path


def _run_guard(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GUARD), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_guard_accepts_this_repository() -> None:
    result = _run_guard(ROOT)

    assert result.returncode == 0, result.stderr
    assert "production-mutation-contract=ok" in result.stdout


def test_guard_rejects_production_access_from_pull_request(tmp_path: Path) -> None:
    workflow = PROD_FACING_WORKFLOW.replace("  workflow_dispatch:", "  pull_request:")

    result = _run_guard(_fixture_repo(tmp_path, workflow))

    assert result.returncode == 1
    assert "must not be triggered by pull_request" in result.stderr


def test_guard_rejects_production_access_without_environment(tmp_path: Path) -> None:
    workflow = PROD_FACING_WORKFLOW.replace("    environment: production\n", "")

    result = _run_guard(_fixture_repo(tmp_path, workflow))

    assert result.returncode == 1
    assert "must declare environment: production" in result.stderr


def test_guard_rejects_workflow_that_pins_its_own_compose_files(tmp_path: Path) -> None:
    # The exact command that detached the API from the shared proxy network.
    workflow = PROD_FACING_WORKFLOW.replace(
        "bash tools/compose_shared_host.sh ps",
        "docker compose -f docker-compose.prod.yml up -d --force-recreate api",
    )

    result = _run_guard(_fixture_repo(tmp_path, workflow))

    assert result.returncode == 1
    assert "instead of naming compose files itself" in result.stderr


def test_guard_rejects_container_mutation_without_public_verification(tmp_path: Path) -> None:
    workflow = PROD_FACING_WORKFLOW.replace(
        "bash tools/compose_shared_host.sh ps",
        "bash tools/compose_shared_host.sh up -d --no-deps api",
    )

    result = _run_guard(_fixture_repo(tmp_path, workflow))

    assert result.returncode == 1
    assert "must verify public reachability afterwards" in result.stderr


def test_guard_accepts_container_mutation_that_verifies_public_edge(tmp_path: Path) -> None:
    workflow = PROD_FACING_WORKFLOW.replace(
        "bash tools/compose_shared_host.sh ps",
        "bash tools/compose_shared_host.sh up -d --no-deps api\n"
        "      - run: bash tools/verify_public_edge.sh",
    )

    result = _run_guard(_fixture_repo(tmp_path, workflow))

    assert result.returncode == 0, result.stderr


def test_guard_rejects_tool_that_pins_production_compose_without_overlay(tmp_path: Path) -> None:
    tool = "docker compose -f docker-compose.prod.yml --env-file .env.prod ps\n"

    result = _run_guard(_fixture_repo(tmp_path, PROD_FACING_WORKFLOW, tool=tool))

    assert result.returncode == 1
    assert "without the shared-host overlay" in result.stderr


def _wrapper_sandbox(tmp_path: Path, env_prod: str) -> tuple[Path, Path]:
    (tmp_path / "tools").mkdir()
    shutil.copy(WRAPPER, tmp_path / "tools" / "compose_shared_host.sh")
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.shared-host.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env.prod").write_text(env_prod, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorded = tmp_path / "compose-args"
    docker = bin_dir / "docker"
    docker.write_text(
        f'#!/usr/bin/env bash\nprintf "%s " "$@" > "{recorded}"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, recorded


def test_wrapper_adds_shared_host_overlay_on_a_shared_edge_host(tmp_path: Path) -> None:
    bin_dir, recorded = _wrapper_sandbox(tmp_path, "PARTIZAN_EDGE_NETWORK=web\n")

    subprocess.run(
        ["bash", str(tmp_path / "tools" / "compose_shared_host.sh"), "ps"],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert "-f docker-compose.prod.yml" in recorded.read_text(encoding="utf-8")
    assert "-f docker-compose.shared-host.yml" in recorded.read_text(encoding="utf-8")


def test_wrapper_omits_shared_host_overlay_on_a_managed_edge_host(tmp_path: Path) -> None:
    bin_dir, recorded = _wrapper_sandbox(tmp_path, "PARTIZAN_PUBLIC_HOST=partizan.example.com\n")

    subprocess.run(
        ["bash", str(tmp_path / "tools" / "compose_shared_host.sh"), "ps"],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    recorded_args = recorded.read_text(encoding="utf-8")
    assert "-f docker-compose.prod.yml" in recorded_args
    assert "docker-compose.shared-host.yml" not in recorded_args


def test_watchdog_polls_public_edge_and_repairs_before_reporting() -> None:
    workflow = WATCHDOG.read_text(encoding="utf-8")

    assert 'cron: "*/10 * * * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "group: partizan-production" in workflow
    assert "environment: production" in workflow
    assert "issues: write" in workflow
    assert "bash tools/verify_public_edge.sh" in workflow
    assert "bash tools/ensure_shared_host_caddy_route.sh" in workflow

    probe_marker = "- name: Probe public edge"
    repair_marker = "- name: Repair shared-host edge attachment"
    recheck_marker = "- name: Re-probe public edge after repair"
    incident_marker = "- name: Open or update production edge incident"

    assert workflow.index(probe_marker) < workflow.index(repair_marker)
    assert workflow.index(repair_marker) < workflow.index(recheck_marker)
    assert workflow.index(recheck_marker) < workflow.index(incident_marker)

    assert "🚨 Production edge unreachable" in workflow
    assert "gh issue create" in workflow
    assert "gh issue close" in workflow


def test_watchdog_only_repairs_a_shared_edge_deployment() -> None:
    workflow = WATCHDOG.read_text(encoding="utf-8")

    assert '[[ "${PARTIZAN_MANAGED_EDGE}" != "false" ]]' in workflow

    for forbidden in (
        "compose_shared_host.sh up",
        "compose_shared_host.sh down",
        "compose_shared_host.sh restart",
        "docker restart",
        "deploy_prod_remote.sh",
    ):
        assert forbidden not in workflow


def test_public_edge_check_requires_https_and_both_health_paths() -> None:
    source = (ROOT / "tools" / "verify_public_edge.sh").read_text(encoding="utf-8")

    assert "/health/live" in source
    assert "/health/ready" in source
    assert "PARTIZAN_PUBLIC_URL must use https://" in source
    assert "public-edge=healthy" in source
    assert "public-edge=unreachable" in source
