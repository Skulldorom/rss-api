import os
from flask import Flask, jsonify
import requests
from dotenv import load_dotenv
import humanize
from datetime import datetime, timezone

load_dotenv()

FRESHRSS_HOST = os.environ["FRESHRSS_HOST"]
FRESHRSS_USERNAME = os.environ["FRESHRSS_USER"]
FRESHRSS_PASSWORD = os.environ["FRESHRSS_PASS"]

app = Flask(__name__)

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
    res = requests.post(login_url, data=payload)
    if res.status_code != 200:
        raise Exception("FreshRSS login failed: {}".format(res.text))
    # Find and extract 'Auth=' line
    for line in res.text.splitlines():
        if line.startswith("Auth="):
            AUTH_TOKEN = line.replace("Auth=", "").strip()
            return AUTH_TOKEN
    raise Exception("Auth token not found in FreshRSS response")


@app.route("/freshrss/unread")
def freshrss_unread():
    token = get_greader_token()
    headers = {"Authorization": f"GoogleLogin auth={token}"}
    params = {
        "xt": "user/-/state/com.google/read",
        "output": "json",
        "n": 10,
    }
    # Using the same host as before but with the right endpoint
    url = f"{FRESHRSS_HOST}/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list"
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    raw = r.json()
    items = []

    now = datetime.now(timezone.utc)

    for entry in raw.get("items", []):
        published_ts = entry.get("published")
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
    return jsonify(items)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
