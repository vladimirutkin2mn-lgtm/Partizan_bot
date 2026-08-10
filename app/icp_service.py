from uuid import UUID, uuid4

from app.icp_agent import ICPEngine
from app.llm import get_llm_provider
from app.models import ProductProfileStatus
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import (
    DuplicateClusterView,
    ICPGenerationResponse,
    ICPScoreBreakdownView,
    ICPView,
    ProductProfileView,
)

ICP_NAMESPACE = "icp_generation"


class InMemoryICPService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._results: dict[UUID, ICPGenerationResponse] = {}

    async def generate(self, product: ProductProfileView) -> ICPGenerationResponse:
        if product.status != ProductProfileStatus.CONFIRMED:
            raise ValueError("ProductProfile must be CONFIRMED before ICP generation")

        engine = ICPEngine(get_llm_provider())
        result = await engine.generate(product.model_dump(mode="json"))
        icps = [
            ICPView(
                id=uuid4(),
                product_id=product.id,
                rank=index,
                title=item.candidate.title,
                description=item.candidate.description,
                pain=item.candidate.pain,
                desired_outcome=item.candidate.desired_outcome,
                trigger=item.candidate.trigger,
                willingness_to_pay=item.candidate.willingness_to_pay_hypothesis,
                alternatives=item.candidate.alternatives,
                message_hook=item.candidate.message_hook,
                score=item.total_score,
                score_breakdown=ICPScoreBreakdownView(
                    **item.candidate.dimensions.model_dump()
                ),
                score_explanation=item.score_explanation,
                rationale=item.candidate.rationale,
                duplicate_of=item.duplicate_of,
            )
            for index, item in enumerate(result.ranked, start=1)
        ]
        response = ICPGenerationResponse(
            product_id=product.id,
            generated_count=result.generated_count,
            ranked_count=len(icps),
            icps=icps,
            duplicate_clusters=[
                DuplicateClusterView(canonical=canonical, duplicates=duplicates)
                for canonical, duplicates in result.duplicate_clusters.items()
            ],
        )
        self._results[product.id] = response
        self._store.put(
            ICP_NAMESPACE,
            str(product.id),
            response.model_dump(mode="json"),
        )
        return response

    def get(self, product_id: UUID) -> ICPGenerationResponse:
        cached = self._results.get(product_id)
        if cached is not None:
            return cached
        payload = self._store.get(ICP_NAMESPACE, str(product_id))
        if payload is None:
            raise KeyError(product_id)
        result = ICPGenerationResponse.model_validate(payload)
        self._results[product_id] = result
        return result

    def reset(self) -> None:
        self._results.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(ICP_NAMESPACE)


icp_service = InMemoryICPService()
