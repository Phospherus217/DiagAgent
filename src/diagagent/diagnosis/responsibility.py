"""v0.2 terminology; the original benchmark taxonomy remains unchanged."""
ALIASES = {"Authentication Error": "API Error", "Model API Error": "API Error",
           "Launch / Window Error": "Window Error", "Dialog Operation Error": "Dialog Error",
           "Execution No-op Error": "Execution No-op"}
RESPONSIBILITY = {
    "Action Format Error": "Agent", "Tool Selection Error": "Agent", "Parameter Error": "Agent",
    "Canvas Target Error": "Agent", "API Error": "Environment", "Dialog Error": "Environment",
    "Window Error": "Environment", "Environment Error": "Environment",
    "Execution No-op": "Execution", "File I/O Error": "Execution", "UI Grounding Error": "Execution",
    "Execution Error": "Execution", "Artifact Error": "Artifact", "False Completion": "Artifact",
    "Evaluator Error": "Evaluator",
}


def attribute(failure_type):
    canonical = ALIASES.get(failure_type, failure_type)
    responsibility = RESPONSIBILITY.get(canonical)
    boundary = "MODEL" if canonical == "API Error" else (
        "ACTION" if responsibility == "Agent" else
        "ARTIFACT" if responsibility == "Artifact" else
        "EVALUATOR" if responsibility == "Evaluator" else
        "ENVIRONMENT" if responsibility == "Environment" else "EXECUTION" if responsibility else None)
    return canonical, responsibility, boundary
