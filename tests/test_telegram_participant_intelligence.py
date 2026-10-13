from uuid import uuid4

from app.discovery_persistence_propagation import recompute_persisted_discovery_signals
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.telegram_participant_intelligence import apply_participant_confidence


def _opportunity(*, matched_participants=2, matched_messages=2, repeat_participants=0):
    return DistributionOpportunitySeed(
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:participant-test",
        title="Participant evidence community",
        url="https://t.me/example",
        relevance_score=70,
        rationale="participant evidence test",
        metadata={
            "participant_evidence": {
                "matched_participant_count": matched_participants,
                "matched_message_count": matched_messages,
                "repeat_participant_count": repeat_participants,
                "identity_persisted": False,
            },
            "intelligence_scores": {
                "version": "v2",
                "audience_fit": 60.0,
                "context_fit": 40.0,
                "actionability": 10.0,
                "confidence": 50.0,
                "ranking_score": 46.0,
                "exploration_candidate": False,
                "execution_permission_inferred": False,
            },
        },
        evidence=[],
    )


def test_two_independent_matched_participants_raise_confidence_only() -> None:
    item = _opportunity(matched_participants=3, matched_messages=4, repeat_participants=1)
    before = dict(item.metadata["intelligence_scores"])

    result = apply_participant_confidence([item])[0]
    scores = result.metadata["intelligence_scores"]
    signal = result.metadata["participant_signal"]

    assert scores["confidence"] > before["confidence"]
    assert scores["audience_fit"] == before["audience_fit"]
    assert scores["context_fit"] == before["context_fit"]
    assert scores["actionability"] == before["actionability"]
    assert signal["reason"] == "MULTI_PARTICIPANT_CORROBORATION"
    assert signal["actionability_propagated"] is False
    assert signal["identity_persisted"] is False
    assert signal["execution_permission_inferred"] is False


def test_one_participant_never_counts_as_independent_corroboration() -> None:
    item = _opportunity(matched_participants=1, matched_messages=5, repeat_participants=1)
    before = dict(item.metadata["intelligence_scores"])

    result = apply_participant_confidence([item])[0]

    assert result.metadata["intelligence_scores"]["confidence"] == before["confidence"]
    assert result.metadata["participant_signal"]["confidence_bonus"] == 0.0
    assert result.metadata["participant_signal"]["reason"] == (
        "INSUFFICIENT_INDEPENDENT_PARTICIPANTS"
    )


def test_persisted_recompute_does_not_compound_participant_bonus() -> None:
    original = _opportunity(
        matched_participants=4,
        matched_messages=5,
        repeat_participants=2,
    )

    first = recompute_persisted_discovery_signals([original])[0]
    second = recompute_persisted_discovery_signals([first])[0]
    third = recompute_persisted_discovery_signals([second])[0]

    assert first.metadata["intelligence_scores"]["confidence"] == 59.0
    assert second.metadata["intelligence_scores"]["confidence"] == 59.0
    assert third.metadata["intelligence_scores"]["confidence"] == 59.0
    assert first.metadata["participant_signal"]["confidence_bonus"] == 8.0
    assert second.metadata["participant_signal"]["confidence_bonus"] == 8.0
