"""Explicit, unscaled screenshot-pixel to virtual-desktop transforms."""
from diagagent.executor.atomic import normalize_coordinates


def desktop_action(action, screenshot_bbox=None):
    """Resolve pointer actions once; never clip or infer a space from the sign."""
    if action.type not in {"click", "drag"}:
        return action
    space = action.coordinate_space
    if space == "virtual_desktop":
        return action
    if space != "screenshot" or screenshot_bbox is None:
        raise ValueError("Screenshot coordinates require a captured screenshot_bbox")
    left, top, right, bottom = screenshot_bbox
    if right <= left or bottom <= top:
        raise ValueError("Invalid screenshot_bbox")

    def point(x, y):
        # Validate both the continuous input and the raster pixel actually sent.
        ix, iy = normalize_coordinates(x, y)
        if not (0 <= x < right - left and 0 <= y < bottom - top
                and 0 <= ix < right - left and 0 <= iy < bottom - top):
            raise ValueError("Pointer outside screenshot bounds")
        return x + left, y + top

    updates = {"coordinate_space": "virtual_desktop"}
    if action.type == "click":
        updates["x"], updates["y"] = point(action.x, action.y)
    else:
        updates["start"] = point(*action.start)
        updates["end"] = point(*action.end)
    return action.model_copy(update=updates)


def raster_action(action):
    """Expose the exact rounded points to ownership and bounds validation."""
    if action.type == "click":
        x, y = normalize_coordinates(action.x, action.y)
        return action.model_copy(update={"x": x, "y": y})
    if action.type == "drag":
        return action.model_copy(update={"start": normalize_coordinates(*action.start),
                                         "end": normalize_coordinates(*action.end)})
    return action
