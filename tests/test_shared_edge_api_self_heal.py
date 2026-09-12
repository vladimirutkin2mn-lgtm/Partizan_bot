from pathlib import Path

REPAIR = Path("tools/ensure_shared_host_caddy_route.sh")


def test_shared_host_repair_can_restore_partizan_api_edge_attachment() -> None:
    source = REPAIR.read_text(encoding="utf-8")

    assert 'docker network connect --alias partizan-api "${edge_network}" "${api_container_id}"' in source
    assert "Partizan API attached to configured edge network" in source
    assert "api_connected_by_repair=false" in source
    assert 'docker network disconnect "${edge_network}" "${api_container_id}"' in source


def test_shared_host_repair_does_not_restart_or_recreate_shared_proxy() -> None:
    source = REPAIR.read_text(encoding="utf-8")

    for forbidden in (
        "docker restart",
        "docker compose restart",
        "docker compose up",
        "systemctl restart",
        "systemctl reload",
    ):
        assert forbidden not in source
