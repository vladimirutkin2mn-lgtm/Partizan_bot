"""Execute ten frozen situations through the real feature branch selector and composer on the CI runner."""
import asyncio
import base64
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.action_drafting import ActionTargetSelector, DistributionActionComposer
from app.distribution_types import DistributionActionType as Action
from app.distribution_types import DistributionPlatform
from app.telegram_writer_media import SelectedPhoto
from app.telegram_writer_v18 import PROMPT_VERSION

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
HANDLES = ("a_sfera", "play_hard_community", "bizarreclubmsk", "findom_gospoja0", "poiskrabovirabin")
IDS = (2123, 2121, 418, 413, 8661, 8660, 536536, 536534, 2751285, 2751284)


def frozen():
    main, albums = [json.loads(Path(f"/tmp/v18-{key}/telegram-five-latest-two.json").read_text())
                    for key in ("main", "albums")]
    assert main["status"] == albums["status"] == "READ_ONLY_COMPLETE"
    by_handle = {c["handle"]: c for c in main["communities"]}
    by_handle["play_hard_community"] = albums["communities"][0]
    assert set(by_handle) == set(HANDLES)
    for i, number in enumerate(IDS):
        handle = HANDLES[i // 2]
        row = by_handle[handle]
        [post] = [p for p in row["posts"] if p["id"] == number]
        assert post["url"] == f"https://t.me/{handle}/{number}" and post["text"]
        yield handle, row, post


def bridge_command():
    # Use the two narrowly scoped source files for the selected public photo only.
    # The writer and selector are executed on the CI runner. No project files on
    # the API host are installed, written, restarted or deployed.
    code = ["import sys, types\n"]
    for name in ("telegram_public_post_links", "telegram_writer_media"):
        module = "app." + name
        source = Path(f"app/{name}.py").read_text()
        code.append(
            f"m=types.ModuleType({module!r}); m.__package__='app'; sys.modules[m.__name__]=m\n"
            f"exec(compile({source!r}, {name!r}, 'exec'), m.__dict__)\n"
        )
    code.append(Path("tools/v18_probe_bridge.py").read_text())
    remote = (
        "cd " + shlex.quote(os.environ["DEPLOY_PATH"]) +
        " && bash tools/compose_shared_host.sh exec -T api python -uB -c " + shlex.quote("".join(code))
    )
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", os.environ["DEPLOY_HOST"], remote]


class RemoteProvider:
    def __init__(self, pipe):
        self.pipe = pipe
        self.source = None
        self.model = None
        self.attempted = 0
        self.media = {}

    def request(self, payload):
        self.pipe.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.pipe.stdin.flush()
        response = self.pipe.stdout.readline()
        if not response:
            raise RuntimeError("Bridge closed before returning a result")
        return json.loads(response)

    async def photo(self, url, caption):
        if url != "https://t.me/bizarreclubmsk/8661":
            return SelectedPhoto()
        result = self.request({"kind": "photo", "url": url, "caption": caption})
        return SelectedPhoto(base64.b64decode(result["jpeg"]) if result["jpeg"] else None,
                             result["linked_url"], result["linked_text"])

    async def parse(self, messages, response_model):
        payload = json.loads(messages[1].content)
        self.media = payload.get("selected_media", {})
        frame = self.request({"kind": "draft", "source_id": self.source,
                              "messages": [message.response_input() for message in messages]})
        self.attempted = frame["attempted"]
        if "error_type" in frame:
            raise RuntimeError("Provider returned " + frame["error_type"])
        self.model = frame["model"]
        return response_model.model_validate(frame["draft"])


async def main():
    cases = list(frozen())
    pipe = subprocess.Popen(bridge_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8")
    previous = {}
    rows = []
    provider = RemoteProvider(pipe)
    try:
        actual = json.loads(pipe.stdout.readline())
        product = SimpleNamespace(id=PRODUCT, model_dump=lambda mode: actual["product"])
        memory = SimpleNamespace(prompt_context_for_product=lambda _, platform, action_type:
                                 SimpleNamespace(rendered=actual["memory"].get(action_type, "")))
        rehearsal = SimpleNamespace(recent_telegram_group_posts=lambda product_id, identity, url:
                                    previous.get(url, [])[-2:])
        composer = DistributionActionComposer(provider=provider, memory_service=memory, execution_service=rehearsal)
        for i, (handle, row, post) in enumerate(cases, 1):
            root = f"https://t.me/{handle}"
            group = row["kind"] == "GROUP"
            peer = actual["peers"].get(handle, {})
            assert not group or peer.get("about")
            action = Action.STANDALONE_POST if group else Action.COMMENT
            play = SimpleNamespace(action_type=action, tactic_id="telegram_group_post" if group else "telegram_channel_comment",
                                   selected_identity_id=UUID(int=1))
            opp = SimpleNamespace(title=peer.get("title") or row.get("title") or handle, url=root,
                                  platform=DistributionPlatform.TELEGRAM, metadata={
                "native_research_status": "VERIFIED", "community_fit_pass": True,
                "action_target_mode": "STANDALONE_POST" if group else "CHANNEL_COMMENT",
                "action_target_specific": not group, "action_target_url": root if group else post["url"],
                "about": peer.get("about") or "", "community_fit_reason": peer.get("reason") or "",
                "surface_capabilities": {"standalone_post": "AVAILABLE", "comment": "AVAILABLE"},
                "recent_context": [{"message_id": post["id"], "url": post["url"], "text": post["text"],
                                    "media_kind": "MessageMediaPhoto" if post["media_present"] else None}]
            })
            target = ActionTargetSelector().select(play, opp)
            provider.source = post["id"]
            with patch("app.action_drafting.read_selected_photo", provider.photo):
                draft = await composer.compose(product=product, play=play, opportunity=opp, target=target, policy=None)
            rows.append({"number": i, "url": post["url"], "surface": opp.metadata["action_target_mode"],
                         "comment": draft.content_text, "model": provider.model,
                         "image_attached": provider.media.get("image_attached", False),
                         "linked_background": (provider.media.get("linked_background") or {}).get("url"),
                         "rehearsal_previous_count": len(previous.get(root, [])) if group else 0})
            if group:
                previous.setdefault(root, []).append(draft.content_text)
    finally:
        pipe.stdin.close()
        pipe.wait(timeout=15)
        Path("v18-review.json").write_text(json.dumps({
            "writer": PROMPT_VERSION, "provider_attempts": provider.attempted, "cases": rows,
            "target": "frozen Nov 4 texts", "published": 0, "live_photo_scope": "bizarreclubmsk/8661 only",
        }, indent=2, ensure_ascii=False))
    assert len(rows) == provider.attempted == 10 and pipe.returncode == 0
    print(f"{len(rows)} generated drafts, {provider.attempted} provider calls, model {provider.model}, zero publications")


if __name__ == "__main__":
    asyncio.run(main())
