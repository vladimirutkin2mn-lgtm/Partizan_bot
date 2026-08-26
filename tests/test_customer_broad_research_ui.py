from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _asset(name: str) -> str:
    return (ROOT / "app" / "web" / name).read_text(encoding="utf-8")


def test_start_renders_broad_research_status_requirement_and_provenance() -> None:
    source = _asset("start.v2.js")

    assert "Broad research" in source
    assert "Execution-platform candidates" in source
    assert "execution_requirement" in source
    assert "item.provenance" in source
    assert "Research finding only. This is not a connected channel" in source
    assert "A control-plane path is not authorization to execute" in source


def test_workspace_keeps_non_execution_research_visible_when_channel_is_off() -> None:
    source = _asset("workspace.v1.js")

    assert "const surface = opportunitySurface(item);" in source
    assert "if (surface !== 'EXECUTION_PLATFORM') return true;" in source
    assert "modes.get(String(item.platform || '').toUpperCase()) !== 'OFF'" in source
    assert "Research-only findings stay visible independently" in source


def test_workspace_renders_execution_boundary_and_evidence() -> None:
    source = _asset("workspace.v1.js")

    assert "Broad research surfaces" in source
    assert "Execution-platform candidates" in source
    assert "execution_requirement" in source
    assert "item.provenance" in source
    assert "integration/identity/permission and normal safety checks" in source
