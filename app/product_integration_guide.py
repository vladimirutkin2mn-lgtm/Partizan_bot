from uuid import UUID

from pydantic import BaseModel

from app.config import Settings
from app.distribution_event_ingestion import DISTRIBUTION_EVENT_KEY_HEADER

_EVENT_KEY_PLACEHOLDER = "<PARTIZAN_EVENT_KEY>"
_DEFAULT_BASE_URL = "https://YOUR_PARTIZAN_HOST"


class ProductIntegrationSnippets(BaseModel):
    curl: str
    python: str
    node: str


class ProductIntegrationGuideView(BaseModel):
    product_id: UUID
    base_url: str
    public_base_configured: bool
    event_key_header: str
    event_key_placeholder: str
    event_endpoint: str
    verification_endpoint: str
    attribution_fields: list[str]
    event_types: list[str]
    checklist: list[str]
    outbox_guidance: list[str]
    snippets: ProductIntegrationSnippets


class ProductIntegrationGuideService:
    def get(self, product_id: UUID, settings: Settings) -> ProductIntegrationGuideView:
        configured_base = settings.partizan_public_base_url
        base_url = (configured_base or _DEFAULT_BASE_URL).rstrip("/")
        event_path = f"/v1/products/{product_id}/distribution-events"
        verify_path = f"{event_path}/verify"
        event_endpoint = f"{base_url}{event_path}"
        verification_endpoint = f"{base_url}{verify_path}"

        snippets = ProductIntegrationSnippets(
            curl=self._curl(verification_endpoint, product_id),
            python=self._python(base_url, product_id),
            node=self._node(base_url, product_id),
        )
        return ProductIntegrationGuideView(
            product_id=product_id,
            base_url=base_url,
            public_base_configured=configured_base is not None,
            event_key_header=DISTRIBUTION_EVENT_KEY_HEADER,
            event_key_placeholder=_EVENT_KEY_PLACEHOLDER,
            event_endpoint=event_endpoint,
            verification_endpoint=verification_endpoint,
            attribution_fields=["experiment_id", "action_id", "referral_token"],
            event_types=["VISIT", "SIGNUP", "ACTIVATED", "PAID"],
            checklist=[
                "Store the Product Event Key only in the product backend secret store",
                "Persist Partizan attribution in a first-party server-side session or user record",
                "Use one stable UUID event_id per real business event and reuse it on retries",
                "Verify one representative payload through /distribution-events/verify",
                "Send real business events through /distribution-events from the product backend",
                "Keep SIGNUP, ACTIVATED and PAID semantics stable across experiments",
            ],
            outbox_guidance=[
                "Commit the business transaction and an outbox row with the same stable event_id",
                "Deliver asynchronously to Partizan after the business transaction commits",
                "Retry network and 5xx failures with the same event_id",
                "Treat HTTP 201 with duplicate=true as already delivered",
                "Never generate a fresh event_id for an HTTP retry",
            ],
            snippets=snippets,
        )

    def _curl(self, verification_endpoint: str, product_id: UUID) -> str:
        return f'''export PARTIZAN_EVENT_KEY='{_EVENT_KEY_PLACEHOLDER}'

curl --fail-with-body \\
  --request POST \\
  --url '{verification_endpoint}' \\
  --header 'Content-Type: application/json' \\
  --header '{DISTRIBUTION_EVENT_KEY_HEADER}: ${{PARTIZAN_EVENT_KEY}}' \\
  --data '{{
    "event_id": "<STABLE_EVENT_UUID>",
    "event_type": "SIGNUP",
    "experiment_id": "<PARTIZAN_EXPERIMENT_UUID>",
    "actor_id": "<STABLE_PRODUCT_USER_ID>"
  }}'

# Product ID: {product_id}
# A successful verification returns persisted=false.'''

    def _python(self, base_url: str, product_id: UUID) -> str:
        return f'''import os
import httpx

PARTIZAN_URL = {base_url!r}
PARTIZAN_PRODUCT_ID = {str(product_id)!r}
PARTIZAN_EVENT_KEY = os.environ["PARTIZAN_EVENT_KEY"]


def send_partizan_event(*, event_id: str, event_type: str, experiment_id: str,
                        actor_id: str, revenue: float = 0, verify: bool = False) -> dict:
    suffix = "/verify" if verify else ""
    response = httpx.post(
        f"{{PARTIZAN_URL}}/v1/products/{{PARTIZAN_PRODUCT_ID}}/distribution-events{{suffix}}",
        headers={{"{DISTRIBUTION_EVENT_KEY_HEADER}": PARTIZAN_EVENT_KEY}},
        json={{
            "event_id": event_id,  # persist this UUID in your outbox and reuse on retry
            "event_type": event_type,
            "experiment_id": experiment_id,
            "actor_id": actor_id,
            "revenue": revenue,
        }},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
'''

    def _node(self, base_url: str, product_id: UUID) -> str:
        return f'''const PARTIZAN_URL = {base_url!r};
const PARTIZAN_PRODUCT_ID = {str(product_id)!r};

export async function sendPartizanEvent({{
  eventId,
  eventType,
  experimentId,
  actorId,
  revenue = 0,
  verify = false,
}}) {{
  const suffix = verify ? "/verify" : "";
  const response = await fetch(
    `${{PARTIZAN_URL}}/v1/products/${{PARTIZAN_PRODUCT_ID}}/distribution-events${{suffix}}`,
    {{
      method: "POST",
      headers: {{
        "Content-Type": "application/json",
        "{DISTRIBUTION_EVENT_KEY_HEADER}": process.env.PARTIZAN_EVENT_KEY,
      }},
      body: JSON.stringify({{
        event_id: eventId, // persist this UUID in your outbox and reuse on retry
        event_type: eventType,
        experiment_id: experimentId,
        actor_id: actorId,
        revenue,
      }}),
    }},
  );
  if (!response.ok) throw new Error(`Partizan event failed: ${{response.status}}`);
  return response.json();
}}
'''


product_integration_guide_service = ProductIntegrationGuideService()
