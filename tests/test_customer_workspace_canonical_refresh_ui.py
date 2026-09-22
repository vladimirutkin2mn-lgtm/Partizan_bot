from pathlib import Path

WORKSPACE_HTML = Path("app/web/workspace.v1.html").read_text(encoding="utf-8")
WORKSPACE_JS = Path("app/web/workspace.v1.js").read_text(encoding="utf-8")
CHANNELS_JS = Path("app/web/workspace.channels.v1.js").read_text(encoding="utf-8")
EXPERIMENTS_JS = Path("app/web/workspace.experiments.v1.js").read_text(encoding="utf-8")


def test_community_action_event_refreshes_the_canonical_workspace_snapshot() -> None:
    event = "partizan:community-action-updated"
    block = WORKSPACE_JS.split(f"window.addEventListener('{event}'", 1)[1]
    block = block.split("const surfaceLabels =", 1)[0]

    assert "event.detail?.projectId" in block
    assert "event.detail.projectId !== projectId" in block
    assert "refreshWorkspaceWithoutResearch().catch" in block
    assert "method: 'POST'" not in block
    assert "method: 'PUT'" not in block
    assert "method: 'DELETE'" not in block
    assert "installCommunityWorkspaceRefresh" not in WORKSPACE_JS
    assert "communityActionSignature" not in WORKSPACE_JS


def test_successful_community_mutations_emit_explicit_refresh_events() -> None:
    event = "partizan:community-action-updated"
    assert f"new CustomEvent('{event}'" in CHANNELS_JS

    publish = CHANNELS_JS.split("async function publishSelectedCommunityAction()", 1)[1]
    publish = publish.split("const observeCommunityAction =", 1)[0]
    observe = CHANNELS_JS.split("const observeCommunityAction =", 1)[1]
    observe = observe.split("const dataObserver =", 1)[0]

    publish_notify = "notifyCommunityActionUpdated(projectId, action.action_id, 'publish');"
    observe_notify = "notifyCommunityActionUpdated(projectId, action.action_id, 'observe');"

    assert "method: 'POST'" in publish
    assert "await refreshCommunityActions();" in publish
    assert publish_notify in publish
    assert publish.index("await refreshCommunityActions();") < publish.index(publish_notify)
    assert publish.index(publish_notify) < publish.index("} catch (error)")

    assert "method: 'POST'" in observe
    assert "await refreshCommunityActions();" in observe
    assert observe_notify in observe
    assert observe.index("await refreshCommunityActions();") < observe.index(observe_notify)
    assert observe.index(observe_notify) < observe.index("} catch (error)")


def test_canonical_workspace_refresh_fans_out_to_read_only_surfaces() -> None:
    event = "partizan:workspace-ready"

    assert f"new CustomEvent('{event}'" in WORKSPACE_JS
    assert f"window.addEventListener('{event}'" in EXPERIMENTS_JS
    assert f"window.addEventListener('{event}'" in WORKSPACE_HTML


def test_customer_results_do_not_install_a_second_community_observer() -> None:
    assert "const communityObserver = new MutationObserver" not in WORKSPACE_HTML
    assert "communityObserver.observe" not in WORKSPACE_HTML
    assert "const refreshLearning = async" not in WORKSPACE_HTML
    assert "const renderLearning = (data) =>" not in WORKSPACE_HTML
    assert "const renderOverviewSnapshot = (data) =>" not in WORKSPACE_HTML


def test_workspace_rerender_tolerates_optional_activation_markup() -> None:
    assert "if (!node) return;" in WORKSPACE_JS
    assert "if (state) state.textContent = stateText;" in WORKSPACE_JS
    assert (
        "const balanceBenefitText = balanceBenefit ? "
        "balanceBenefit.querySelector('span') : null;"
        in WORKSPACE_JS
    )
    assert "if (balanceBenefitText)" in WORKSPACE_JS


def test_paid_checkout_callback_is_consumed_before_follow_up_research_render() -> None:
    block = WORKSPACE_JS.split("if (growthState === 'success' && sessionId)", 1)[1]
    block = block.split("} else if (growthState === 'cancelled')", 1)[0]

    cleanup = "window.history.replaceState({}, '', `/workspace?project=${encodeURIComponent(projectId)}`);"
    assert cleanup in block
    assert "await loadResearch(true);" in block
    assert block.index(cleanup) < block.index("await loadResearch(true);")


def test_needs_input_restores_persisted_clarification_without_restarting_research() -> None:
    assert "data.research_clarifications" in WORKSPACE_JS
    assert "renderClarification(data.research_clarifications[0])" in WORKSPACE_JS
    assert "project.research_state === 'NEEDS_INPUT'" in WORKSPACE_JS

    callback = WORKSPACE_JS.split("const handleCallbacks = async (initial) =>", 1)[1]
    callback = callback.split("const openAccount = async", 1)[0]
    assert "initial.project.research_state === 'READY'" in callback
    assert "initial.project.research_state !== 'NOT_STARTED'" not in callback


def test_needs_input_uses_answer_form_instead_of_misleading_continue_research_button() -> None:
    assert "const renderClarification = (question) =>" in WORKSPACE_JS
    assert 'id="clarification-answer"' in WORKSPACE_JS
    assert "/clarifications" in WORKSPACE_JS
    assert "$('research-button').classList.toggle('hidden', hasPersistedClarification);" in WORKSPACE_JS


def test_workspace_shows_audience_discovery_diagnostics() -> None:
    assert 'research-diagnostics' in WORKSPACE_HTML
    assert "renderResearchDiagnostics(data.research_diagnostics)" in WORKSPACE_JS
    assert "Discovery diagnostic:" in WORKSPACE_JS
    assert "no cited sources that cleared the evidence bar" in WORKSPACE_JS
    assert "none normalized into a valid execution-platform target" in WORKSPACE_JS
