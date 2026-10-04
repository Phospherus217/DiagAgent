"""CLI model configuration tests without network requests or GUI execution."""

from pathlib import Path
from unittest.mock import Mock
import urllib.request

from click.testing import CliRunner
import pytest

from diagagent.cli.main import main
import diagagent.cli.run as run_module
import diagagent.models.openai_compatible as model_module


TASK = Path(__file__).resolve().parents[1] / "benchmark/tasks/resize_image.yaml"
CONFIG = {
    "DIAGAGENT_API_KEY": "test-only-diagagent-key",
    "DIAGAGENT_BASE_URL": "https://diagagent.example.invalid/compatible-mode/v1",
    "DIAGAGENT_MODEL": "qwen3-vl-plus",
}


@pytest.fixture
def isolated_cli(monkeypatch):
    monkeypatch.delenv("DIAGAGENT_MODEL_TIMEOUT", raising=False)
    for name, value in CONFIG.items():
        monkeypatch.setenv(name, value)
    # Conflicting legacy values must never override the DiagAgent configuration.
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-wrong-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://wrong.example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "wrong-model")
    constructor = Mock(wraps=model_module.OpenAICompatibleModel)
    environment = Mock()
    runtime = Mock()
    runtime.return_value.run_task.return_value = Mock(
        success=True, termination_reason=None,
        format_cli=Mock(return_value="isolated configuration test"),
    )
    monkeypatch.setattr(model_module, "OpenAICompatibleModel", constructor)
    monkeypatch.setattr(run_module, "GIMPEnvironment", environment)
    monkeypatch.setattr(run_module, "AgentRuntime", runtime)

    def forbidden_request(*args, **kwargs):
        pytest.fail("Configuration tests must not make network requests")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_request)
    return constructor, environment, runtime


@pytest.mark.parametrize("adapter", ["openai", "openai_compat", "vlm"])
def test_cli_injects_diagagent_model_configuration(isolated_cli, adapter):
    constructor, environment, runtime = isolated_cli
    result = CliRunner().invoke(main, ["run", str(TASK), "--model", adapter])

    assert result.exit_code == 0, result.output
    constructor.assert_called_once_with(
        api_key=CONFIG["DIAGAGENT_API_KEY"],
        base_url=CONFIG["DIAGAGENT_BASE_URL"],
        model_name=CONFIG["DIAGAGENT_MODEL"],
        timeout=30.0,
    )
    model = runtime.call_args.kwargs["model"]
    assert model.api_key == CONFIG["DIAGAGENT_API_KEY"]
    assert model.base_url == CONFIG["DIAGAGENT_BASE_URL"]
    assert model.model_name == CONFIG["DIAGAGENT_MODEL"]
    assert model.timeout == 30.0
    assert runtime.return_value.run_task.call_args.kwargs["model"] is model
    environment.return_value.close.assert_called_once()
    assert CONFIG["DIAGAGENT_API_KEY"] not in result.output


@pytest.mark.parametrize("variable", list(CONFIG))
@pytest.mark.parametrize("value", [None, "", " \t"])
@pytest.mark.parametrize("legacy_present", [False, True])
def test_cli_rejects_missing_config_without_fallback(
    monkeypatch, isolated_cli, variable, value, legacy_present
):
    constructor, environment, runtime = isolated_cli
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    if not legacy_present:
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
            monkeypatch.delenv(name)

    result = CliRunner().invoke(main, ["run", str(TASK), "--model", "openai_compat"])

    assert result.exit_code == 2  # Preserve the existing CLI exception exit code.
    assert f"{variable} must be set and non-empty" in result.output
    constructor.assert_not_called()
    environment.assert_not_called()
    runtime.assert_not_called()
    assert CONFIG["DIAGAGENT_API_KEY"] not in result.output


@pytest.mark.parametrize("arguments", [[], ["--agent", "mock_fault"], ["--model", "mock"]])
def test_cli_non_api_agents_do_not_require_model_configuration(
    monkeypatch, isolated_cli, arguments
):
    constructor, environment, runtime = isolated_cli
    for variable in CONFIG:
        monkeypatch.delenv(variable)
    monkeypatch.setenv("DIAGAGENT_MODEL_TIMEOUT", "invalid-unused-value")

    result = CliRunner().invoke(main, ["run", str(TASK), *arguments])

    assert result.exit_code == 0, result.output
    constructor.assert_not_called()
    environment.assert_called_once()
    runtime.return_value.run_task.assert_called_once()


@pytest.mark.parametrize("value", ["60", "2.5"])
def test_cli_model_timeout_configuration(monkeypatch, isolated_cli, value):
    constructor, _, runtime = isolated_cli
    monkeypatch.setenv("DIAGAGENT_MODEL_TIMEOUT", value)
    result = CliRunner().invoke(main, ["run", str(TASK), "--model", "openai_compat"])
    assert result.exit_code == 0, result.output
    assert constructor.call_args.kwargs["timeout"] == float(value)
    assert runtime.call_args.kwargs["model"].timeout == float(value)


@pytest.mark.parametrize("value", ["", "invalid", "0", "-1", "nan", "inf", "-inf", "1e999"])
def test_cli_rejects_invalid_model_timeout(monkeypatch, isolated_cli, value):
    constructor, environment, runtime = isolated_cli
    monkeypatch.setenv("DIAGAGENT_MODEL_TIMEOUT", value)
    result = CliRunner().invoke(main, ["run", str(TASK), "--model", "openai_compat"])
    assert result.exit_code == 2
    assert "DIAGAGENT_MODEL_TIMEOUT must be a finite positive number" in result.output
    constructor.assert_not_called()
    environment.assert_not_called()
    runtime.assert_not_called()
