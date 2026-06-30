import os
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


def get_greader_token():
    global AUTH_TOKEN
    if AUTH_TOKEN:
        return AUTH_TOKEN
    login_url = f"{FRESHRSS_HOST}/api/greader.php/accounts/ClientLogin"
    payload = {
        "Email": FRESHRSS_USERNAME,
        "Passwd": FRESHRSS_PASSWORD,
    }
    res = requests.post(login_url, data=payload, timeout=10)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"FreshRSS login failed: {res.text}")
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
def freshrss_unread(n: int = Query(default=10, ge=1)):
    token = get_greader_token()
    headers = {"Authorization": f"GoogleLogin auth={token}"}
    params = {
        "xt": "user/-/state/com.google/read",
        "output": "json",
        "n": n,
    }
    # Using the same host as before but with the right endpoint
    url = f"{FRESHRSS_HOST}/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list"
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    raw = r.json()
    items = []

    now = datetime.now(timezone.utc)

    for entry in raw.get("items", []):
        published_ts = entry.get("published")
        if published_ts is None:
            continue
        published_dt = datetime.fromtimestamp(published_ts, timezone.utc)
        published_str = humanize.naturaltime(now - published_dt)
        items.append(
            {
                "title": entry.get("title"),
                "feed": entry.get("origin", {}).get("title"),
                "published": entry.get("published"),
                "url": entry.get("alternate", [{}])[0].get("href", ""),
                "display": f"{entry.get('title')} • {published_str}",
            }
        )
    return items


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
