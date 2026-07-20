import logging
import os
import secrets
import threading
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import requests
import humanize
from datetime import datetime, timezone
from math import isfinite
from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, ValidationError, field_validator

# Print ASCII skull
skull = r"""
            ⠄⠄⠄⠄⠄⠄⠄⣀⣠⣤⣤⣤⣤⣀⡀
            ⠄⠄⠄⣠⣤⢶⣻⣿⣻⣿⣿⣿⣿⣿⣿⣦⣤⣀
            ⠄⠄⣼⣺⢷⣻⣽⣾⣿⢿⣿⣷⣿⣿⢿⣿⣿⣿⣇
            ⠠⡍⢾⣺⢽⡳⣻⡺⣽⢝⢗⢯⣻⢽⣻⣿⣿⣿⣿⢿⡄
            ⡨⣖⢹⠜⢅⢫⢊⢎⠜⢌⠣⢑⠡⣹⡸⣜⣯⣿⢿⣻⣷
            ⢜⢔⡹⡭⣪⢼⠽⠷⠧⣳⢘⢔⡝⠾⠽⢿⣷⣿⣟⢷⣟
            ⢸⢘⢼⠿⠟⠁⠄⠄⡀⠄⠃⠑⡌⠄⠄⠈⠙⠿⣷⢽⣻
            ⢌⠂⠅⠄⠄⠄⠄⠄⠄⡀⣲⣢⢂⠄⠄⠄⠄⠄⠈⣯⠏
            ⠐⠨⡂⠄⠄⠄⠄⠄⡀⡔⠋⢻⣤⡀⠄⠄⢀⠄⢸⣯⠇
            ⠈⣕⠝⠒⠄⠄⠒⢉⠪⠄⠄⠄⢿⠜⠑⠢⠠⡒⡺⣿⠖
            ⠄⠐⠅⠁⡀⠄⠐⢔⠁⠄⠄⠄⢀⢇⢌⠄⠄⠄⠸⠕
            ⠄⠄⠂⠄⠄⠨⣔⡝⠼⡄⠂⣦⡆⣿⣲⠐⠑⠁⠄⠃
            ⠄⠄⠄⠄⠄⠄⠃⢫⢛⣙⡊⣜⣏⡝⣝⠆
            ⠄⠄⠄⠄⠄⠄⠈⠈⠁⠁⠁⠈⠈⠊

            RSS api - Starting...
"""
print(skull)

FRESHRSS_HOST = os.environ.get("FRESHRSS_HOST")
FRESHRSS_USERNAME = os.environ.get("FRESHRSS_USER")
FRESHRSS_PASSWORD = os.environ.get("FRESHRSS_PASS")
RSS_API_TOKEN = os.environ.get("RSS_API_TOKEN")  # optional — when unset, auth is skipped

_missing = [
    key
    for key, value in {
        "FRESHRSS_HOST": FRESHRSS_HOST,
        "FRESHRSS_USER": FRESHRSS_USERNAME,
        "FRESHRSS_PASS": FRESHRSS_PASSWORD,
    }.items()
    if not value
]
if _missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(_missing)}")

app = FastAPI()
bearer_scheme = HTTPBearer(auto_error=False)

AUTH_TOKEN = None
AUTH_TOKEN_LOCK = threading.Lock()


class FreshRSSOrigin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str | None = None


class FreshRSSAlternate(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    href: str | None = None


class FreshRSSItem(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str | None = None
    origin: FreshRSSOrigin | None = None
    published: StrictInt | StrictFloat | None = None
    alternate: list[FreshRSSAlternate] | None = None

    @field_validator("published")
    @classmethod
    def validate_published_timestamp(cls, value):
        if value is None:
            return value
        try:
            if not isfinite(value):
                raise ValueError("timestamp must be finite")
            datetime.fromtimestamp(value, timezone.utc)
        except OverflowError:
            raise ValueError("timestamp is outside the supported range")
        except (OSError, ValueError) as exc:
            raise ValueError("timestamp is outside the supported range") from exc
        return value


class FreshRSSResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    items: list[FreshRSSItem]


def validate_freshrss_response(raw):
    """Validate FreshRSS data, rejecting the whole malformed response.

    A response with any malformed item produces a sanitized 502 rather than a
    partial result. This keeps upstream data-quality failures visible and gives
    callers consistent all-or-nothing results.
    """
    try:
        return FreshRSSResponse.model_validate(raw)
    except ValidationError as exc:
        logging.warning("FreshRSS returned an invalid unread response: %s", exc)
        raise HTTPException(status_code=502, detail="FreshRSS returned an invalid unread response") from exc


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Require the configured bearer token when RSS_API_TOKEN is set.

    When RSS_API_TOKEN is not configured, all requests pass through without
    authentication — use this for trusted/internal networks. When set, every
    protected endpoint demands a matching ``Authorization: Bearer <token>`` header.
    """
    if not RSS_API_TOKEN:
        return  # auth is disabled
    if credentials is None or not secrets.compare_digest(credentials.credentials, RSS_API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_greader_token():
    global AUTH_TOKEN
    with AUTH_TOKEN_LOCK:
        if AUTH_TOKEN:
            return AUTH_TOKEN
        login_url = f"{FRESHRSS_HOST}/api/greader.php/accounts/ClientLogin"
        payload = {
            "Email": FRESHRSS_USERNAME,
            "Passwd": FRESHRSS_PASSWORD,
        }
        try:
            res = requests.post(login_url, data=payload, timeout=10)
        except requests.RequestException as exc:
            logging.warning("FreshRSS login request failed: %s", exc)
            raise HTTPException(status_code=502, detail="FreshRSS login request failed") from exc
        if res.status_code != 200:
            upstream_host = urlsplit(FRESHRSS_HOST).hostname or "unknown"
            logging.warning(
                "FreshRSS login failed (status=%d, upstream_host=%s)",
                res.status_code,
                upstream_host,
            )
            raise HTTPException(status_code=502, detail=f"FreshRSS login failed with status {res.status_code}")
        # Find and extract 'Auth=' line
        for line in res.text.splitlines():
            if line.startswith("Auth="):
                AUTH_TOKEN = line.replace("Auth=", "").strip()
                return AUTH_TOKEN
        raise HTTPException(status_code=502, detail="Auth token not found in FreshRSS response")


def request_unread(token, n, category):
    """Construct and send one upstream unread request."""
    headers = {"Authorization": f"GoogleLogin auth={token}"}
    params = {
        "xt": "user/-/state/com.google/read",
        "output": "json",
        "n": n,
    }
    category_label = category.strip() if isinstance(category, str) else None
    if category_label in (".", ".."):
        category_label = None  # dot-only labels resolve as path traversal; use reading-list
    stream_id = (
        f"user/-/label/{quote(category_label, safe='')}"
        if category_label
        else "user/-/state/com.google/reading-list"
    )
    url = f"{FRESHRSS_HOST}/api/greader.php/reader/api/0/stream/contents/{stream_id}"
    return requests.get(url, headers=headers, params=params, timeout=10)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/freshrss/unread", dependencies=[Depends(require_api_token)])
def freshrss_unread(
    n: int = Query(default=10, ge=1, le=100),
    category: str | None = Query(default=None, max_length=200),
):
    token = get_greader_token()
    try:
        r = request_unread(token, n, category)
        if r.status_code in (401, 403):
            global AUTH_TOKEN
            with AUTH_TOKEN_LOCK:
                if AUTH_TOKEN == token:
                    AUTH_TOKEN = None
            token = get_greader_token()
            r = request_unread(token, n, category)
        r.raise_for_status()
        raw = r.json()
    except requests.RequestException as exc:
        logging.warning("FreshRSS unread request failed: %s", exc)
        raise HTTPException(status_code=502, detail="FreshRSS unread request failed") from exc
    response = validate_freshrss_response(raw)
    items = []

    now = datetime.now(timezone.utc)

    for entry in response.items:
        published_ts = entry.published
        if published_ts is None:
            continue
        published_dt = datetime.fromtimestamp(published_ts, timezone.utc)
        published_str = humanize.naturaltime(now - published_dt)
        alternates = entry.alternate or []
        item_url = alternates[0].href or "" if alternates else ""
        items.append(
            {
                "title": entry.title,
                "feed": entry.origin.title if entry.origin else None,
                "published": published_ts,
                "url": item_url,
                "display": f"{entry.title} • {published_str}",
            }
        )
    return items


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
