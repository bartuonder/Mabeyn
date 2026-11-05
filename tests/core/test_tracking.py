import os

from mabeyn.core.tracking import DEFAULT_SAMPLE_RATE, export_tracking_env


def clear(monkeypatch):
    for name in (
        "LANGSMITH_TRACING_SAMPLING_RATE",
        "LANGCHAIN_TRACING_SAMPLING_RATE",
        "TRACING_SAMPLE_RATE",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_only_a_tenth_of_the_runs_are_recorded_by_default(monkeypatch, tmp_path):
    clear(monkeypatch)
    export_tracking_env(tmp_path / "missing.env")
    assert os.environ["LANGSMITH_TRACING_SAMPLING_RATE"] == DEFAULT_SAMPLE_RATE == "0.1"
    assert os.environ["LANGCHAIN_TRACING_SAMPLING_RATE"] == "0.1"


def test_the_rate_can_be_set_and_explicit_values_win(monkeypatch, tmp_path):
    clear(monkeypatch)
    monkeypatch.setenv("TRACING_SAMPLE_RATE", "1")
    export_tracking_env(tmp_path / "missing.env")
    assert os.environ["LANGSMITH_TRACING_SAMPLING_RATE"] == "1"

    clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING_SAMPLING_RATE", "0.5")
    export_tracking_env(tmp_path / "missing.env")
    assert os.environ["LANGSMITH_TRACING_SAMPLING_RATE"] == "0.5"


def test_tracking_settings_are_still_read_from_the_env_file(monkeypatch, tmp_path):
    clear(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("LANGSMITH_PROJECT=mabeyn\nOTHER=1\n", encoding="utf-8")
    export_tracking_env(env_file)
    assert os.environ["LANGSMITH_PROJECT"] == "mabeyn" and "OTHER" not in os.environ
    monkeypatch.delenv("LANGSMITH_PROJECT")
