"""Offline regressions for the SignBridge backend (FastAPI).

Nothing here touches the network, Firebase, MediaPipe or the hosted model:
external services are replaced with mocks while the real FastAPI routes, the
native ASGI WebSocket handler, the processing helpers and the auth/history
helpers run.

Run from the repository root: python -m unittest discover -s backend/tests -v
"""
import asyncio
import base64
import importlib.util
import itertools
import json
import os
import runpy
import sys
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch, sentinel

# Import real runtime dependencies BEFORE any test swaps modules in sys.modules.
# patch.dict(sys.modules) drops anything first imported inside the patch, so
# these must already be loaded.
import cv2  # noqa: F401
import firebase_admin  # noqa: F401
import requests as real_requests
import numpy
from PIL import Image
from dotenv import load_dotenv

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketState

# Load environment variables from .env file for local testing
load_dotenv()

BACKEND = Path(__file__).resolve().parents[1]

AUTH = {"Authorization": "Bearer valid"}
VALID_PASSWORD = "s3cr3tpw"


# Helpers shared across test classes

def _load_module(name, filename, fake_modules=None, env=None, env_remove=(), patches=()):
    """Execute BACKEND/<filename> as a fresh module with the given fakes."""
    spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
    module = importlib.util.module_from_spec(spec)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, fake_modules or {}))
        stack.enter_context(patch.dict("os.environ", env or {}))
        for key in env_remove:
            os.environ.pop(key, None)
        for patcher in patches:
            stack.enter_context(patcher)
        spec.loader.exec_module(module)
    return module


def _make_jpeg_b64(width: int = 8, height: int = 8) -> str:
    """Return a base64-encoded JPEG of a blank RGB image."""
    buf = BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _frame_msg(width: int = 8, height: int = 8) -> str:
    """Return a JSON WebSocket frame message."""
    return json.dumps({"frame": _make_jpeg_b64(width, height)})


def _done_future(result=None) -> Future:
    """A real, already-resolved concurrent.futures.Future.

    asyncio.wrap_future() (used internally by loop.run_in_executor) asserts
    its argument is an actual concurrent.futures.Future, so every mocked
    executor in the WebSocket tests must return one of these (or
    _failed_future / a manually driven Future) rather than a bare MagicMock.
    """
    future = Future()
    future.set_result(result)
    return future


def _failed_future(exc: Exception) -> Future:
    future = Future()
    future.set_exception(exc)
    return future


def _make_request(headers=None, client_host="127.0.0.1"):
    """A minimal Starlette Request, for calling get_user_id() directly."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 12345),
        "server": (client_host, 80),
        "query_string": b"",
        "scheme": "http",
    }
    return Request(scope)


# BackendRouteTests - real FastAPI routes in main.py

class BackendRouteTests(unittest.TestCase):
    def setUp(self):
        self.auth = MagicMock()
        self.auth.ExpiredIdTokenError = type("ExpiredIdTokenError", (Exception,), {})
        self.auth.RevokedIdTokenError = type("RevokedIdTokenError", (Exception,), {})
        self.auth.verify_id_token.return_value = {"uid": "alice"}
        self.authentication = MagicMock()
        self.history = MagicMock()
        self.history.retrieve_history.return_value = ["hello"]
        self.websocket_handler = MagicMock()
        self.websocket_handler.handle_websocket = AsyncMock()
        self.model = MagicMock()
        self.module = self._load_backend()
        self.client = TestClient(self.module.app)

    def _load_backend(self, env=None):
        modules = {
            "authentication": self.authentication,
            "history": self.history,
            "websocket_handler": self.websocket_handler,
            "model": self.model,
            "firebase_admin_init": MagicMock(admin_auth=self.auth),
        }
        # Keep the developer's .env / shell out of the app under test, and stop
        # the model warm-up thread from really starting.
        module = _load_module(
            "backend_under_test", "main.py", modules,
            env=env,
            env_remove=() if env and "ALLOWED_ORIGINS" in env else ("ALLOWED_ORIGINS",),
            patches=[patch("dotenv.load_dotenv"), patch("threading.Thread.start")],
        )
        self.addCleanup(module.executor.shutdown, wait=False)
        return module

    def _statuses(self, method, path, count, **kwargs):
        send = getattr(self.client, method)
        return [send(path, **kwargs).status_code for _ in range(count)]

    def _assert_rejected(self, response, error):
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], error)
        self.assertEqual(body["id"], "")
        self.assertEqual(body["token"], "")

    def _assert_invalid_body(self, response, expected_errors):
        """Assert a pydantic validation-error rejection.

        expected_errors is an ordered list of (field, message_substring) pairs
        matching request.validated_data's field declaration order; pydantic
        reports errors in that order.
        """
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Invalid request body")
        details = body["details"]
        self.assertEqual(len(details), len(expected_errors))
        for (field, substring), detail in zip(expected_errors, details):
            self.assertEqual(detail["field"], field)
            self.assertIn(substring, detail["message"])

    # TESTS - app wiring

    def test_model_api_created_with_top_1(self):
        self.model.ISLModelAPI.assert_called_once_with(top_k=1)

    def test_cors_uses_default_origins(self):
        self.assertEqual(
            self.module.allowed_origins,
            [
                "http://localhost:3000",
                "http://localhost:8080",
                "http://127.0.0.1:3000",
                "app://signbridge",
            ],
        )

    def test_cors_origins_come_from_environment(self):
        module = self._load_backend({"ALLOWED_ORIGINS": " https://a.example , ,https://b.example "})
        self.assertEqual(module.allowed_origins, ["https://a.example", "https://b.example"])

    def test_get_user_id_prefers_authenticated_user_over_ip(self):
        request = _make_request()
        self.assertEqual(self.module.get_user_id(request), "127.0.0.1")
        request.state.user_id = "alice"
        self.assertEqual(self.module.get_user_id(request), "alice")

    # TESTS - REST: index

    def test_index_returns_api_running_message(self):
        """GET / returns a JSON status message with version 2.0."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["message"], "SignBridge API is running")
        self.assertEqual(body["version"], "2.0")

    # TESTS - REST: /register

    def test_register_success(self):
        """POST /register with valid credentials returns id and token."""
        self.authentication.register_account.return_value = {"id": "uid1", "token": "tok1"}
        response = self.client.post(
            "/register", json={"email": "a@b.com", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": "uid1", "token": "tok1"})
        self.authentication.register_account.assert_called_once_with("a@b.com", VALID_PASSWORD)

    def test_register_validation_errors(self):
        """Each malformed payload is rejected before the backend is called."""
        cases = [
            ("empty body", {}, [("email", "Field required"), ("password", "Field required")]),
            ("missing email", {"password": VALID_PASSWORD}, [("email", "Field required")]),
            ("non-string email", {"email": 123, "password": VALID_PASSWORD},
             [("email", "valid string")]),
            ("malformed email", {"email": "not-an-email", "password": VALID_PASSWORD},
             [("email", "valid email address")]),
            ("missing password", {"email": "a@b.com"}, [("password", "Field required")]),
            ("non-string password", {"email": "a@b.com", "password": 123456},
             [("password", "valid string")]),
            ("short password", {"email": "a@b.com", "password": "12345"},
             [("password", "at least 6 characters")]),
        ]
        for label, payload, expected in cases:
            with self.subTest(label):
                self.module.limiter.reset()  # 5/min limit would otherwise trip
                self._assert_invalid_body(self.client.post("/register", json=payload), expected)
        self.authentication.register_account.assert_not_called()

    def test_register_accepts_short_single_char_tld_email(self):
        """pydantic's EmailStr treats a single-character TLD (e.g. 'a@b.c') as valid."""
        self.authentication.register_account.return_value = {"id": "u", "token": "t"}
        response = self.client.post(
            "/register", json={"email": "a@b.c", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.authentication.register_account.assert_called_once_with("a@b.c", VALID_PASSWORD)

    def test_register_no_json_returns_400(self):
        """POST /register with a non-JSON body is treated as an invalid (missing) body."""
        response = self.client.post("/register", content="not json", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid request body")
        self.authentication.register_account.assert_not_called()

    def test_register_backend_failure_returns_400(self):
        """POST /register returns 400 with an error message when the backend returns None."""
        self.authentication.register_account.return_value = None
        response = self.client.post(
            "/register", json={"email": "a@b.com", "password": VALID_PASSWORD}
        )
        self._assert_rejected(response, "Registration failed")

    def test_register_is_rate_limited(self):
        self.authentication.register_account.return_value = {"id": "u", "token": "t"}
        payload = {"email": "a@b.com", "password": VALID_PASSWORD}
        statuses = self._statuses("post", "/register", 6, json=payload)
        self.assertEqual(statuses, [200] * 5 + [429])
        response = self.client.post("/register", json=payload)
        self.assertEqual(
            response.json(),
            {"error": "Too many registration attempts. Please try again later."},
        )

    # TESTS - REST: /login

    def test_login_success(self):
        """POST /login with valid credentials returns id and token."""
        self.authentication.login_account.return_value = {"id": "uid2", "token": "tok2"}
        response = self.client.post(
            "/login", json={"email": "a@b.com", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": "uid2", "token": "tok2"})
        self.authentication.login_account.assert_called_once_with("a@b.com", VALID_PASSWORD)

    def test_login_validation_errors(self):
        cases = [
            ("empty body", {}, [("email", "Field required"), ("password", "Field required")]),
            ("missing email", {"password": VALID_PASSWORD}, [("email", "Field required")]),
            ("malformed email", {"email": "nope", "password": VALID_PASSWORD},
             [("email", "valid email address")]),
            ("missing password", {"email": "a@b.com"}, [("password", "Field required")]),
            ("short password", {"email": "a@b.com", "password": "pw"},
             [("password", "at least 6 characters")]),
        ]
        for label, payload, expected in cases:
            with self.subTest(label):
                self.module.limiter.reset()
                self._assert_invalid_body(self.client.post("/login", json=payload), expected)
        self.authentication.login_account.assert_not_called()

    def test_login_no_json_returns_400(self):
        response = self.client.post("/login", content="bad", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid request body")
        self.authentication.login_account.assert_not_called()

    def test_login_backend_failure_returns_400(self):
        """POST /login returns 400 when the backend returns None (wrong credentials)."""
        self.authentication.login_account.return_value = None
        response = self.client.post(
            "/login", json={"email": "a@b.com", "password": "wrongpass"}
        )
        self._assert_rejected(response, "Login failed")

    def test_login_is_rate_limited(self):
        self.authentication.login_account.return_value = {"id": "u", "token": "t"}
        payload = {"email": "a@b.com", "password": VALID_PASSWORD}
        self.assertEqual(self._statuses("post", "/login", 6, json=payload), [200] * 5 + [429])
        response = self.client.post("/login", json=payload)
        self.assertEqual(
            response.json(),
            {"error": "Too many login attempts. Please try again later."},
        )

    # TESTS - REST: /forgot-password

    def test_forgot_password_success(self):
        response = self.client.post("/forgot-password", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"success": "Password reset email has been sent."}
        )
        self.authentication.forgot_password.assert_called_once_with("a@b.com")

    def test_forgot_password_does_not_reveal_whether_the_account_exists(self):
        """The reply is identical whatever the helper returns."""
        for outcome in (True, False, None, ""):
            with self.subTest(outcome=outcome):
                self.module.limiter.reset()
                self.authentication.forgot_password.return_value = outcome
                response = self.client.post("/forgot-password", json={"email": "a@b.com"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(), {"success": "Password reset email has been sent."}
                )

    def test_forgot_password_validation_errors(self):
        cases = [
            ("empty body", {}, [("email", "Field required")]),
            ("non-string email", {"email": ["a@b.com"]}, [("email", "valid string")]),
            ("malformed email", {"email": "a@b"}, [("email", "valid email address")]),
        ]
        for label, payload, expected in cases:
            with self.subTest(label):
                self.module.limiter.reset()
                self._assert_invalid_body(
                    self.client.post("/forgot-password", json=payload), expected
                )
        self.authentication.forgot_password.assert_not_called()

    def test_forgot_password_no_json_returns_400(self):
        response = self.client.post("/forgot-password", content="bad", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 400)
        self.authentication.forgot_password.assert_not_called()

    def test_forgot_password_is_rate_limited(self):
        statuses = self._statuses("post", "/forgot-password", 4, json={"email": "a@b.com"})
        self.assertEqual(statuses, [200] * 3 + [429])

    # TESTS - REST: /logout

    def test_logout_success(self):
        response = self.client.post("/logout", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Logged out successfully"})
        self.authentication.logout_user.assert_called_once_with("alice")

    def test_logout_requires_auth(self):
        response = self.client.post("/logout")
        self.assertEqual(response.status_code, 401)
        self.authentication.logout_user.assert_not_called()

    def test_logout_failure_returns_500(self):
        self.authentication.logout_user.side_effect = RuntimeError("boom")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.post("/logout", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Logout failed"})

    def test_logout_is_rate_limited(self):
        statuses = self._statuses("post", "/logout", 11, headers=AUTH)
        self.assertEqual(statuses, [200] * 10 + [429])

    # TESTS - REST: /update-password

    def test_update_password_success(self):
        self.authentication.update_password.return_value = True
        response = self.client.post(
            "/update-password", json={"password": VALID_PASSWORD}, headers=AUTH
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": "Password has been updated."})
        self.authentication.update_password.assert_called_once_with("alice", VALID_PASSWORD)

    def test_update_password_requires_auth(self):
        response = self.client.post("/update-password", json={"password": VALID_PASSWORD})
        self.assertEqual(response.status_code, 401)
        self.authentication.update_password.assert_not_called()

    def test_update_password_validation_errors(self):
        cases = [
            ("missing password", {}, [("password", "Field required")]),
            ("non-string password", {"password": 123456}, [("password", "valid string")]),
            ("short password", {"password": "12345"}, [("password", "at least 6 characters")]),
        ]
        for label, payload, expected in cases:
            with self.subTest(label):
                self.module.limiter.reset()
                self._assert_invalid_body(
                    self.client.post("/update-password", json=payload, headers=AUTH), expected
                )
        self.authentication.update_password.assert_not_called()

    def test_update_password_backend_failure_returns_500(self):
        self.authentication.update_password.return_value = False
        response = self.client.post(
            "/update-password", json={"password": VALID_PASSWORD}, headers=AUTH
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Password update failed."})

    def test_update_password_is_rate_limited(self):
        self.authentication.update_password.return_value = True
        statuses = self._statuses(
            "post", "/update-password", 4, json={"password": VALID_PASSWORD}, headers=AUTH
        )
        self.assertEqual(statuses, [200] * 3 + [429])

    # TESTS - REST: /slt

    def test_slt_model_returns_deep_health(self):
        self.model.ISLModelAPI.return_value.deep_health.return_value = {
            "isl_model_status": "connected"
        }
        response = self.client.get("/slt/health/deep", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"isl_model_status": "connected"})

    def test_slt_model_requires_auth(self):
        response = self.client.get("/slt/health/deep")
        self.assertEqual(response.status_code, 401)

    def test_slt_model_failure_returns_503(self):
        self.model.ISLModelAPI.return_value.deep_health.side_effect = RuntimeError("down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.get("/slt/health/deep", headers=AUTH)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "Model not ready"})

    def test_slt_model_is_rate_limited(self):
        self.model.ISLModelAPI.return_value.deep_health.return_value = {}
        statuses = self._statuses("get", "/slt/health/deep", 6, headers=AUTH)
        self.assertEqual(statuses, [200] * 5 + [429])

    # TESTS - REST: authentication on protected routes

    def test_protected_routes_reject_missing_token(self):
        routes = [
            ("get", "/history"),
            ("post", "/history/store"),
            ("delete", "/history/abc"),
            ("delete", "/history/clear"),
            ("post", "/logout"),
            ("post", "/update-password"),
            ("get", "/slt/health/deep"),
        ]
        for method, path in routes:
            with self.subTest(route=f"{method.upper()} {path}"):
                kwargs = {}
                if path == "/history/store":
                    kwargs = {"json": {"translation": "x"}}
                elif path == "/update-password":
                    kwargs = {"json": {"password": VALID_PASSWORD}}
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Missing or invalid token"})
        self.auth.verify_id_token.assert_not_called()
        self.history.retrieve_history.assert_not_called()
        self.history.store_translation.assert_not_called()
        self.history.delete_translation.assert_not_called()
        self.history.delete_all_translations.assert_not_called()
        self.authentication.logout_user.assert_not_called()
        self.authentication.update_password.assert_not_called()

    def test_require_auth_rejects_wrong_scheme(self):
        response = self.client.get("/history", headers={"Authorization": "Basic abc123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Missing or invalid token"})
        self.auth.verify_id_token.assert_not_called()

    def test_require_auth_rejects_invalid_tokens(self):
        for error in (ValueError("bad token"), Exception("bad")):
            with self.subTest(error=repr(error)):
                self.auth.verify_id_token.side_effect = error
                response = self.client.get("/history", headers={"Authorization": "Bearer invalid"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Invalid token"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_reports_expired_tokens(self):
        self.auth.verify_id_token.side_effect = self.auth.ExpiredIdTokenError()
        response = self.client.get("/history", headers={"Authorization": "Bearer old"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Token expired"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_reports_revoked_tokens(self):
        self.auth.verify_id_token.side_effect = self.auth.RevokedIdTokenError()
        response = self.client.get("/history", headers={"Authorization": "Bearer old"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Token revoked"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_passes_bare_token_and_check_revoked_to_verifier(self):
        self.client.get("/history", headers={"Authorization": "Bearer abc.def.ghi"})
        self.auth.verify_id_token.assert_called_once_with("abc.def.ghi", check_revoked=True)

    # TESTS - REST: GET /history

    def test_history_uses_token_owner_even_with_another_id(self):
        response = self.client.get("/history?id=bob", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"history": ["hello"]})
        self.history.retrieve_history.assert_called_once_with("alice")

    def test_history_get_returns_items(self):
        items = [
            {"id": "-N2", "translation": "world", "timestamp": "2025-01-02T00:00:00"},
            {"id": "-N1", "translation": "hello", "timestamp": "2025-01-01T00:00:00"},
        ]
        self.history.retrieve_history.return_value = items
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"history": items})

    def test_history_get_empty_list(self):
        self.history.retrieve_history.return_value = []
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"history": []})

    def test_history_get_failure_returns_500(self):
        self.history.retrieve_history.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"history": "", "error": "Failed to retrieve history"},
        )

    def test_history_get_is_rate_limited_per_user(self):
        self.assertEqual(self._statuses("get", "/history", 21, headers=AUTH), [200] * 20 + [429])
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(
            response.json(),
            {"error": "Too many history requests. Please try again later."},
        )
        # A different signed-in user has their own bucket.
        self.auth.verify_id_token.return_value = {"uid": "bob"}
        self.assertEqual(self.client.get("/history", headers=AUTH).status_code, 200)

    def test_post_to_history_root_is_no_longer_supported(self):
        """Writes moved to POST /history/store."""
        response = self.client.post("/history", json={"translation": "hello"}, headers=AUTH)
        self.assertEqual(response.status_code, 405)
        self.history.store_translation.assert_not_called()

    # TESTS - REST: POST /history/store

    def test_history_store_returns_created_item_for_token_owner(self):
        item = {"id": "-N1", "translation": "hello", "timestamp": "2025-01-01T00:00:00"}
        self.history.store_translation.return_value = item
        response = self.client.post(
            "/history/store", json={"translation": "hello"}, headers=AUTH
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), item)
        self.history.store_translation.assert_called_once_with("alice", "hello")

    def test_history_store_uses_uid_from_verified_token(self):
        self.auth.verify_id_token.return_value = {"uid": "carol"}
        self.history.store_translation.return_value = {"id": "x"}
        self.client.post("/history/store", json={"translation": "world"}, headers=AUTH)
        self.history.store_translation.assert_called_once_with("carol", "world")

    def test_history_store_missing_translation_returns_400(self):
        cases = [
            (
                {"id": "alice"},
                {
                    "error": "Invalid request body",
                    "details": [
                        {"field": "translation", "message": "Field required"},
                        {"field": "id", "message": "Extra inputs are not permitted"},
                    ],
                },
            ),
            (
                {},
                {
                    "error": "Invalid request body",
                    "details": [
                        {"field": "translation", "message": "Field required"},
                    ],
                },
            ),
        ]
        for payload, expected_body in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/history/store", json=payload, headers=AUTH)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), expected_body)
        self.history.store_translation.assert_not_called()

    def test_history_store_rejects_bodies_that_are_not_json(self):
        """A body that fails to parse as JSON is rejected regardless of Content-Type."""
        bad_json = self.client.post(
            "/history/store", content="{oops", headers={**AUTH, "Content-Type": "application/json"}
        )
        self.assertEqual(bad_json.status_code, 400)
        wrong_type = self.client.post(
            "/history/store", content="not json", headers={**AUTH, "Content-Type": "text/plain"}
        )
        self.assertEqual(wrong_type.status_code, 400)
        self.history.store_translation.assert_not_called()

    def test_history_store_failure_returns_500(self):
        self.history.store_translation.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.post(
                "/history/store", json={"translation": "hello"}, headers=AUTH
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Failed to store history"})

    # TESTS - REST: DELETE /history/<id> and /history/clear

    def test_history_delete_success_scoped_to_token_owner(self):
        self.history.delete_translation.return_value = True
        response = self.client.delete("/history/-N1", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Translation deleted"})
        self.history.delete_translation.assert_called_once_with("alice", "-N1")

    def test_history_delete_unknown_id_returns_404(self):
        self.history.delete_translation.return_value = False
        response = self.client.delete("/history/missing", headers=AUTH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Translation not found"})

    def test_history_delete_failure_returns_500(self):
        self.history.delete_translation.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.delete("/history/-N1", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Failed to delete history"})

    def test_history_clear_is_not_shadowed_by_delete_by_id(self):
        """DELETE /history/clear must reach clear_history, not delete_history('clear')."""
        self.history.delete_all_translations.return_value = True
        response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "History deleted"})
        self.history.delete_all_translations.assert_called_once_with("alice")
        self.history.delete_translation.assert_not_called()

    def test_history_clear_when_nothing_stored_returns_404(self):
        self.history.delete_all_translations.return_value = False
        response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "No history found"})

    def test_history_clear_failure_returns_500(self):
        self.history.delete_all_translations.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Failed to delete history"})

    # TESTS - WebSocket route wiring (behaviour lives in WebSocketHandlerTests)

    def test_ws_route_is_registered(self):
        ws_routes = [
            r
            for r in self.module.app.router.routes
            if isinstance(r, WebSocketRoute)
        ]

        self.assertEqual(
            [r.path for r in ws_routes],
            ["/slt/ws", "/slp/ws"],
        )


# WebSocket-specific unit tests live in tests/test_regression_websocket.py
