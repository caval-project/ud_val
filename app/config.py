import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def get_database_uri() -> str:
    """
    Reads DATABASE_URL from env.
    Fallback is a placeholder that users must replace (or set DATABASE_URL).
    """
    return os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/DBNAME"
    )


def load_verb_features_config() -> dict:
    """
    Config file is necessary for enabling the "Verbal Features filtering" logic.
    """
    config_path = os.getenv("VERB_CONFIG_PATH", str(BASE_DIR / "config.json"))
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
