import json
import logging
import os
import pyrebase

logger = logging.getLogger(__name__)

firebase_path = (
    "/etc/secrets/firebase.json"
    if os.path.exists("/etc/secrets/firebase.json")
    else "firebase.json"
)

with open(firebase_path) as f:
    firebaseConfig = json.load(f)

firebase = pyrebase.initialize_app(firebaseConfig)
auth     = firebase.auth()


def register_account(email, password):
    try:
        user = auth.create_user_with_email_and_password(email, password)
        return {"id": user["localId"], "token": user["idToken"]}
    except Exception:
        logger.exception("Register failed")
        return None


def login_account(email, password):
    try:
        login = auth.sign_in_with_email_and_password(email, password)
        return {"id": login["localId"], "token": login["idToken"]}
    except Exception:
        logger.exception("Login failed")
        return None


def forgot_password(email):
    try:
        auth.send_password_reset_email(email)
        return "Password reset email sent successfully."
    except Exception:
        logger.exception("Forgot password failed")
        return ""


def email_verify(id_token):
    try:
        auth.send_email_verification(id_token)
        return "Email verification link sent successfully."
    except Exception:
        logger.exception("Email verification failed")
        return ""