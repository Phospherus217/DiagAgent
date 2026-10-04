"""Typed desktop failures preserve responsibility through the runtime."""
class DesktopError(RuntimeError):
    def __init__(self, message, error_type="Environment Error"):
        super().__init__(message)
        self.error_type = error_type
