from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/releases')
def releases():
    data = [
        {"name": "homepage", "version": "v1.2.0", "age": "14 hr"},
        {"name": "audiobookshelf", "version": "v2.21.0", "age": "1 days"},
        {"name": "open-webui", "version": "v0.6.5", "age": "2 wk"},
        {"name": "dockge", "version": "1.5.0", "age": "4 wk"},
        {"name": "paperless-ai", "version": "v2.7.6", "age": "1 mo"}
    ]
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)