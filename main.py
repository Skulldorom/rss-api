import logging
import os
import threading
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Query
import requests
import humanize
from datetime import datetime, timezone

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
AUTH_TOKEN_LOCK = threading.Lock()


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
    category_label = category if isinstance(category, str) and category else None
    stream_id = f"user/-/label/{category_label}" if category_label else "user/-/state/com.google/reading-list"
    url = f"{FRESHRSS_HOST}/api/greader.php/reader/api/0/stream/contents/{stream_id}"
    return requests.get(url, headers=headers, params=params, timeout=10)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/freshrss/unread")
def freshrss_unread(
    n: int = Query(default=10, ge=1, le=100),
    category: str | None = Query(default=None),
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
    items = []

    now = datetime.now(timezone.utc)

    for entry in raw.get("items", []):
        published_ts = entry.get("published")
        if published_ts is None:
            continue
        published_dt = datetime.fromtimestamp(published_ts, timezone.utc)
        published_str = humanize.naturaltime(now - published_dt)
        alternates = entry.get("alternate") or []
        item_url = alternates[0].get("href", "") if alternates else ""
        items.append(
            {
                "title": entry.get("title"),
                "feed": entry.get("origin", {}).get("title"),
                "published": entry.get("published"),
                "url": item_url,
                "display": f"{entry.get('title')} • {published_str}",
            }
        )
    return items


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
