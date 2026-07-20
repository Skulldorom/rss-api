import importlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

AUTH_TOKEN_VALUE = "api-test-token"


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


# ── Environment / import ──────────────────────────────────────────────


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
    # RSS_API_TOKEN is optional — not required for import


# ── Health endpoint ───────────────────────────────────────────────────


def test_health_endpoint_returns_json_over_http(test_client):
    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


# ── Authentication ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}],
    ids=["missing", "invalid"],
)
def test_unread_rejects_unauthorized_requests_without_contacting_freshrss(
    test_client, monkeypatch, main_module, headers
):
    main_module.RSS_API_TOKEN = AUTH_TOKEN_VALUE  # enable auth for this test
    monkeypatch.setattr(main_module, "get_greader_token", lambda: "unreachable")

    def unexpected_request(*args, **kwargs):
        pytest.fail("unauthorized requests must not contact FreshRSS")

    monkeypatch.setattr(main_module.requests, "post", unexpected_request)
    monkeypatch.setattr(main_module.requests, "get", unexpected_request)

    response = test_client.get("/freshrss/unread", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API token"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_unread_accepts_valid_credentials(test_client, monkeypatch, main_module):
    main_module.RSS_API_TOKEN = AUTH_TOKEN_VALUE  # enable auth for this test
    calls = install_freshrss_transport(monkeypatch, main_module)

    response = test_client.get(
        "/freshrss/unread",
        headers={"Authorization": "Bearer api-test-token"},
    )

    assert response.status_code == 200
    assert response.json() == []
    assert len(calls["post"]) == 1  # authenticated and logged in


def test_unread_allows_requests_when_token_not_configured(monkeypatch):
    """When RSS_API_TOKEN is unset, auth is skipped entirely."""
    env_without_token = {k: v for k, v in REQUIRED_ENV.items() if k != "RSS_API_TOKEN"}
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env_without_token.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")

    # Stub FreshRSS transport so we don't need a real server
    monkeypatch.setattr(
        module.requests, "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=no-auth-token\n"),
    )
    monkeypatch.setattr(
        module.requests, "get",
        lambda *args, **kwargs: FakeResponse(payload={"items": []}),
    )

    with TestClient(module.app) as client:
        # No Authorization header at all — should still work
        response = client.get("/freshrss/unread")

    assert response.status_code == 200
    assert response.json() == []

    sys.modules.pop("main", None)


# ── Unread endpoint (via TestClient) ──────────────────────────────────


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


@pytest.mark.parametrize(
    ("category", "expected_stream"),
    [
        ("  Tech News  ", "user/-/label/Tech%20News"),
        ("Tech/News", "user/-/label/Tech%2FNews"),
        ("100% News", "user/-/label/100%25%20News"),
        ("What?", "user/-/label/What%3F"),
        ("日本語", "user/-/label/%E6%97%A5%E6%9C%AC%E8%AA%9E"),
        ("  \t\n ", "user/-/state/com.google/reading-list"),
        (".", "user/-/state/com.google/reading-list"),
        ("..", "user/-/state/com.google/reading-list"),
    ],
)
def test_freshrss_unread_normalizes_and_encodes_category(
    test_client, monkeypatch, main_module, category, expected_stream
):
    calls = install_freshrss_transport(monkeypatch, main_module)

    response = test_client.get("/freshrss/unread", params={"category": category})

    assert response.status_code == 200
    assert calls["get"][0]["url"] == (
        "https://freshrss.example.test/api/greader.php/reader/api/0/stream/contents/"
        f"{expected_stream}"
    )


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


# ── Token / login unit tests ──────────────────────────────────────────


def test_get_greader_token_rejects_failed_login_without_logging_secrets(
    monkeypatch, caplog
):
    """Verify that no credentials or response body leak into warning logs."""
    credentials = {
        "FRESHRSS_HOST": "https://freshrss.example.test",
        "FRESHRSS_USER": "sentinel-username",
        "FRESHRSS_PASS": "sentinel-password",
    }
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")

    response_body = "sentinel-response-body\nAuth=sentinel-token"
    monkeypatch.setattr(
        main.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=403, text=response_body),
    )

    with caplog.at_level("WARNING"):
        with pytest.raises(HTTPException) as excinfo:
            main.get_greader_token()

    assert excinfo.value.status_code == 502
    assert "FreshRSS login failed with status 403" == excinfo.value.detail
    assert "status=403" in caplog.text
    assert "upstream_host=freshrss.example.test" in caplog.text
    for secret in (*credentials.values(), response_body, "sentinel-token"):
        assert secret not in caplog.text

    sys.modules.pop("main", None)


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


def test_get_greader_token_wraps_request_failures(monkeypatch, main_module):
    def fake_post(*args, **kwargs):
        raise requests.Timeout("slow upstream")

    monkeypatch.setattr(main_module.requests, "post", fake_post)

    with pytest.raises(HTTPException) as excinfo:
        main_module.get_greader_token()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "FreshRSS login request failed"


# ── Reauthentication ──────────────────────────────────────────────────


def test_unread_reauthenticates_and_retries_once(
    test_client, monkeypatch, main_module
):
    main_module.AUTH_TOKEN = "expired-token"
    login_calls = []
    get_calls = []

    def fake_post(*args, **kwargs):
        login_calls.append(1)
        return FakeResponse(text="Auth=fresh-token")

    def fake_get(url, headers, params, timeout):
        get_calls.append(headers["Authorization"])
        if len(get_calls) == 1:
            return FakeResponse(status_code=401)
        return FakeResponse(payload={"items": []})

    monkeypatch.setattr(main_module.requests, "post", fake_post)
    monkeypatch.setattr(main_module.requests, "get", fake_get)

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 200
    assert response.json() == []
    assert get_calls == [
        "GoogleLogin auth=expired-token",
        "GoogleLogin auth=fresh-token",
    ]
    assert len(login_calls) == 1
    assert main_module.AUTH_TOKEN == "fresh-token"


def test_unread_fails_after_single_reauthentication_retry(
    test_client, monkeypatch, main_module
):
    main_module.AUTH_TOKEN = "expired-token"
    get_calls = []
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=fresh-token"),
    )

    def fake_get(url, headers, params, timeout):
        get_calls.append(headers["Authorization"])
        return FakeResponse(status_code=403)

    monkeypatch.setattr(main_module.requests, "get", fake_get)

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}
    assert get_calls == [
        "GoogleLogin auth=expired-token",
        "GoogleLogin auth=fresh-token",
    ]


# ── Concurrent safety ─────────────────────────────────────────────────


def test_get_greader_token_is_concurrent_safe(monkeypatch, main_module):
    login_calls = 0
    calls_lock = threading.Lock()
    workers_ready = threading.Barrier(5)

    def fake_post(*args, **kwargs):
        nonlocal login_calls
        with calls_lock:
            login_calls += 1
        time.sleep(0.05)
        return FakeResponse(text="Auth=shared-token")

    def acquire_token():
        workers_ready.wait()
        return main_module.get_greader_token()

    monkeypatch.setattr(main_module.requests, "post", fake_post)
    with ThreadPoolExecutor(max_workers=5) as executor:
        tokens = list(executor.map(lambda _: acquire_token(), range(5)))

    assert tokens == ["shared-token"] * 5
    assert login_calls == 1


# ── Pydantic response validation ──────────────────────────────────────


@pytest.mark.parametrize("payload", [[], "not an object", None])
def test_unread_rejects_malformed_top_level_payload(
    test_client, monkeypatch, main_module, payload
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    response_obj = FakeResponse(payload={})
    response_obj._payload = payload
    monkeypatch.setattr(
        main_module.requests, "get", lambda *args, **kwargs: response_obj
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}


@pytest.mark.parametrize("items", [None, {}, "not a list", ["not an object"]])
def test_unread_rejects_invalid_items(
    test_client, monkeypatch, main_module, items
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload={"items": items}),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}


@pytest.mark.parametrize(
    "invalid_field",
    [
        {"origin": "not an object"},
        {"origin": []},
        {"alternate": "not a list"},
        {"alternate": {}},
        {"alternate": ["not an object"]},
    ],
)
def test_unread_rejects_malformed_item_containers(
    test_client, monkeypatch, main_module, invalid_field
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    item = {"title": "Bad item", "published": 1700000000, **invalid_field}
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload={"items": [item]}),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}


@pytest.mark.parametrize(
    "timestamp", ["1700000000", True, float("nan"), float("inf")]
)
def test_unread_rejects_nonnumeric_timestamps(
    test_client, monkeypatch, main_module, timestamp
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    payload = {"items": [{"title": "Bad timestamp", "published": timestamp}]}
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload=payload),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}


@pytest.mark.parametrize("timestamp", [10**30, -(10**30)])
def test_unread_rejects_out_of_range_timestamps(
    test_client, monkeypatch, main_module, timestamp
):
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    payload = {"items": [{"title": "Bad timestamp", "published": timestamp}]}
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload=payload),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}


def test_unread_rejects_timestamp_overflowing_isfinite(
    test_client, monkeypatch, main_module
):
    """A huge int that overflows math.isfinite() must still produce a 502, not 500."""
    monkeypatch.setattr(
        main_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(text="Auth=token\n"),
    )
    huge = 10**1000  # far beyond float range — isfinite() raises OverflowError
    payload = {"items": [{"title": "Overflow timestamp", "published": huge}]}
    monkeypatch.setattr(
        main_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload=payload),
    )

    response = test_client.get("/freshrss/unread")

    assert response.status_code == 502
    assert response.json() == {"detail": "FreshRSS returned an invalid unread response"}
