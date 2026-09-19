# main.py
"""
SignBridge Backend - Flask + WebSocket Server
============================================
Endpoints:
  POST /register       -> Firebase user registration
  POST /login          -> Firebase user login
  POST /forgot-password -> Send password reset email
  GET  /history        -> Retrieve translation history
  POST /history        -> Store a translation
  WS   /ws             -> Real-time sign detection
"""
import os
import base64
import gc
import json
import logging
import time
from collections import deque
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

import cv2
import numpy as np

import concurrent.futures
from flask import Flask, request
from flask_cors import CORS
from flask_sock import Sock
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded

import re


def get_user_id():
    return getattr(request, "user_id", get_remote_address())


#  App setup 
from authentication import register_account, login_account, forgot_password
from history import retrieve_history, store_translation, delete_translation, delete_all_translations
from websocket_handler import handle_websocket
from model import ISLModelAPI
from firebase_admin_init import admin_auth
from functools import wraps
from flask import request, jsonify



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app  = Flask(__name__)
limiter = Limiter(
    get_user_id,
    app=app,
    default_limits=["100 per minute", "5000 per day"]
)
allowed_origins_str = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000,app://signbridge"
)
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)
sock = Sock(app)

executor  = concurrent.futures.ThreadPoolExecutor(max_workers=2)
model_api = ISLModelAPI(top_k=1)

CLIP_LENGTH = 16
FRAME_DELAY = 0.08
RESIZE_DIM  = 224

# Wake up HF Space in background at startup
import threading
threading.Thread(target=model_api.check_health, daemon=True).start()


_SAVE_TEST_VIDEOS = os.environ.get("SAVE_TEST_VIDEOS", "0") == "1"


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid token"}), 401
        try:
            decoded = admin_auth.verify_id_token(auth_header.split(" ", 1)[1])
        except admin_auth.ExpiredIdTokenError:
            return jsonify({"error": "Token expired"}), 401
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        request.user_id = decoded["uid"]
        return f(*args, **kwargs)
    return wrapper


@app.errorhandler(RateLimitExceeded)
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": str(e.description)
    }), 429



def check_type(check_password=True):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            account = request.get_json(silent=True)

            if not account or "email" not in account:
                return jsonify({
                    "id": "",
                    "token": "",
                    "error": "Missing email"
                }), 400

            if not isinstance(account["email"], str):
                return jsonify({
                    "id": "",
                    "token": "",
                    "error": "Email must be a string"
                }), 400

            if not re.match(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                account["email"]
            ):
                return jsonify({
                    "id": "",
                    "token": "",
                    "error": "Invalid email"
                }), 400

            if check_password:
                if "password" not in account:
                    return jsonify({
                        "id": "",
                        "token": "",
                        "error": "Missing password"
                    }), 400

                if not isinstance(account["password"], str):
                    return jsonify({
                        "id": "",
                        "token": "",
                        "error": "Password must be a string"
                    }), 400

                if len(account["password"]) < 6:
                    return jsonify({
                        "id": "",
                        "token": "",
                        "error": "Password must be at least 6 characters long"
                    }), 400

            return f(*args, **kwargs)

        return wrapper

    return decorator


#  REST routes 
@app.route("/")
def index():
    return json.dumps({"message": "SignBridge API is running", "version": "2.0"})




@app.route("/register", methods=["POST"])
@limiter.limit(
    "5 per minute", 
    error_message="Too many registration attempts. Please try again later."
)
@check_type()
def register():
    account = request.get_json(silent=True)

    res = register_account(account["email"], account["password"])

    if not res:
        return json.dumps({
            "id": "", 
            "token": "",
            "error": "Registration failed"
            }), 400

    logger.info(f"Register: {account['email']} -> id={res['id']}")
    return json.dumps(res), 200


@app.route("/login", methods=["POST"])
@limiter.limit(
    "5 per minute", 
    error_message="Too many login attempts. Please try again later."
)
@check_type()
def login():
    account = request.get_json(silent=True)

    res = login_account(account["email"], account["password"])
    if not res:
        return json.dumps({
            "id": "", 
            "token": "",
            "error": "Login failed"
            }), 400
    logger.info(f"Login: {account['email']} -> id={res['id']}")
    return json.dumps(res), 200



@app.route("/forgot-password", methods=["POST"])
@limiter.limit(
    "3 per minute", 
    error_message="Too many forgot password attempts. Please try again later."
)
@check_type(check_password=False)
def forgot_pwd():
    account = request.get_json(silent=True)

    forgot_password(account["email"])
    
    logger.info("Password reset request received")

    return jsonify({
        "success": "Password reset email has been sent."
    }), 200




@app.route("/history", methods=["GET"])
@require_auth
@limiter.limit(
    "20 per minute", 
    error_message="Too many history requests. Please try again later."
)
def get_history():
    user_id = request.user_id

    try:
        return json.dumps({
            "history": retrieve_history(user_id)
        }), 200

    except Exception:
        logger.exception(
            "Failed to retrieve history",
        )
        return json.dumps({
            "history": "",
            "error": "Failed to retrieve history"
        }), 500



@app.route("/history/store", methods=["POST"])
@require_auth
@limiter.limit(
    "50 per minute", 
    error_message="Too many store requests. Please try again later."
)
def store_history():
    user_id = request.user_id

    data = request.get_json()

    if not data or "translation" not in data:
        return jsonify({"error": "Missing translation"}), 400

    try:
        item = store_translation(
            user_id,
            data["translation"]
        )

        return jsonify(item), 201

    except Exception:
        logger.exception(
            "Failed to store history"
        )
        return jsonify({
            "error": "Failed to store history"
        }), 500



@app.route("/history/<translation_id>", methods=["DELETE"])
@require_auth
@limiter.limit(
    "50 per minute", 
    error_message="Too many delete history requests. Please try again later."
)
def delete_history(translation_id):
    user_id = request.user_id

    try:
        deleted = delete_translation(
            user_id,
            translation_id
        )

        if not deleted:
            return jsonify({
                "error": "Translation not found"
            }), 404

        return jsonify({
            "message": "Translation deleted"
        }), 200

    except Exception:
        logger.exception(
            "Failed to delete history"
        )
        return jsonify({
            "error": "Failed to delete history"
        }), 500



@app.route("/history/clear", methods=["DELETE"])
@require_auth
@limiter.limit(
    "10 per minute", 
    error_message="Too many clear requests. Please try again later."
)
def clear_history():
    user_id = request.user_id
    try:
        deleted = delete_all_translations(user_id)
        if not deleted:
            return jsonify({
                "error": "No history found"
            }), 404
        return jsonify({
            "message": "History deleted"
        }), 200
    except Exception:
        logger.exception(
            "Failed to delete history"
        )
        return jsonify({
            "error": "Failed to delete history"
        }), 500



#  WebSocket Route
#  for live translations
@sock.route("/ws")
def websocket_translate(ws):
    handle_websocket(ws, model_api, executor)


#  Entry point 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*60}")
    print(f"  SignBridge Backend  ->  http://0.0.0.0:{port}/")
    print(f"  WebSocket         ->  ws://0.0.0.0:{port}/ws")
    print(f"  Android emulator  ->  use 10.0.2.2 instead of localhost")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, threaded=True)
