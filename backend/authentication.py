import json
import logging
import os
from pathlib import Path

import requests

from firebase_admin_init import admin_auth  # importing this initializes the app once

logger = logging.getLogger(__name__)

# Web API key, needed only for the REST calls the Admin SDK can't do
firebase_path = (
    "/etc/secrets/firebase.json"
    if os.path.exists("/etc/secrets/firebase.json")
    else Path(__file__).with_name("firebase.json")
)

with open(firebase_path) as f:
    data = json.load(f)
    FIREBASE_API_KEY = data["apiKey"]
    _IDENTITY_URL = data["identityURL"]

del data, f

def _identity_request(endpoint, payload):
    resp = requests.post(
        f"{_IDENTITY_URL}/{endpoint}",
        params={"key": FIREBASE_API_KEY},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def login_account(email, password):
    try:
        login = _identity_request(
            "accounts:signInWithPassword",
            {"email": email, "password": str(password), "returnSecureToken": True},
        )
        return {"id": login["localId"], "token": login["idToken"]}
    except Exception:
        logger.exception("Login failed")
        return None


def register_account(email, password):
    try:
        admin_auth.create_user(email=email, password=str(password))
        # Admin SDK doesn't return an ID token, so sign in to get one
        return login_account(email, password)
    except Exception:
        logger.exception("Register failed")
        return None

def forgot_password(email):
    try:
        _identity_request(
            "accounts:sendOobCode",
            {
                "requestType": "PASSWORD_RESET",
                "email": email,
            },
        )
    except Exception:
        logger.exception("Forgot password request failed")

    return True

def email_verify(id_token):
    try:
        _identity_request(
            "accounts:sendOobCode",
            {"requestType": "VERIFY_EMAIL", "idToken": id_token},
        )
        return "Email verification link sent successfully."
    except Exception:
        logger.exception("Email verification failed")
        return ""