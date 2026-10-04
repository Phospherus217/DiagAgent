"""Offline model boundary regressions: no network requests or real GIMP."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
import traceback
import urllib.error
import urllib.request

import pytest

from diagagent.actions.parser import ActionParser, ActionParseError as ParserError
from diagagent.actions.schema import BaseAction
from diagagent.agent.runtime import AgentRuntime
from diagagent.agent.context import ContextBuilder
from diagagent.core.errors import ActionParseError, ExecutionError, ModelAPIError
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.models.base import Message
from diagagent.models.openai_compatible import OpenAICompatibleModel
import diagagent.models.openai_compatible as model_module
from diagagent.tasks import load_task


ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-only-secret-never-persist"


def adapter():
    return OpenAICompatibleModel(
        model_name="offline-test-model",
        base_url=f"https://user:{SECRET}@example.invalid/v1?api_key={SECRET}",
        api_key=SECRET,
    )


def http_error(code):
    return urllib.error.HTTPError(
        f"https://example.invalid/?api_key={SECRET}",
        code,
        f"server echoed Authorization: Bearer {SECRET}",
        {"Authorization": f"Bearer {SECRET}"},
        io.BytesIO(SECRET.encode()),
    )


def mock_failure(monkeypatch, error):
    requests = []

    def fail(request, **kwargs):
        requests.append(request)
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    return requests


def test_api_401_raises_typed_error_without_credential_details(monkeypatch):
    requests = mock_failure(monkeypatch, http_error(401))
    model = adapter()
    with pytest.raises(ModelAPIError) as caught:
        model.query([Message(role="user", content="Resize to 512x512")])

    error = caught.value
    assert error.status_code == 401
    assert error.failure_layer == "MODEL"
    assert error.error_type == "Authentication Error"
    assert len(requests) == 1
    assert SECRET not in json.dumps(error.to_dict())
    assert SECRET not in json.dumps(model.request_metadata())
    assert "Authorization" not in json.dumps(error.to_dict())
    assert not isinstance(error, ActionParseError)


@pytest.mark.parametrize("error,status", [
    (http_error(403), 403),
    (http_error(429), 429),
    (http_error(500), 500),
    (urllib.error.URLError(SECRET), None),
    (TimeoutError(SECRET), None),
    (ValueError(SECRET), None),
])
def test_adapter_error_propagation_never_returns_pseudo_action(monkeypatch, error, status):
    mock_failure(monkeypatch, error)
    with pytest.raises(ModelAPIError) as caught:
        adapter().query([Message(role="user", content="Export")])
    assert caught.value.status_code == status
    assert caught.value.error_type == "Model API Error"
    assert SECRET not in str(caught.value)


def test_adapter_response_decode_error_is_model_failure(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"invalid HTTP response JSON"))
    with pytest.raises(ModelAPIError, match="response processing"):
        adapter().query([Message(role="user", content="Export")])


@pytest.mark.parametrize("raw", [
    {"type": "error", "message": "HTTP Error 401: Unauthorized"},
    '{"type": "unknown_action"}',
    '\x60\x60\x60json\n{"type": "unknown_action"}\n\x60\x60\x60',
    BaseAction(type="unknown_action"),
])
def test_parser_unknown_type_raises_shared_action_parse_error(raw):
    assert ParserError is ActionParseError  # Preserve the existing import path.
    with pytest.raises(ActionParseError, match="Unsupported action"):
        ActionParser.parse(raw)


def test_shared_execution_error_contract():
    error = ExecutionError("primitive failed")
    assert error.failure_layer == "EXECUTION"
    assert error.error_type == "Execution Error"
    assert isinstance(error, RuntimeError)


@pytest.mark.parametrize("source_error,status,failure_type", [
    (http_error(401), 401, "Authentication Error"),
    (http_error(500), 500, "Model API Error"),
    (TimeoutError(SECRET), None, "Model API Error"),
    (urllib.error.URLError(SECRET), None, "Model API Error"),
    (urllib.error.URLError(TimeoutError(SECRET)), None, "Model API Error"),
    (OSError(SECRET), None, "Model API Error"),
])
def test_runtime_model_failure_stops_before_parser_and_persists_safe_trace(
    monkeypatch, tmp_path, source_error, status, failure_type
):
    requests = mock_failure(monkeypatch, source_error)
    task = load_task(ROOT / "benchmark/smoke/resize_export.yaml")
    env = GIMPEnvironment(backend="mock", run_root=tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("model API failure reached the action parser or GUI executor")

    monkeypatch.setattr(ActionParser, "parse", forbidden)
    monkeypatch.setattr(env.executor, "execute", forbidden)
    report = AgentRuntime(env, model=adapter()).run_task(task)

    run = env.trace_recorder.run_dir
    diagnosis = json.loads((run / "diagnosis.json").read_text(encoding="utf-8"))
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    trace = [json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]

    assert len(requests) == result["model_call_count"] == result["total_decisions"] == 1
    assert result["termination_reason"] == "model_error"
    assert result["first_failure_step"] == report.first_failure_step == 2
    assert report.failure_layer == diagnosis["failure_layer"] == "MODEL"
    assert report.failure_type == diagnosis["failure_type"] == result["first_failure_type"] == failure_type
    assert report.responsibility.value == diagnosis["responsibility"] == "Environment"
    assert not report.success and not env.backend.launched
    assert result["executed_primitives"] == 0
    assert not (run / "artifacts/output.png").exists()
    assert [row["step"] for row in trace] == [1, 2]
    assert trace[-1]["action"] is None
    assert trace[-1]["execution_result"]["primitive_results"] == []
    assert trace[-1]["error_type"] == failure_type
    assert not any(e["event"] in {"action_received", "recovery_triggered", "primitive_started"} for e in events)
    request = next(e for e in events if e["event"] == "model_request")
    failure = next(e for e in events if e["event"] == "model_error")
    assert request["status"] == "STARTED" and failure["status"] == "FAILED"
    assert request["step"] == failure["step"] == 2
    assert request["call_id"] == failure["call_id"] == 1
    assert failure["model_error"]["status_code"] == status
    assert failure["model_error"]["failure_layer"] == "MODEL"
    assert failure["model_error"]["exception_type"] == type(source_error).__name__
    assert failure["model_error"]["elapsed_time"] >= 0
    assert failure["model_error"]["timeout_seconds"] == 30.0
    assert request["model_request_metadata"]["timeout_seconds"] == 30.0
    assert request["model_request_metadata"]["model"] == "offline-test-model"
    assert request["model_request_metadata"]["message_count"] == 2
    assert request["model_request_metadata"] == failure["model_request_metadata"]
    for path in run.glob("*.json*"):
        content = path.read_text(encoding="utf-8")
        assert SECRET not in content
        assert "Authorization" not in content
        assert "Unsupported action: error" not in content


def test_successful_adapter_runtime_retains_resize_export_and_status_trace(monkeypatch, tmp_path):
    actions = iter([
        {"type": "resize_image", "width": 512, "height": 512},
        {"type": "export_file", "path": "output.png", "format": "png"},
        {"type": "stop"},
    ])

    def respond(*args, **kwargs):
        body = {"choices": [{"message": {"content": json.dumps(next(actions))}}]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    task = load_task(ROOT / "benchmark/smoke/resize_export.yaml")
    env = GIMPEnvironment(backend="mock", run_root=tmp_path)
    report = AgentRuntime(env, model=adapter()).run_task(task)
    assert report.success and report.failure_layer is None
    assert env.model_call_count == 2
    events = [json.loads(line) for line in (env.trace_recorder.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    completed = [e for e in events if e["event"] == "model_response"]
    assert len(completed) == 2 and all(e["status"] == "SUCCEEDED" for e in completed)
    assert not any(e["event"] == "model_error" for e in events)
    assert SECRET not in json.dumps(events)


@pytest.mark.parametrize("source_error,reason_type,message", [
    (TimeoutError(SECRET), None, "timed out"),
    (urllib.error.URLError(TimeoutError(SECRET)), "TimeoutError", "timed out"),
    (urllib.error.URLError(SECRET), None, "network request failed"),
    (urllib.error.URLError(ConnectionRefusedError(SECRET)), "ConnectionRefusedError", "network request failed"),
    (OSError(SECRET), None, "transport or input I/O failed"),
])
def test_transport_error_preserves_safe_type_and_timing(monkeypatch, source_error, reason_type, message):
    requests = mock_failure(monkeypatch, source_error)
    clock = iter([100.0, 100.0, 130.546])
    monkeypatch.setattr(model_module.time, "monotonic", lambda: next(clock))
    model = adapter()
    model.timeout = 60.0
    with pytest.raises(ModelAPIError, match=message) as caught:
        model.query([Message(role="user", content="Export")])
    error = caught.value
    details = error.to_dict()
    assert details["exception_type"] == type(source_error).__name__
    assert details["reason_exception_type"] == reason_type
    assert details["elapsed_time"] == pytest.approx(30.546)
    assert details["timeout_seconds"] == 60.0
    assert details["failure_layer"] == "MODEL"
    assert details["failure_type"] == "Model API Error"
    assert details["status_code"] is None
    assert len(requests) == 1  # No automatic retry.
    assert SECRET not in json.dumps(details)
    assert SECRET not in "".join(traceback.format_exception(type(error), error, error.__traceback__))


def test_screenshot_io_failure_retains_type_without_network_request(monkeypatch):
    model = adapter()
    requests = mock_failure(monkeypatch, AssertionError("must not reach network"))

    def unreadable(path):
        raise PermissionError(SECRET)

    monkeypatch.setattr(model, "_encode_image", unreadable)
    with pytest.raises(ModelAPIError) as caught:
        model.query([Message(role="user", content="Export", images=["unreadable.png"])])
    assert caught.value.to_dict()["exception_type"] == "PermissionError"
    assert not requests
    assert SECRET not in json.dumps(caught.value.to_dict())


@pytest.mark.parametrize("attachment", ["context", "data_uri", "observation_only", "different_image"])
def test_screenshot_injected_once_and_response_unchanged(monkeypatch, tmp_path, attachment):
    from PIL import Image

    screenshot = tmp_path / "screenshot.png"
    Image.new("RGB", (2, 2), "red").save(screenshot)
    model = adapter()
    model.timeout = 60.0
    observation = SimpleNamespace(screenshot_path=screenshot)
    if attachment == "context":
        messages = ContextBuilder().build_context({}, observation)
    else:
        images = []
        if attachment == "data_uri":
            images = [model._encode_image(str(screenshot))]
        elif attachment == "different_image":
            other = tmp_path / "other.png"
            Image.new("RGB", (2, 2), "blue").save(other)
            images = [str(other)]
        messages = [Message(role="user", content="Export", images=images)]
    original_messages = [message.model_dump() for message in messages]
    body = {"choices": [{"message": {"content": '{"type":"stop"}'}}], "usage": {"total_tokens": 42}}
    requests = []

    def respond(request, *, timeout):
        requests.append(json.loads(request.data))
        assert timeout == 60.0
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    response = model.query(messages, observation=observation)
    assert len(requests) == 1
    content = requests[0]["messages"][-1]["content"]
    image_parts = [part for part in content if part["type"] == "image_url"]
    screenshot_uri = model._encode_image(str(screenshot))
    assert sum(part["image_url"]["url"] == screenshot_uri for part in image_parts) == 1
    assert len(image_parts) == (2 if attachment == "different_image" else 1)
    assert content[0] == {"type": "text", "text": messages[-1].content}
    assert [message.model_dump() for message in messages] == original_messages
    assert response.text == '{"type":"stop"}'
    assert response.raw == body
    assert response.model == model.model_name
    assert response.provider == "openai_compatible"
    assert response.latency_ms >= 0
