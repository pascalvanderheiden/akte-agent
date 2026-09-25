"""Tool evidence reaching the eval judges.

The hosted-agent path records raw SSE ``ToolCallEvent`` dicts. Those use
``skillName`` rather than ``name`` and split one logical call across a
``started`` event (arguments) and a ``completed`` event (result). The harness
previously read ``name`` only, so no tool evidence survived normalisation and
every tool-backed claim was judged unsubstantiated.
"""

from app.services.eval_service import (
    _build_tool_definitions,
    _criterion_passed,
    _filter_tool_calls,
    _normalise_judge_params,
    _render_tool_transcript,
)


def _sse(skill: str, status: str, **extra: object) -> dict[str, object]:
    return {"type": "tool_call", "skillName": skill, "status": status, "input": "", "output": "", **extra}


def _payloads(calls: list[dict]) -> list[dict]:
    """Drop the evaluator envelope keys so tests assert on the captured evidence."""
    return [{k: v for k, v in c.items() if k not in ("type", "tool_call_id")} for c in calls]


class TestFilterToolCalls:
    def test_merges_sse_started_and_completed_into_one_call(self) -> None:
        calls = _filter_tool_calls(
            [
                _sse("view", "started", input='{"path": "deed.md"}'),
                _sse("view", "completed", output="file contents"),
            ]
        )

        assert _payloads(calls) == [
            {
                "name": "mcp-tools-view",
                "arguments": {"path": "deed.md"},
                "result": "file contents",
            }
        ]

    def test_pairs_concurrent_calls_of_the_same_tool_in_order(self) -> None:
        calls = _filter_tool_calls(
            [
                _sse("view", "started", input='{"path": "a"}'),
                _sse("view", "started", input='{"path": "b"}'),
                _sse("view", "completed", output="A"),
                _sse("view", "completed", output="B"),
            ]
        )

        assert [c["arguments"]["path"] for c in calls] == ["a", "b"]
        assert [c["result"] for c in calls] == ["A", "B"]

    def test_completion_without_a_start_is_still_recorded(self) -> None:
        calls = _filter_tool_calls([_sse("code_interpreter", "completed", output="42")])

        assert _payloads(calls) == [{"name": "mcp-tools-code_interpreter", "arguments": {}, "result": "42"}]

    def test_drops_internal_sdk_tools(self) -> None:
        calls = _filter_tool_calls(
            [
                _sse("skill", "started"),
                _sse("skill", "completed", output="loaded"),
                _sse("report_intent", "started"),
            ]
        )

        assert calls == []

    def test_non_json_arguments_are_preserved_verbatim(self) -> None:
        calls = _filter_tool_calls([_sse("web-search", "started", input="notarial deed")])

        assert calls[0]["arguments"] == {"input": "notarial deed"}

    def test_responses_api_shape_passes_through_with_prefixed_name(self) -> None:
        calls = _filter_tool_calls([{"name": "view", "arguments": {"path": "x"}, "result": "y"}])

        assert _payloads(calls) == [{"name": "mcp-tools-view", "arguments": {"path": "x"}, "result": "y"}]

    def test_subagent_delegation_survives_normalisation(self) -> None:
        calls = _filter_tool_calls(
            [
                _sse("subagent:sol-legal-analyst", "started", agentName="sol-legal-analyst"),
                _sse("subagent:sol-legal-analyst", "completed", output="analysis done"),
            ]
        )

        assert calls[0]["name"] == "mcp-tools-subagent:sol-legal-analyst"
        assert calls[0]["result"] == "analysis done"


class TestBuildToolDefinitions:
    def test_includes_tools_the_agent_actually_used(self) -> None:
        defs = _build_tool_definitions(["notarial-intake"], [{"name": "mcp-tools-code_interpreter"}])

        assert [d["name"] for d in defs] == ["mcp-tools-notarial-intake", "mcp-tools-code_interpreter"]

    def test_does_not_duplicate_an_expected_tool_that_was_used(self) -> None:
        defs = _build_tool_definitions(["view"], [{"name": "mcp-tools-view"}])

        assert [d["name"] for d in defs] == ["mcp-tools-view"]

    def test_every_definition_carries_a_parameters_schema(self) -> None:
        # ToolCallAccuracyEvaluator rejects definitions without one.
        for definition in _build_tool_definitions(["view"], [{"name": "mcp-tools-x"}]):
            assert definition["parameters"] == {"type": "object", "properties": {}}


class TestRenderToolTranscript:
    """TaskAdherence reads TOOL_CALLS as the source of truth for any claim."""

    def test_renders_each_call_and_its_result(self) -> None:
        transcript = _render_tool_transcript(
            [{"name": "mcp-tools-view", "arguments": {"path": "deed.md"}, "result": "ok"}]
        )

        assert transcript == '[TOOL_CALL] mcp-tools-view({"path": "deed.md"})\n[TOOL_RESULT] ok'

    def test_no_tool_calls_renders_empty(self) -> None:
        assert _render_tool_transcript([]) == ""

    def test_repeated_tools_each_appear(self) -> None:
        transcript = _render_tool_transcript(
            [
                {"name": "mcp-tools-view", "arguments": {}, "result": "a"},
                {"name": "mcp-tools-view", "arguments": {}, "result": "b"},
            ]
        )

        assert transcript.count("[TOOL_CALL]") == 2
        assert "[TOOL_RESULT] a" in transcript and "[TOOL_RESULT] b" in transcript

    def test_non_dict_arguments_are_stringified(self) -> None:
        transcript = _render_tool_transcript([{"name": "web-search", "arguments": "notarial deed", "result": ""}])

        assert "[TOOL_CALL] web-search(notarial deed)" in transcript


class TestFoundryInvocationsShape:
    """The Foundry Invocations protocol passes its own payload through untouched."""

    def test_tool_name_and_arguments_are_understood(self) -> None:
        calls = _filter_tool_calls(
            [
                {"tool_call_id": "c1", "tool_name": "view", "arguments": {"path": "deed.md"}},
                {"tool_call_id": "c1", "tool_name": "view", "result": "contents"},
            ]
        )

        assert [c["name"] for c in calls] == ["mcp-tools-view", "mcp-tools-view"]
        assert calls[0]["arguments"] == {"path": "deed.md"}
        assert calls[1]["result"] == "contents"

    def test_internal_tools_are_dropped_on_this_shape_too(self) -> None:
        assert _filter_tool_calls([{"tool_name": "report_intent", "arguments": {}}]) == []


class TestJudgeParamNormalisation:
    """Reasoning judge models reject the prompty-hardcoded sampling params."""

    def test_max_tokens_is_renamed(self) -> None:
        assert _normalise_judge_params({"max_tokens": 800}) == {"max_completion_tokens": 800}

    def test_existing_max_completion_tokens_wins(self) -> None:
        out = _normalise_judge_params({"max_tokens": 800, "max_completion_tokens": 42})
        assert out == {"max_completion_tokens": 42}

    def test_zero_temperature_is_dropped(self) -> None:
        assert "temperature" not in _normalise_judge_params({"temperature": 0.0})

    def test_non_default_top_p_is_dropped(self) -> None:
        assert "top_p" not in _normalise_judge_params({"top_p": 0.2})

    def test_default_sampling_values_are_kept(self) -> None:
        assert _normalise_judge_params({"temperature": 1, "top_p": 1}) == {"temperature": 1, "top_p": 1}


class TestToolCallShape:
    """ToolCallAccuracy rejects any call lacking the converter format."""

    def test_calls_declare_converter_type_and_id(self) -> None:
        calls = _filter_tool_calls(
            [
                {"skillName": "view", "status": "started", "arguments": {"path": "a.md"}},
                {"skillName": "view", "status": "completed", "result": "ok"},
                {"skillName": "export", "status": "started", "arguments": {}},
                {"skillName": "export", "status": "completed", "result": "done"},
            ]
        )
        assert [c["type"] for c in calls] == ["tool_call", "tool_call"]
        assert [c["tool_call_id"] for c in calls] == ["call_0", "call_1"]


class TestCriterionVerdict:
    """The SDK reports verdicts on <metric>_result / <metric>_passed."""

    def test_result_key_decides_pass(self) -> None:
        assert _criterion_passed({"task_adherence": 1.0, "task_adherence_result": "pass"}) is True

    def test_low_score_with_pass_result_is_not_a_failure(self) -> None:
        scores = {"task_adherence": 1.0, "task_adherence_passed": True, "task_adherence_threshold": 3}
        assert _criterion_passed(scores) is True

    def test_fail_result_is_a_failure(self) -> None:
        assert _criterion_passed({"intent_resolution_result": "fail"}) is False

    def test_not_applicable_is_excluded(self) -> None:
        assert _criterion_passed({"tool_call_accuracy_result": "not_applicable"}) is None

    def test_evaluator_error_is_excluded(self) -> None:
        assert _criterion_passed({"error": "boom"}) is None

    def test_numeric_fallback_uses_the_reported_threshold(self) -> None:
        assert _criterion_passed({"coherence_score": 4, "coherence_threshold": 3}) is True
        assert _criterion_passed({"coherence_score": 2, "coherence_threshold": 3}) is False

    def test_empty_scores_yield_no_verdict(self) -> None:
        assert _criterion_passed({}) is None
