"""Contract checks for action examples sent to the model."""

import json

from diagagent.actions.parser import ActionParser
from diagagent.actions.schema import ExportFileAction
from diagagent.agent.context import ContextBuilder


def test_export_file_prompt_matches_action_schema():
    messages = ContextBuilder().build_context(task_spec={}, observation=None)
    examples = [
        json.loads(line.removeprefix("- "))
        for line in messages[0].content.splitlines()
        if line.startswith('- {"type": "export_file"')
    ]
    assert len(examples) == 1
    example = examples[0]
    schema = ExportFileAction.model_json_schema()

    assert "output_path" not in example
    assert set(example) == set(schema["properties"])
    assert set(schema["required"]) <= set(example)
    for name, field in schema["properties"].items():
        if "default" in field:
            assert example[name] == field["default"]

    action = ActionParser.parse(example)
    assert isinstance(action, ExportFileAction)
    assert action.model_dump() == example
