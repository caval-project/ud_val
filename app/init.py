import os
from urllib.parse import urlsplit, urlunsplit, quote

from flask import Flask

from .extensions import db, migrate


def _normalize_database_url(raw_url: str) -> str:
    if not raw_url:
        raise ValueError("DATABASE_URL is empty or missing.")

    if raw_url.startswith("mysql://"):
        raw_url = "mysql+pymysql://" + raw_url[len("mysql://") :]

    parts = urlsplit(raw_url)

    if parts.username is None:
        return raw_url

    user = parts.username
    pw = parts.password or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or ""
    query = parts.query or ""
    fragment = parts.fragment or ""

    user_enc = quote(user, safe="")
    pw_enc = quote(pw, safe="")

    auth = f"{user_enc}:{pw_enc}@" if pw or user else f"{user_enc}@"
    netloc = f"{auth}{host}{port}"

    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def create_app() -> Flask:
    app = Flask(__name__)

    # ---- Database config ----
    default_local = "your_details"
    raw_db_url = os.environ.get("DATABASE_URL", default_local)
    app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_database_url(raw_db_url)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ---- Init extensions ----
    db.init_app(app)
    migrate.init_app(app, db)

    # ---- Register route modules ----
    # IMPORTANT: your routes files must expose Blueprint objects with these exact names:
    #   routes_language.py  -> bp_language
    #   routes_translit.py  -> bp_translit
    from .routes_language import bp_language
    from .routes_translit import bp_translit

    app.register_blueprint(bp_language)
    app.register_blueprint(bp_translit)

    return app
