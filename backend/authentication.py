from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError

import json
import logging
import os
from pathlib import Path

import requests

from firebase_admin_init import admin_auth  # Initializes Firebase once


logger = logging.getLogger(__name__)


# Firebase configuration

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


# Firebase Identity Toolkit REST API

def _identity_request(endpoint, payload):
    try:
        response = requests.post(
            f"{_IDENTITY_URL}/{endpoint}",
            params={"key": FIREBASE_API_KEY},
            json=payload,
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException:
        logger.exception(
            "Firebase Identity API request failed: %s",
            endpoint,
        )
        return None

    except ValueError:
        logger.exception(
            "Firebase Identity API returned invalid JSON: %s",
            endpoint,
        )
        return None


# Authentication

def login_account(email, password):
    try:
        login = _identity_request(
            "accounts:signInWithPassword",
            {
                "email": email,
                "password": str(password),
                "returnSecureToken": True,
            },
        )

        if not login:
            return None

        return {
            "id": login["localId"],
            "token": login["idToken"],
        }

    except (KeyError, TypeError):
        logger.exception("Invalid response received during login")
        return None

    except Exception:
        logger.exception("Login failed")
        return None


def logout_user(user_id):
    try:
        admin_auth.revoke_refresh_tokens(user_id)
        logger.info("Logout successful for user")
    except Exception:
        logger.exception("Logout failed for user")
        return None
    return True


def register_account(email, password):
    try:
        admin_auth.create_user(
            email=email,
            password=str(password),
        )

        # Admin SDK does not return an ID token.
        # Sign in through the Identity Toolkit API to obtain one.
        return login_account(email, password)

    except auth.EmailAlreadyExistsError:
        logger.warning("Registration attempted for existing email")
        return None

    except FirebaseError:
        logger.exception("Firebase error during registration")
        return None

    except Exception:
        logger.exception("Registration failed")
        return None


# Password management

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
        # Deliberately do not expose whether the account exists.
        logger.exception("Forgot-password request failed")

    # Always report success to the caller.
    return True


def update_password(user_id, password):
    try:
        admin_auth.update_user(
            user_id,
            password=str(password),
        )

    except auth.UserNotFoundError:
        logger.warning(
            "Password update attempted for nonexistent user"
        )
        return False

    except auth.InvalidArgumentError:
        logger.warning(
            "Invalid password update request"
        )
        return False

    except FirebaseError:
        logger.exception(
            "Firebase error while updating password"
        )
        return False

    except Exception:
        logger.exception(
            "Unexpected error while updating password"
        )
        return False

    # Password has already been changed at this point.
    try:
        admin_auth.revoke_refresh_tokens(user_id)

    except FirebaseError:
        logger.exception(
            "Password changed but refresh-token revocation failed"
        )
        return False

    except Exception:
        logger.exception(
            "Password changed but refresh-token revocation failed"
        )
        return False

    return True


# Email verification

def email_verify(id_token):
    try:
        result = _identity_request(
            "accounts:sendOobCode",
            {
                "requestType": "VERIFY_EMAIL",
                "idToken": id_token,
            },
        )

        return True

    except Exception:
        logger.exception("Email verification failed")
        return False