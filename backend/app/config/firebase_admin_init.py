import json
from pathlib import Path

import firebase_admin
from firebase_admin import auth as admin_auth
from firebase_admin import credentials


# Project root:
# backend/
# ├── firebase.json
# ├── firebase-admin.json
# └── app/
#     └── config/
#         └── firebase_admin_init.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]

FIREBASE_CONFIG_PATH = PROJECT_ROOT / "firebase.json"
FIREBASE_ADMIN_PATH = PROJECT_ROOT / "firebase-admin.json"


with FIREBASE_CONFIG_PATH.open(encoding="utf-8") as f:
    _firebase_config = json.load(f)

_database_url = _firebase_config["databaseURL"]


try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(
        credentials.Certificate(str(FIREBASE_ADMIN_PATH)),
        {
            "databaseURL": _database_url,
        },
    )