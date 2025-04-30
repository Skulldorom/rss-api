import os
from flask import Flask, jsonify
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

FRESHRSS_URL = os.environ["FRESHRSS_URL"]
FRESHRSS_USER = os.environ["FRESHRSS_USER"]
FRESHRSS_PASS = os.environ["FRESHRSS_PASS"]


@app.route("/freshrss/unread")
def freshrss_unread():
    params = {"xt": "user/-/state/com.google/read", "output": "json", "n": 10}
    auth = (FRESHRSS_USER, FRESHRSS_PASS)
    r = requests.get(
        FRESHRSS_URL
        + "/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
        params=params,
        auth=auth,
    )  # HTTP Basic Auth
    r.raise_for_status()
    raw = r.json()
    items = []
    for entry in raw.get("items", []):
        items.append(
            {
                "title": entry.get("title"),
                "feed": entry.get("origin", {}).get("title"),
                "published": entry.get("published"),
                "url": entry.get("alternate", [{}])[0].get("href", ""),
            }
        )
    return jsonify(items)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
