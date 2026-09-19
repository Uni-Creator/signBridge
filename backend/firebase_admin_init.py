import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, auth as admin_auth


def _secret_path(name):
    secret = f"/etc/secrets/{name}"
    return secret if os.path.exists(secret) else str(Path(__file__).with_name(name))


with open(_secret_path("firebase.json")) as f:
    _database_url = json.load(f)["databaseURL"]

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(
        credentials.Certificate(_secret_path("firebase-admin.json")),
        {"databaseURL": _database_url},
    )