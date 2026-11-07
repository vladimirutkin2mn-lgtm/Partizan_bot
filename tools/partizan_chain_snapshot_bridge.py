import json
import os
import shlex
import subprocess
from pathlib import Path

source = Path("tools/partizan_chain_snapshot.py").read_text()
remote = ("cd " + shlex.quote(os.environ["DEPLOY_PATH"])
          + " && bash tools/compose_shared_host.sh exec -T api python -B -c " + shlex.quote(source))
result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
                         os.environ["DEPLOY_HOST"], remote], text=True, capture_output=True, timeout=90)
data = json.loads(result.stdout)
assert result.returncode == 0 and data["status"] == "READ_ONLY_CHAIN_SNAPSHOT_COMPLETE"
assert data["read_counts"]["write_attempts"] == 0
assert data["paid_llm_calls"] == data["telegram_api_calls"] == data["publication_actions"] == 0
Path("partizan-chain-runtime-20261107.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
print(json.dumps({"status": data["status"], "release_sha": data["release_sha"],
                  "product_readiness_issues": data["product_readiness_issues"],
                  "voice_versions": len(data["voice_versions"]), "icp_count": data["icp"]["count"],
                  "registry_count": data["registry"]["count"], "write_attempts": 0}))
