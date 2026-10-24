"""MapQuest Flask application factory and HTML entry point."""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

from routes.api import api

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return render_template("index.html", mapbox_token=os.getenv("MAPBOX_ACCESS_TOKEN", "").strip())

    @app.errorhandler(413)
    def handle_large_request(_error):
        return jsonify({"error": "Request is too large."}), 413

    @app.errorhandler(500)
    def handle_server_error(_error):
        app.logger.exception("Unexpected application error")
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
