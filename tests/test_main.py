import importlib
import sys
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

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


@pytest.fixture
def main_module(monkeypatch):
    """Import the application only after installing a deterministic environment."""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    yield module
    sys.modules.pop("main", None)


@pytest.fixture
def test_client(main_module):
    with TestClient(main_module.app) as client:
        yield client


def install_freshrss_transport(monkeypatch, main_module, *, payload=None):
    """Stub requests at the FreshRSS boundary, leaving the HTTP app intact."""
    calls = {"post": [], "get": []}

    def fake_post(url, data, timeout):
        calls["post"].append({"url": url, "data": data, "timeout": timeout})
        return FakeResponse(text="SID=ignored\nAuth= transport-token \n")

    def fake_get(url, headers, params, timeout):
        calls["get"].append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        return FakeResponse(payload=payload or {"items": []})

    monkeypatch.setattr(main_module.requests, "post", fake_post)
    monkeypatch.setattr(main_module.requests, "get", fake_get)
    return calls


def test_import_requires_freshrss_environment(monkeypatch):
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("main", None)

    with pytest.raises(RuntimeError) as excinfo:
        importlib.import_module("main")

    assert all(name in str(excinfo.value) for name in REQUIRED_ENV)


def test_health_endpoint_returns_json_over_http(test_client):
    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


def test_unread_uses_default_query_and_authenticates_once(
    test_client, monkeypatch, main_module
):
    calls = install_freshrss_transport(monkeypatch, main_module)

    first = test_client.get("/freshrss/unread")
    second = test_client.get("/freshrss/unread")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == []
    assert calls["post"] == [
        {
            "url": "https://freshrss.example.test/api/greader.php/accounts/ClientLogin",
            "data": {"Email": "reader", "Passwd": "secret"},
            "timeout": 10,
        }
    ]
    assert len(calls["get"]) == 2
    assert calls["get"][0] == {
        "url": "https://freshrss.example.test/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
        "headers": {"Authorization": "GoogleLogin auth=transport-token"},
        "params": {
            "xt": "user/-/state/com.google/read",
            "output": "json",
            "n": 10,
        },
        "timeout": 10,
    }


def test_unread_accepts_explicit_query_and_encoded_category(
    test_client, monkeypatch, main_module
):
    calls = install_freshrss_transport(monkeypatch, main_module)

    response = test_client.get(
        "/freshrss/unread", params={"n": "25", "category": "Tech & Science/News"}
    )

    assert response.status_code == 200
    request = calls["get"][0]
    assert request["params"]["n"] == 25
    assert request["url"].endswith("/user/-/label/Tech%20%26%20Science%2FNews")


@pytest.mark.parametrize("value", ["not-a-number", "1.5", "", "0", "-1"])
def test_unread_rejects_invalid_counts_without_contacting_upstream(
    value, test_client, monkeypatch, main_module
):
    calls = install_freshrss_transport(monkeypatch, main_module)

    response = test_client.get("/freshrss/unread", params={"n": value})

    assert response.status_code == 422
    assert response.json()["detail"]
    assert calls == {"post": [], "get": []}


def test_unread_response_json_structure(test_client, monkeypatch, main_module):
    calls = install_freshrss_transport(
        monkeypatch,
        main_module,
        payload={
            "items": [
                {
                    "title": "Release shipped",
                    "origin": {"title": "GitHub Releases"},
                    "published": 1700000000,
                    "alternate": [{"href": "https://example.test/release"}],
                },
                {"title": "No timestamp", "origin": {"title": "Bad Feed"}},
            ]
        },
    )

    response = test_client.get("/freshrss/unread", params={"n": 5})

    assert response.status_code == 200
    assert calls["get"][0]["params"]["n"] == 5
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) == {"title", "feed", "published", "url", "display"}
    assert body[0] == {
        "title": "Release shipped",
        "feed": "GitHub Releases",
        "published": 1700000000,
        "url": "https://example.test/release",
        "display": body[0]["display"],
    }
    assert body[0]["display"].startswith("Release shipped • ")


def test_unread_serializes_upstream_failure_as_502(
    test_client, monkeypatch, main_module
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            raise_error=requests.HTTPError("500 Server Error")
        ),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS unread request failed"}


# These focused unit tests make token parsing failures easier to diagnose than an
# endpoint-level assertion while still mocking only the requests transport.
def test_get_greader_token_rejects_failed_login(monkeypatch, main_module):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=403, text="nope"),
    )

    with pytest.raises(HTTPException) as excinfo:
        main_module.get_greader_token()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "FreshRSS login failed with status 403"


def test_get_greader_token_rejects_missing_auth_line(monkeypatch, main_module):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="SID=only"),
    )

    with pytest.raises(HTTPException) as excinfo:
        main_module.get_greader_token()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Auth token not found in FreshRSS response"
