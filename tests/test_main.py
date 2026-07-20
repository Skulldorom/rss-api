import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_ENV = {
    "FRESHRSS_HOST": "https://freshrss.example.test",
    "FRESHRSS_USER": "reader",
    "FRESHRSS_PASS": "secret",
}


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None, raise_error=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}
        self._raise_error = raise_error

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self._raise_error:
            raise self._raise_error


def import_app(monkeypatch, env=None):
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or REQUIRED_ENV).items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_import_requires_freshrss_environment(monkeypatch):
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("main", None)

    with pytest.raises(RuntimeError) as excinfo:
        importlib.import_module("main")

    message = str(excinfo.value)
    assert "FRESHRSS_HOST" in message
    assert "FRESHRSS_USER" in message
    assert "FRESHRSS_PASS" in message


def test_health_endpoint_returns_ok(monkeypatch):
    main = import_app(monkeypatch)

    assert main.health() == {"status": "ok"}
    assert any(route.path == "/health" for route in main.app.routes)


def test_get_greader_token_logs_in_once_and_caches_token(monkeypatch):
    main = import_app(monkeypatch)
    calls = []

    def fake_post(url, data, timeout):
        calls.append({"url": url, "data": data, "timeout": timeout})
        return FakeResponse(text="SID=ignored\nAuth= cached-token \n")

    monkeypatch.setattr(main.requests, "post", fake_post)

    assert main.get_greader_token() == "cached-token"
    assert main.get_greader_token() == "cached-token"
    assert calls == [
        {
            "url": "https://freshrss.example.test/api/greader.php/accounts/ClientLogin",
            "data": {"Email": "reader", "Passwd": "secret"},
            "timeout": 10,
        }
    ]


def test_get_greader_token_rejects_failed_login(monkeypatch):
    main = import_app(monkeypatch)
    monkeypatch.setattr(main.requests, "post", lambda *args, **kwargs: FakeResponse(status_code=403, text="nope"))

    with pytest.raises(HTTPException) as excinfo:
        main.get_greader_token()

    assert excinfo.value.status_code == 502
    assert "FreshRSS login failed with status 403" == excinfo.value.detail


def test_get_greader_token_rejects_missing_auth_line(monkeypatch):
    main = import_app(monkeypatch)
    monkeypatch.setattr(main.requests, "post", lambda *args, **kwargs: FakeResponse(text="SID=only"))

    with pytest.raises(HTTPException) as excinfo:
        main.get_greader_token()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Auth token not found in FreshRSS response"


def test_get_greader_token_wraps_request_failures(monkeypatch):
    main = import_app(monkeypatch)

    def fake_post(*args, **kwargs):
        raise requests.Timeout("slow upstream")

    monkeypatch.setattr(main.requests, "post", fake_post)

    with pytest.raises(HTTPException) as excinfo:
        main.get_greader_token()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "FreshRSS login request failed"


def test_freshrss_unread_fetches_reading_list_and_shapes_items(monkeypatch):
    main = import_app(monkeypatch)
    monkeypatch.setattr(main, "get_greader_token", lambda: "token-123")
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured.update({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return FakeResponse(
            payload={
                "items": [
                    {
                        "title": "Release shipped",
                        "origin": {"title": "GitHub Releases"},
                        "published": 1700000000,
                        "alternate": [{"href": "https://example.test/release"}],
                    },
                    {
                        "title": "Missing timestamp is ignored",
                        "origin": {"title": "Bad Feed"},
                    },
                ]
            }
        )

    monkeypatch.setattr(main.requests, "get", fake_get)

    result = main.freshrss_unread(n=5)

    assert captured == {
        "url": "https://freshrss.example.test/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
        "headers": {"Authorization": "GoogleLogin auth=token-123"},
        "params": {"xt": "user/-/state/com.google/read", "output": "json", "n": 5},
        "timeout": 10,
    }
    assert len(result) == 1
    assert result[0]["title"] == "Release shipped"
    assert result[0]["feed"] == "GitHub Releases"
    assert result[0]["published"] == 1700000000
    assert result[0]["url"] == "https://example.test/release"
    assert result[0]["display"].startswith("Release shipped • ")


def test_freshrss_unread_scopes_to_category_and_handles_missing_url(monkeypatch):
    main = import_app(monkeypatch)
    monkeypatch.setattr(main, "get_greader_token", lambda: "token-123")
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured.update({"url": url, "params": params})
        return FakeResponse(
            payload={
                "items": [
                    {
                        "title": "Category item",
                        "origin": {},
                        "published": 1700000000,
                        "alternate": [],
                    }
                ]
            }
        )

    monkeypatch.setattr(main.requests, "get", fake_get)

    result = main.freshrss_unread(n=3, category="Tech")

    assert captured["url"].endswith("/stream/contents/user/-/label/Tech")
    assert captured["params"]["n"] == 3
    assert result[0]["feed"] is None
    assert result[0]["url"] == ""


def test_freshrss_unread_wraps_upstream_http_errors(monkeypatch):
    main = import_app(monkeypatch)
    monkeypatch.setattr(main, "get_greader_token", lambda: "token-123")
    upstream_error = requests.HTTPError("500 Server Error")
    monkeypatch.setattr(main.requests, "get", lambda *args, **kwargs: FakeResponse(raise_error=upstream_error))

    with pytest.raises(HTTPException) as excinfo:
        main.freshrss_unread()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "FreshRSS unread request failed"
