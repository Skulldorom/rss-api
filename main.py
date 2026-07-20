import logging
import os
from fastapi import FastAPI, HTTPException, Query
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

_missing = [k for k, v in {"FRESHRSS_HOST": FRESHRSS_HOST, "FRESHRSS_USER": FRESHRSS_USERNAME, "FRESHRSS_PASS": FRESHRSS_PASSWORD}.items() if not v]
if _missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(_missing)}")

app = FastAPI()

AUTH_TOKEN = None


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
        if not isfinite(value):
            raise ValueError("timestamp must be finite")
        try:
            datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
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


def get_greader_token():
    global AUTH_TOKEN
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
        logging.warning("FreshRSS login failed (status %d): %s", res.status_code, res.text)
        raise HTTPException(status_code=502, detail=f"FreshRSS login failed with status {res.status_code}")
    # Find and extract 'Auth=' line
    for line in res.text.splitlines():
        if line.startswith("Auth="):
            AUTH_TOKEN = line.replace("Auth=", "").strip()
            return AUTH_TOKEN
    raise HTTPException(status_code=502, detail="Auth token not found in FreshRSS response")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/freshrss/unread")
def freshrss_unread(
    n: int = Query(default=10, ge=1),
    category: str | None = Query(default=None),
):
    token = get_greader_token()
    headers = {"Authorization": f"GoogleLogin auth={token}"}
    params = {
        "xt": "user/-/state/com.google/read",
        "output": "json",
        "n": n,
    }
    category_label = category if isinstance(category, str) and category else None
    stream_id = f"user/-/label/{category_label}" if category_label else "user/-/state/com.google/reading-list"
    # Using the same host as before but with the right endpoint
    url = f"{FRESHRSS_HOST}/api/greader.php/reader/api/0/stream/contents/{stream_id}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
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
