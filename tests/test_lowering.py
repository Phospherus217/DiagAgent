"""Unit tests for semantic action lowering."""

from diagagent.actions.schema import (
    AddTextAction,
    ApplyFilterAction,
    CropCanvasAction,
    ExportFileAction,
    ResizeImageAction,
    SelectToolAction,
)
from diagagent.lowering.lowering_engine import LoweringEngine


def test_lowering_resize():
    engine = LoweringEngine()
    act = ResizeImageAction(width=512, height=512)
    lowered = engine.lower(act)
    types = [a.type for a in lowered]

    assert "open_menu" in types
    assert "set_dialog_field" in types
    assert "click_dialog_button" in types


def test_lowering_crop():
    engine = LoweringEngine()
    act = CropCanvasAction(region="center")
    lowered = engine.lower(act, ui_primitives={"canvas_bbox": (100, 100, 500, 500)})
    types = [a.type for a in lowered]

    assert "click_toolbar_icon" in types
    assert "drag" in types
    assert "hotkey" in types


def test_lowering_add_text():
    engine = LoweringEngine()
    act = AddTextAction(text="Demo", position=(300, 200), font_size=32)
    lowered = engine.lower(act)
    types = [a.type for a in lowered]

    assert "click_toolbar_icon" in types
    assert "click" in types
    assert "type" in types


def test_lowering_apply_filter():
    engine = LoweringEngine()
    act = ApplyFilterAction(filter_name="Gaussian Blur", parameters={"radius": 5})
    lowered = engine.lower(act)
    types = [a.type for a in lowered]

    assert "open_menu" in types
    assert "click_dialog_button" in types


def test_lowering_export():
    engine = LoweringEngine()
    act = ExportFileAction(path="output.png")
    lowered = engine.lower(act)
    types = [a.type for a in lowered]

    assert "open_menu" in types
    assert "set_dialog_field" in types
    assert "click_dialog_button" in types
