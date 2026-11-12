"""The Go Google-compatible transport (newsroom/tool_loop/go_transport.py). No network, no key.

Run: pytest -m "not integration" tests/test_go_transport.py
"""
import importlib
import os

import pytest


def _reload(monkeypatch, key=None, session=None):
    for name in ("OPENCODE_GO_API_KEY", "MU_GO_SESSION"):
        monkeypatch.delenv(name, raising=False)
    if key is not None:
        monkeypatch.setenv("OPENCODE_GO_API_KEY", key)
    if session is not None:
        monkeypatch.setenv("MU_GO_SESSION", session)
    import newsroom.tool_loop.go_transport as transport
    return importlib.reload(transport)


def test_client_refuses_without_a_key(monkeypatch):
    transport = _reload(monkeypatch, key=None)
    monkeypatch.setattr(transport, "_env", lambda name: None)
    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        transport.go_client()


def test_client_points_at_the_go_google_path_with_a_session_header(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test", session="mu-test-1")
    client = transport.go_client()
    options = client._api_client._http_options
    assert options.base_url == "https://opencode.ai/inference/go/google"
    assert options.headers["x-opencode-session"] == "mu-test-1"


def test_session_id_is_unique_per_process_and_time(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test")
    monkeypatch.setattr(transport.os, "getpid", lambda: 1234)
    monkeypatch.setattr(transport.time, "time", lambda: 1000.0)
    assert transport.go_session_id("mu") == "mu-1234-1000"
    monkeypatch.setattr(transport.time, "time", lambda: 1001.0)
    assert transport.go_session_id("mu") == "mu-1234-1001"


def test_is_go_model_splits_gemini_from_go_ids(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test")
    for model in ("muse-spark-1.3-contributor", "glm-5.3-flash", "deepseek-v4.1-flash", "kimi-k3"):
        assert transport.is_go_model(model) is True, model
    for model in ("gemini-3.8-flash", "gemini:3.8", "gemini/4-argon", ""):
        assert transport.is_go_model(model) is False, model


def test_module_imports_without_the_sdk(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError("no SDK here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    import importlib as _importlib
    import newsroom.tool_loop.go_transport as transport
    _importlib.reload(transport)                      # module load must not need google.genai
