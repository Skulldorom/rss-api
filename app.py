import requests
from flask import Flask, jsonify

app = Flask(__name__)

FRESHRSS_URL = "http://your-freshrss-host/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list"
FRESHRSS_TOKEN = "your-access-token"  # Set if needed


@app.route("/freshrss/unread")
def freshrss_unread():
    params = {
        "xt": "user/-/state/com.google/read",
        "output": "json",
        "n": 10,
        "ct": FRESHRSS_TOKEN,
    }
    r = requests.get(FRESHRSS_URL, params=params)
    # If authentication or special headers are needed, add `headers={...}`
    r.raise_for_status()
    raw = r.json()

    # Format for Homepage customapi display
    # Example: Show title, feed title, published
    items = []
    for entry in raw.get("items", []):
        items.append(
            {
                "title": entry.get("title"),
                "feed": entry.get("origin", {}).get("title"),
                "published": entry.get("published"),  # You can format as needed
                "url": entry.get("alternate", [{}])[0].get("href", ""),
            }
        )
    return jsonify(items)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
