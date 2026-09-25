"""Tool evidence reaching the eval judges.

The hosted-agent path records raw SSE ``ToolCallEvent`` dicts. Those use
``skillName`` rather than ``name`` and split one logical call across a
``started`` event (arguments) and a ``completed`` event (result). The harness
previously read ``name`` only, so no tool evidence survived normalisation and
every tool-backed claim was judged unsubstantiated.
"""

from app.services.eval_service import (
    _build_agent_messages,
    _build_tool_definitions,
    _filter_tool_calls,
)


def _sse(skill: str, status: str, **extra: object) -> dict[str, object]:
    return {"type": "tool_call", "skillName": skill, "status": status, "input": "", "output": "", **extra}


class TestFilterToolCalls:
    def test_merges_sse_started_and_completed_into_one_call(self) -> None:
        calls = _filter_tool_calls(
            [
                _sse("view", "started", input='{"path": "deed.md"}'),
                _sse("view", "completed", output="file contents"),
            ]
        )

        assert calls == [
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

        assert calls == [{"name": "mcp-tools-code_interpreter", "arguments": {}, "result": "42"}]

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

        assert calls == [{"name": "mcp-tools-view", "arguments": {"path": "x"}, "result": "y"}]

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


class TestBuildAgentMessages:
    def test_renders_calls_and_results_before_the_final_answer(self) -> None:
        messages = _build_agent_messages(
            "The deed was generated.",
            [{"name": "mcp-tools-view", "arguments": {"path": "deed.md"}, "result": "ok"}],
        )

        assert [m["role"] for m in messages] == ["assistant", "tool", "assistant"]
        assert messages[0]["content"][0]["type"] == "tool_call"
        assert messages[0]["content"][0]["tool_call_id"] == messages[1]["tool_call_id"]
        assert messages[1]["content"][0]["tool_result"] == "ok"
        assert messages[2]["content"][0]["text"] == "The deed was generated."

    def test_final_answer_is_present_even_with_no_tool_calls(self) -> None:
        messages = _build_agent_messages("Nothing to do.", [])

        assert messages == [{"role": "assistant", "content": [{"type": "text", "text": "Nothing to do."}]}]

    def test_tool_call_ids_are_unique_across_repeated_tools(self) -> None:
        messages = _build_agent_messages(
            "done",
            [
                {"name": "mcp-tools-view", "arguments": {}, "result": "a"},
                {"name": "mcp-tools-view", "arguments": {}, "result": "b"},
            ],
        )

        ids = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
        assert len(set(ids)) == 2


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
