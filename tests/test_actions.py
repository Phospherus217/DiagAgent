"""Unit tests for DiagAgent Actions system."""

import pytest
from diagagent.actions.parser import ActionParser, ActionParseError
from diagagent.actions.schema import (
    AddTextAction,
    ClickAction,
    CropCanvasAction,
    DragAction,
    ResizeImageAction,
    StopAction,
)
from diagagent.actions.types import ActionLevel, get_action_level
from diagagent.actions.validator import ActionValidator


def test_action_levels():
    assert get_action_level("click") == ActionLevel.A0
    assert get_action_level("drag") == ActionLevel.A0
    assert get_action_level("open_menu") == ActionLevel.A1
    assert get_action_level("click_dialog_button") == ActionLevel.A1
    assert get_action_level("resize_image") == ActionLevel.A2
    assert get_action_level("crop_canvas") == ActionLevel.A2
    assert get_action_level("stop") == ActionLevel.CONTROL


def test_action_models_serialization():
    act = ResizeImageAction(width=512, height=512)
    assert act.type == "resize_image"
    assert act.width == 512
    assert act.height == 512
    d = act.to_dict()
    assert d["width"] == 512

    click = ClickAction(x=100.5, y=200.0)
    assert click.type == "click"
    assert click.x == 100.5


def test_action_parser_json_string():
    raw = '{"type": "resize_image", "width": 800, "height": 600}'
    action = ActionParser.parse(raw)
    assert isinstance(action, ResizeImageAction)
    assert action.width == 800
    assert action.height == 600


def test_action_parser_markdown_code_fence():
    raw = """Here is the next step:
```json
{
  "type": "add_text",
  "text": "Hello World",
  "position": [250, 180],
  "font_size": 24
}
```
"""
    action = ActionParser.parse(raw)
    assert isinstance(action, AddTextAction)
    assert action.text == "Hello World"
    assert action.position == (250.0, 180.0)
    assert action.font_size == 24


def test_action_parser_invalid():
    with pytest.raises(ActionParseError):
        ActionParser.parse("Not a JSON string at all")

    with pytest.raises(ActionParseError):
        ActionParser.parse({"missing_type": True})


def test_action_validator_constraints():
    window_bbox = (0, 0, 1280, 720)
    allowed = ["click", "resize_image", "stop"]

    # Allowed click inside window
    ok, err = ActionValidator.validate({"type": "click", "x": 100, "y": 100}, allowed, window_bbox)
    assert ok is True

    # Click outside window bbox
    ok, err = ActionValidator.validate({"type": "click", "x": 1500, "y": 100}, allowed, window_bbox)
    assert ok is False
    assert "outside window bbox" in err

    # Action not allowed
    ok, err = ActionValidator.validate({"type": "crop_canvas", "region": "center"}, allowed, window_bbox)
    assert ok is False
    assert "not permitted" in err

    # Invalid resize dimension
    ok, err = ActionValidator.validate({"type": "resize_image", "width": -5, "height": 512}, allowed, window_bbox)
    assert ok is False
    assert "positive integer" in err
