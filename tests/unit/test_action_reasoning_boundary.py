from praxile.runtime import AgentRuntime
from praxile.action_schema import ActionSchemaRegistry


def parse(content):
    return AgentRuntime._parse_action(None, content)


def test_reasoning_example_is_not_an_executable_action():
    assert parse('<think>{"type":"edit_file","path":"wrong","content":"bad"}</think>'
                 '{"type":"finish","summary":"Done"}') == {"type": "finish", "summary": "Done"}


def test_reasoning_only_unclosed_or_nested_is_rejected():
    assert parse('<think>{"type":"finish","summary":"Not final"}</think>') is None
    assert parse('<think>{"type":"finish","summary":"Not final"}') is None
    assert parse('<think><think>x</think>{"type":"finish","summary":"Ambiguous"}') is None


def test_tags_in_json_strings_preserved_and_fenced_final_supported():
    assert parse('{"type":"finish","summary":"Literal <think> text"}')["summary"] == "Literal <think> text"
    assert parse('<think>Reasoning</think>\n```json\n{"type":"finish","summary":"OK"}\n```')["summary"] == "OK"


def test_nested_document_contract_is_in_initial_and_repair_prompts():
    registry = ActionSchemaRegistry()
    for prompt in (registry.prompt_summary(), registry.repair_prompt(["document spec requires only blocks"])):
        assert '"blocks"' in prompt
        assert '"rows"' in prompt
        assert "footer" in prompt
        assert "1..20" in prompt
