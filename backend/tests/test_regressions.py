"""Offline regressions for the SignBridge backend.

Nothing here touches the network, Firebase, MediaPipe or the hosted model:
external services are replaced with mocks while the real Flask routes, the
WebSocket handler, the processing helpers and the auth/history helpers run.

Run from the repository root: python -m unittest discover -s backend/tests -v
"""
from PIL import ImageCms
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
from contextlib import ExitStack, contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch, sentinel

# Import real runtime dependencies BEFORE any test swaps modules in sys.modules.
# patch.dict(sys.modules) drops anything first imported inside the patch, so
# these must already be loaded.
import cv2  # noqa: F401
import flask
import firebase_admin
import flask_limiter  # noqa: F401
import flask_limiter.errors  # noqa: F401
import flask_limiter.util  # noqa: F401
import requests as real_requests
import numpy
from PIL import Image
from dotenv import load_dotenv

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
    future = Future()
    future.set_result(result)
    return future


def _failed_future(exc: Exception) -> Future:
    future = Future()
    future.set_exception(exc)
    return future


# BackendRouteTests - real Flask routes in main.py

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
        self.model = MagicMock()
        self.flask_cors = MagicMock()
        self.flask_sock = MagicMock()
        self.flask_sock.Sock.return_value.route.side_effect = lambda path: lambda f: f
        self.module = self._load_backend()
        self.client = self.module.app.test_client()

    def _load_backend(self, env=None):
        modules = {
            "authentication": self.authentication,
            "history": self.history,
            "websocket_handler": self.websocket_handler,
            "model": self.model,
            "firebase_admin_init": MagicMock(admin_auth=self.auth),
            "cv2": MagicMock(),
            "flask_cors": self.flask_cors,
            "flask_sock": self.flask_sock,
        }
        # Keep the developer's .env / shell out of the app under test, and stop
        # the model warm-up thread from really starting.
        module = _load_module(
            "backend_under_test", "main.py", modules,
            env=env,
            env_remove=() if env and "ALLOWED_ORIGINS" in env else ("ALLOWED_ORIGINS",),
            patches=[patch("dotenv.load_dotenv"), patch("threading.Thread.start")],
        )
        self.addCleanup(module.executor.shutdown, wait=True)
        return module

    def _statuses(self, method, path, count, **kwargs):
        send = getattr(self.client, method)
        return [send(path, **kwargs).status_code for _ in range(count)]

    def _assert_rejected(self, response, error):
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertEqual(body["error"], error)
        self.assertEqual(body["id"], "")
        self.assertEqual(body["token"], "")

    def _assert_invalid_body(self, response, expected_errors):
        """Assert a pydantic validate_body() rejection.

        expected_errors is an ordered list of (field, message_substring) pairs
        matching request.validated_data's field declaration order; pydantic
        reports errors in that order.
        """
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
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
        args, kwargs = self.flask_cors.CORS.call_args
        self.assertIs(args[0], self.module.app)
        self.assertEqual(
            kwargs["resources"],
            {r"/*": {"origins": [
                "http://localhost:3000",
                "http://localhost:8080",
                "http://127.0.0.1:3000",
                "app://signbridge",
            ]}},
        )
        self.assertTrue(kwargs["supports_credentials"])

    def test_cors_origins_come_from_environment(self):
        self.flask_cors.CORS.reset_mock()
        self._load_backend({"ALLOWED_ORIGINS": " https://a.example , ,https://b.example "})
        kwargs = self.flask_cors.CORS.call_args[1]
        self.assertEqual(
            kwargs["resources"], {r"/*": {"origins": ["https://a.example", "https://b.example"]}}
        )

    def test_get_user_id_prefers_authenticated_user_over_ip(self):
        with self.module.app.test_request_context("/"):
            self.assertEqual(self.module.get_user_id(), "127.0.0.1")
            flask.request.user_id = "alice"
            self.assertEqual(self.module.get_user_id(), "alice")

    # TESTS - REST: index

    def test_index_returns_api_running_message(self):
        """GET / returns a JSON status message with version 2.0."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
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
        self.assertEqual(json.loads(response.data), {"id": "uid1", "token": "tok1"})
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
        response = self.client.post("/register", data="not json", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.data)["error"], "Invalid request body")
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
            json.loads(response.data),
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
        self.assertEqual(json.loads(response.data), {"id": "uid2", "token": "tok2"})
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
        response = self.client.post("/login", data="bad", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.data)["error"], "Invalid request body")
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
            json.loads(response.data),
            {"error": "Too many login attempts. Please try again later."},
        )

    # TESTS - REST: /forgot-password

    def test_forgot_password_success(self):
        response = self.client.post("/forgot-password", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.data), {"success": "Password reset email has been sent."}
        )
        self.authentication.forgot_password.assert_called_once_with("a@b.com")

    def test_forgot_password_does_not_require_a_password(self):
        response = self.client.post("/forgot-password", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 200)

    def test_forgot_password_does_not_reveal_whether_the_account_exists(self):
        """The reply is identical whatever the helper returns."""
        for outcome in (True, False, None, ""):
            with self.subTest(outcome=outcome):
                self.module.limiter.reset()
                self.authentication.forgot_password.return_value = outcome
                response = self.client.post("/forgot-password", json={"email": "a@b.com"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    json.loads(response.data), {"success": "Password reset email has been sent."}
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
        response = self.client.post("/forgot-password", data="bad", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.authentication.forgot_password.assert_not_called()

    def test_forgot_password_is_rate_limited(self):
        statuses = self._statuses("post", "/forgot-password", 4, json={"email": "a@b.com"})
        self.assertEqual(statuses, [200] * 3 + [429])

    # TESTS - REST: authentication on protected routes

    def test_protected_routes_reject_missing_token(self):
        routes = [
            ("get", "/history"),
            ("post", "/history/store"),
            ("delete", "/history/abc"),
            ("delete", "/history/clear"),
        ]
        for method, path in routes:
            with self.subTest(route=f"{method.upper()} {path}"):
                kwargs = {"json": {"translation": "x"}} if method == "post" else {}
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(json.loads(response.data), {"error": "Missing or invalid token"})
        self.auth.verify_id_token.assert_not_called()
        self.history.retrieve_history.assert_not_called()
        self.history.store_translation.assert_not_called()
        self.history.delete_translation.assert_not_called()
        self.history.delete_all_translations.assert_not_called()

    def test_require_auth_rejects_wrong_scheme(self):
        response = self.client.get("/history", headers={"Authorization": "Basic abc123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.data), {"error": "Missing or invalid token"})
        self.auth.verify_id_token.assert_not_called()

    def test_require_auth_rejects_invalid_tokens(self):
        for error in (ValueError("bad token"), Exception("bad")):
            with self.subTest(error=repr(error)):
                self.auth.verify_id_token.side_effect = error
                response = self.client.get("/history", headers={"Authorization": "Bearer invalid"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(json.loads(response.data), {"error": "Invalid token"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_reports_expired_tokens(self):
        self.auth.verify_id_token.side_effect = self.auth.ExpiredIdTokenError()
        response = self.client.get("/history", headers={"Authorization": "Bearer old"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.data), {"error": "Token expired"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_reports_revoked_tokens(self):
        self.auth.verify_id_token.side_effect = self.auth.RevokedIdTokenError()
        response = self.client.get("/history", headers={"Authorization": "Bearer old"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.data), {"error": "Token revoked"})
        self.history.retrieve_history.assert_not_called()

    def test_require_auth_passes_bare_token_and_check_revoked_to_verifier(self):
        self.client.get("/history", headers={"Authorization": "Bearer abc.def.ghi"})
        self.auth.verify_id_token.assert_called_once_with("abc.def.ghi", check_revoked=True)

    # TESTS - REST: GET /history

    def test_history_uses_token_owner_even_with_another_id(self):
        response = self.client.get("/history?id=bob", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"history": ["hello"]})
        self.history.retrieve_history.assert_called_once_with("alice")

    def test_history_get_returns_items(self):
        items = [
            {"id": "-N2", "translation": "world", "timestamp": "2025-01-02T00:00:00"},
            {"id": "-N1", "translation": "hello", "timestamp": "2025-01-01T00:00:00"},
        ]
        self.history.retrieve_history.return_value = items
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"history": items})

    def test_history_get_empty_list(self):
        self.history.retrieve_history.return_value = []
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"history": []})

    def test_history_get_failure_returns_500(self):
        self.history.retrieve_history.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.get("/history", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            json.loads(response.data),
            {"history": "", "error": "Failed to retrieve history"},
        )

    def test_history_get_is_rate_limited_per_user(self):
        self.assertEqual(self._statuses("get", "/history", 21, headers=AUTH), [200] * 20 + [429])
        response = self.client.get("/history", headers=AUTH)
        self.assertEqual(
            json.loads(response.data),
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
        self.assertEqual(json.loads(response.data), item)
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
                self.assertEqual(json.loads(response.data), expected_body)
        self.history.store_translation.assert_not_called()

    def test_history_store_rejects_bodies_that_are_not_json(self):
        """Malformed JSON gives 400; a wrong content type gives 415 on current Flask (400 on old)."""
        bad_json = self.client.post(
            "/history/store", data="{oops", content_type="application/json", headers=AUTH
        )
        self.assertEqual(bad_json.status_code, 400)
        wrong_type = self.client.post(
            "/history/store", data="not json", content_type="text/plain", headers=AUTH
        )
        self.assertIn(wrong_type.status_code, (400, 415))
        self.history.store_translation.assert_not_called()

    def test_history_store_failure_returns_500(self):
        self.history.store_translation.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.post(
                "/history/store", json={"translation": "hello"}, headers=AUTH
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.data), {"error": "Failed to store history"})

    # TESTS - REST: DELETE /history/<id> and /history/clear

    def test_history_delete_success_scoped_to_token_owner(self):
        self.history.delete_translation.return_value = True
        response = self.client.delete("/history/-N1", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"message": "Translation deleted"})
        self.history.delete_translation.assert_called_once_with("alice", "-N1")

    def test_history_delete_unknown_id_returns_404(self):
        self.history.delete_translation.return_value = False
        response = self.client.delete("/history/missing", headers=AUTH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.data), {"error": "Translation not found"})

    def test_history_delete_failure_returns_500(self):
        self.history.delete_translation.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.delete("/history/-N1", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.data), {"error": "Failed to delete history"})

    def test_history_clear_is_not_shadowed_by_delete_by_id(self):
        """DELETE /history/clear must reach clear_history, not delete_history('clear')."""
        self.history.delete_all_translations.return_value = True
        response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"message": "History deleted"})
        self.history.delete_all_translations.assert_called_once_with("alice")
        self.history.delete_translation.assert_not_called()

    def test_history_clear_when_nothing_stored_returns_404(self):
        self.history.delete_all_translations.return_value = False
        response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.data), {"error": "No history found"})

    def test_history_clear_failure_returns_500(self):
        self.history.delete_all_translations.side_effect = RuntimeError("db down")
        with self.assertLogs(self.module.logger, "ERROR"):
            response = self.client.delete("/history/clear", headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.data), {"error": "Failed to delete history"})

    # TESTS - WebSocket route wiring (behaviour lives in WebSocketHandlerTests)

    def test_ws_route_is_registered(self):
        self.flask_sock.Sock.return_value.route.assert_called_once_with("/ws")

    def test_ws_route_delegates_to_handler_with_shared_model_and_executor(self):
        ws = MagicMock()
        self.module.websocket_translate(ws)
        self.websocket_handler.handle_websocket.assert_called_once_with(
            ws, self.module.model_api, self.module.executor
        )


# WebSocket handler - shared fixture

class _HandlerTestCase(unittest.TestCase):
    """Loads websocket_handler.py with Firebase and processing replaced."""

    def setUp(self):
        self.auth = MagicMock()
        self.auth.verify_id_token.return_value = {"uid": "alice"}
        processing = MagicMock(MEDIAPIPE_OK=True)
        self.handler = _load_module(
            "websocket_handler_under_test", "websocket_handler.py",
            {
                "firebase_admin_init": MagicMock(admin_auth=self.auth),
                "websocket_processing": processing,
            },
        )
        self.process_frame = MagicMock(name="process_frame")
        self.run_inference = MagicMock(name="run_inference")
        self.handler.process_frame = self.process_frame
        self.handler.run_inference = self.run_inference
        self.model_api = MagicMock()
        self.model_api.check_health.return_value = True
        self.app = flask.Flask("handler-tests")

    def _run(self, ws, pool=None, landmarkers=(None, None), query="token=valid", ticks=None):
        """Run handle_websocket once and return the (mock) executor.

        `ticks` feeds time.monotonic(); by default every frame is a second
        apart so none is dropped by the frame-rate limiter. Only the handler's
        own `time` reference is faked, never the global one.
        """
        pool = pool if pool is not None else MagicMock()
        fake_time = MagicMock()
        fake_time.monotonic.side_effect = ticks if ticks is not None else itertools.count(1)
        self.handler.time = fake_time
        self.handler.build_landmarkers = MagicMock(return_value=landmarkers)
        with self.app.test_request_context(f"/ws?{query}"):
            self.handler.handle_websocket(ws, self.model_api, pool)
        return pool

    @staticmethod
    def _sent(ws):
        return [json.loads(c[0][0]) for c in ws.send.call_args_list]

    def _ws(self, *messages):
        ws = MagicMock()
        ws.receive.side_effect = list(messages) + [None]
        return ws

    def _pipeline_pool(self, inference_future):
        """Executor whose landmark jobs finish instantly and inference returns `inference_future`."""
        landmark_future = _done_future(Image.new("RGB", (32, 32)))

        def submit(fn, *args):
            return inference_future if fn is self.run_inference else landmark_future

        pool = MagicMock()
        pool.submit.side_effect = submit
        return pool

    def _inference_calls(self, pool):
        return [c for c in pool.submit.call_args_list if c[0][0] is self.run_inference]


# WebSocketHelperTests - module-level helpers in websocket_handler.py

class WebSocketHelperTests(_HandlerTestCase):
    def test_authenticate_returns_none_without_token(self):
        for token in ("", None):
            self.assertIsNone(self.handler.authenticate_websocket(token))
        self.auth.verify_id_token.assert_not_called()

    def test_authenticate_returns_decoded_token(self):
        self.assertEqual(self.handler.authenticate_websocket("valid"), {"uid": "alice"})
        self.auth.verify_id_token.assert_called_once_with("valid")

    def test_authenticate_returns_none_when_verification_fails(self):
        self.auth.verify_id_token.side_effect = ValueError("bad")
        self.assertIsNone(self.handler.authenticate_websocket("garbage"))

    def test_send_json_serialises_payload(self):
        ws = MagicMock()
        self.handler.send_json(ws, {"a": 1})
        ws.send.assert_called_once()
        self.assertEqual(json.loads(ws.send.call_args[0][0]), {"a": 1})

    def test_decode_frame_returns_rgb_image(self):
        image = self.handler.decode_frame(_frame_msg(8, 6))
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (8, 6))

    def test_decode_frame_converts_other_modes_to_rgb(self):
        buf = BytesIO()
        Image.new("RGBA", (4, 4)).save(buf, format="PNG")
        message = json.dumps({"frame": base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(self.handler.decode_frame(message).mode, "RGB")

    def test_decode_frame_rejects_bad_input(self):
        cases = [
            ("not json", "not json", "Invalid JSON message"),
            ("none", None, "Invalid JSON message"),
            ("no frame key", json.dumps({}), "Missing frame"),
            ("empty frame", json.dumps({"frame": ""}), "Missing frame"),
            ("bad base64", json.dumps({"frame": "!!!not_valid_base64!!!"}), "Invalid base64 frame"),
            ("not an image", json.dumps({"frame": base64.b64encode(b"hello").decode()}),
             "Invalid image"),
        ]
        for label, message, error in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, error):
                    self.handler.decode_frame(message)

    def test_config_message_switches_mode_and_acknowledges(self):
        for mode in ("frames", "video", "hybrid"):
            with self.subTest(mode=mode):
                ws, config = MagicMock(), {"mode": "frames"}
                message = json.dumps({"type": "config", "mode": mode})
                self.assertTrue(self.handler.handle_config_message(message, config, ws))
                self.assertEqual(config["mode"], mode)
                self.assertEqual(self._sent(ws), [{"status": "config_updated", "mode": mode}])

    def test_config_message_with_unknown_mode_is_consumed_but_ignored(self):
        ws, config = MagicMock(), {"mode": "frames"}
        message = json.dumps({"type": "config", "mode": "unknown_mode"})
        self.assertTrue(self.handler.handle_config_message(message, config, ws))
        self.assertEqual(config["mode"], "frames")
        ws.send.assert_not_called()

    def test_non_config_messages_are_not_consumed(self):
        ws, config = MagicMock(), {"mode": "frames"}
        for message in ("not json", _frame_msg(), json.dumps({"type": "ping"})):
            with self.subTest(message=message[:20]):
                self.assertFalse(self.handler.handle_config_message(message, config, ws))
        ws.send.assert_not_called()

    def test_send_inference_result_sends_label_and_confidence(self):
        ws = MagicMock()
        result = {"prediction": "hello", "confidence": 0.95, "total_latency_ms": 12}
        self.assertTrue(self.handler.send_inference_result(ws, result, "frames"))
        self.assertEqual(self._sent(ws), [{"label": "hello", "confidence": 0.95}])

    def test_send_inference_result_tolerates_bad_confidence(self):
        ws = MagicMock()
        result = {"prediction": "hello", "confidence": "high"}
        self.assertTrue(self.handler.send_inference_result(ws, result, "video"))
        self.assertEqual(self._sent(ws), [{"label": "hello", "confidence": 0.0}])

    def test_send_inference_result_skips_empty_and_missing_labels(self):
        for result in (None, {}, {"prediction": "", "confidence": 0.9}, {"confidence": 0.9}):
            with self.subTest(result=result):
                ws = MagicMock()
                self.assertFalse(self.handler.send_inference_result(ws, result, "frames"))
                ws.send.assert_not_called()

    def test_send_inference_result_logs_errors_and_sends_nothing(self):
        ws = MagicMock()
        with self.assertLogs(self.handler.logger, "ERROR"):
            sent = self.handler.send_inference_result(ws, {"error": "boom"}, "frames")
        self.assertFalse(sent)
        ws.send.assert_not_called()


# WebSocketHandlerTests - handle_websocket connection lifecycle

class WebSocketHandlerTests(_HandlerTestCase):
    # authentication

    def test_rejects_missing_token(self):
        ws = MagicMock()
        self._run(ws, query="")
        self.assertEqual(self._sent(ws), [{"error": "Unauthorized"}])
        ws.close.assert_called_once()
        ws.receive.assert_not_called()
        self.auth.verify_id_token.assert_not_called()

    def test_rejects_invalid_token(self):
        for error in (ValueError("bad"), Exception("expired")):
            with self.subTest(error=repr(error)):
                self.auth.verify_id_token.side_effect = error
                ws = MagicMock()
                self._run(ws, query="token=garbage")
                self.assertEqual(self._sent(ws), [{"error": "Unauthorized"}])
                ws.close.assert_called_once()
                ws.receive.assert_not_called()

    def test_unauthorized_close_errors_are_swallowed(self):
        ws = MagicMock()
        ws.close.side_effect = RuntimeError("already closed")
        self._run(ws, query="")  # must not raise
        ws.close.assert_called_once()

    def test_sends_connected_on_auth_success(self):
        ws = self._ws()
        self._run(ws)
        self.auth.verify_id_token.assert_called_once_with("valid")
        self.assertEqual(
            self._sent(ws)[0], {"status": "connected", "message": "Ready for frames"}
        )

    def test_receive_uses_30_second_timeout(self):
        ws = self._ws()
        self._run(ws)
        ws.receive.assert_called_with(timeout=30)

    # start-up notices

    def test_landmarks_disabled_notice_when_mediapipe_missing(self):
        self.handler.MEDIAPIPE_OK = False
        ws = self._ws()
        self._run(ws)
        info = [m for m in self._sent(ws) if m.get("status") == "info"]
        self.assertEqual(len(info), 1)
        self.assertIn("Landmarks disabled", info[0]["message"])

    def test_no_landmarks_notice_when_mediapipe_available(self):
        ws = self._ws()
        self._run(ws)
        self.assertFalse([m for m in self._sent(ws) if m.get("status") == "info"])

    def test_model_unhealthy_sends_warming_message(self):
        self.model_api.check_health.return_value = False
        ws = self._ws()
        self._run(ws)
        warming = [m for m in self._sent(ws) if m.get("status") == "api_warming"]
        self.assertEqual(len(warming), 1)

    def test_model_health_check_error_sends_warming_message(self):
        self.model_api.check_health.side_effect = ConnectionError("refused")
        ws = self._ws()
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(ws)
        self.assertTrue([m for m in self._sent(ws) if m.get("status") == "api_warming"])

    def test_model_healthy_sends_no_warming_message(self):
        ws = self._ws()
        self._run(ws)
        self.assertFalse([m for m in self._sent(ws) if m.get("status") == "api_warming"])

    # configuration

    def test_config_command_updates_mode(self):
        ws = self._ws(json.dumps({"type": "config", "mode": "video"}))
        pool = self._run(ws)
        acks = [m for m in self._sent(ws) if m.get("status") == "config_updated"]
        self.assertEqual(acks, [{"status": "config_updated", "mode": "video"}])
        pool.submit.assert_not_called()  # config messages are never treated as frames

    def test_hybrid_mode_is_accepted(self):
        ws = self._ws(json.dumps({"type": "config", "mode": "hybrid"}))
        self._run(ws)
        acks = [m for m in self._sent(ws) if m.get("status") == "config_updated"]
        self.assertEqual(acks[0]["mode"], "hybrid")

    def test_invalid_config_mode_is_not_acknowledged(self):
        ws = self._ws(json.dumps({"type": "config", "mode": "unknown_mode"}))
        pool = self._run(ws)
        self.assertFalse([m for m in self._sent(ws) if m.get("status") == "config_updated"])
        pool.submit.assert_not_called()

    # frame intake

    def test_invalid_frame_sends_error_and_continues(self):
        ws = self._ws(
            json.dumps({"frame": "!!!not_valid_base64!!!"}),
            "not json",
            json.dumps({"frame": base64.b64encode(b"hello").decode()}),
        )
        with self.assertLogs(self.handler.logger, "WARNING"):
            pool = self._run(ws)
        errors = [m for m in self._sent(ws) if "error" in m]
        self.assertEqual(errors, [{"error": "Invalid frame"}] * 3)
        pool.submit.assert_not_called()

    def test_empty_frame_field_is_reported_as_invalid(self):
        ws = self._ws(json.dumps({"frame": ""}))
        with self.assertLogs(self.handler.logger, "WARNING"):
            pool = self._run(ws)
        self.assertIn({"error": "Invalid frame"}, self._sent(ws))
        pool.submit.assert_not_called()

    def test_frames_faster_than_frame_delay_are_dropped(self):
        pool = MagicMock()
        pool.submit.return_value = _done_future(Image.new("RGB", (8, 8)))
        ws = self._ws(_frame_msg(), _frame_msg(), _frame_msg())
        # 2nd frame arrives 10 ms after the 1st (< FRAME_DELAY) and is skipped.
        self._run(ws, pool=pool, ticks=[1.0, 1.01, 1.2])
        self.assertEqual(pool.submit.call_count, 2)

    def test_frame_is_scheduled_for_landmark_processing(self):
        pose, hand = MagicMock(), MagicMock()
        pool = self._run(self._ws(_frame_msg(8, 6)), landmarkers=(pose, hand))
        fn, image, pose_arg, hand_arg, enabled = pool.submit.call_args[0]
        self.assertIs(fn, self.process_frame)
        self.assertEqual(image.size, (8, 6))
        self.assertIs(pose_arg, pose)
        self.assertIs(hand_arg, hand)
        self.assertTrue(enabled)

    def test_landmarks_flag_is_off_without_detectors(self):
        pool = self._run(self._ws(_frame_msg()), landmarkers=(None, None))
        self.assertFalse(pool.submit.call_args[0][4])

    def test_landmarks_flag_is_off_if_only_one_detector_exists(self):
        pool = self._run(self._ws(_frame_msg()), landmarkers=(MagicMock(), None))
        self.assertFalse(pool.submit.call_args[0][4])

    def test_slow_landmarks_keep_pending_job_and_collect_its_result(self):
        message = _frame_msg()
        first, second = Future(), Future()
        first.result = MagicMock(wraps=first.result)
        pool = MagicMock()
        pool.submit.side_effect = [first, second]
        pose, hand = MagicMock(), MagicMock()
        calls = 0

        def receive(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                # Frame 2 arrived while frame 1 was still being processed.
                self.assertEqual(pool.submit.call_count, 1)
                first.set_result(Image.new("RGB", (8, 8)))
            return message if calls <= 3 else None

        ws = MagicMock()
        ws.receive.side_effect = receive
        self._run(ws, pool=pool, landmarkers=(pose, hand))
        self.assertEqual(pool.submit.call_count, 2)
        first.result.assert_called_once()
        self.assertTrue(second.cancelled())
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    def test_landmark_failure_does_not_stop_the_stream(self):
        pool = MagicMock()
        pool.submit.return_value = _failed_future(RuntimeError("mediapipe crashed"))
        pose, hand = MagicMock(), MagicMock()
        ws = self._ws(_frame_msg(), _frame_msg(), _frame_msg())
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(ws, pool=pool, landmarkers=(pose, hand))
        self.assertEqual(ws.receive.call_count, 4)  # ran until the disconnect
        self.assertFalse([m for m in self._sent(ws) if "label" in m])
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    # inference

    def test_inference_result_sent_to_client(self):
        """16 buffered frames trigger inference; the label is echoed back."""
        inference = _done_future({"prediction": "hello", "confidence": 0.95})
        pool = self._pipeline_pool(inference)
        # Frame N collects the landmark job of frame N-1, so the 16th buffered
        # frame lands on message 17 (dispatch) and the result is sent on 18.
        ws = self._ws(*[_frame_msg()] * 18)
        self._run(ws, pool=pool)
        labels = [m for m in self._sent(ws) if "label" in m]
        self.assertEqual(labels, [{"label": "hello", "confidence": 0.95}])

    def test_inference_gets_16_resized_frames_and_default_mode(self):
        pool = self._pipeline_pool(Future())
        self._run(self._ws(*[_frame_msg()] * 17), pool=pool)
        calls = self._inference_calls(pool)
        self.assertEqual(len(calls), 1)
        _, frames, mode, model_api, save_videos = calls[0][0]
        self.assertEqual(len(frames), 16)
        self.assertTrue(all(f.size == (224, 224) for f in frames))
        self.assertEqual(mode, "frames")
        self.assertIs(model_api, self.model_api)
        self.assertIs(save_videos, self.handler.SAVE_TEST_VIDEOS)

    def test_inference_uses_the_configured_mode(self):
        pool = self._pipeline_pool(Future())
        ws = self._ws(json.dumps({"type": "config", "mode": "hybrid"}), *[_frame_msg()] * 17)
        self._run(ws, pool=pool)
        self.assertEqual(self._inference_calls(pool)[0][0][2], "hybrid")

    def test_no_inference_before_the_clip_is_full(self):
        pool = self._pipeline_pool(Future())
        self._run(self._ws(*[_frame_msg()] * 16), pool=pool)
        self.assertEqual(self._inference_calls(pool), [])

    def test_inference_error_is_logged_and_no_label_sent(self):
        pool = self._pipeline_pool(_done_future({"error": "model down"}))
        ws = self._ws(*[_frame_msg()] * 18)
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(ws, pool=pool)
        self.assertFalse([m for m in self._sent(ws) if "label" in m])

    def test_inference_without_label_sends_nothing(self):
        pool = self._pipeline_pool(_done_future({"prediction": "", "confidence": 0.1}))
        ws = self._ws(*[_frame_msg()] * 18)
        self._run(ws, pool=pool)
        self.assertFalse([m for m in self._sent(ws) if "label" in m])

    def test_inference_future_raising_does_not_crash_the_stream(self):
        pool = self._pipeline_pool(_failed_future(RuntimeError("worker died")))
        ws = self._ws(*[_frame_msg()] * 18)
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(ws, pool=pool)
        self.assertEqual(ws.receive.call_count, 19)

    # disconnect / cleanup

    def test_disconnect_cancels_pending_inference_future(self):
        pending_inference = Future()
        pool = self._pipeline_pool(pending_inference)
        self._run(self._ws(*[_frame_msg()] * 17), pool=pool)
        self.assertTrue(pending_inference.cancelled())

    def test_disconnect_waits_for_running_landmarks_before_closing_detectors(self):
        pose, hand = MagicMock(), MagicMock()
        pending = MagicMock()
        pending.cancel.return_value = False

        def finish():
            pose.close.assert_not_called()
            hand.close.assert_not_called()

        pending.result.side_effect = finish
        pool = MagicMock()
        pool.submit.return_value = pending
        self._run(self._ws(_frame_msg()), pool=pool, landmarkers=(pose, hand))
        pending.result.assert_called_once()
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    def test_disconnect_survives_failed_landmark_job_and_still_closes_detectors(self):
        pose, hand = MagicMock(), MagicMock()
        pending = MagicMock()
        pending.cancel.return_value = False
        pending.result.side_effect = RuntimeError("landmarks failed")
        pool = MagicMock()
        pool.submit.return_value = pending
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(self._ws(_frame_msg()), pool=pool, landmarkers=(pose, hand))
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    def test_detector_close_errors_do_not_leak(self):
        pose, hand = MagicMock(), MagicMock()
        pose.close.side_effect = RuntimeError("pose close failed")
        with self.assertLogs(self.handler.logger, "ERROR"):
            self._run(self._ws(), landmarkers=(pose, hand))
        hand.close.assert_called_once()  # still closed after the pose failure

    def test_receive_error_ends_session_and_cleans_up(self):
        pose, hand = MagicMock(), MagicMock()
        ws = MagicMock()
        ws.receive.side_effect = RuntimeError("socket reset")
        with self.assertLogs(self.handler.logger, "WARNING"):
            self._run(ws, landmarkers=(pose, hand))
        pose.close.assert_called_once()
        hand.close.assert_called_once()


# WebSocketProcessingTests - websocket_processing.py

class WebSocketProcessingTests(unittest.TestCase):
    def setUp(self):
        self.proc = _load_module(
            "websocket_processing_under_test", "websocket_processing.py",
            env={"ENABLE_MEDIAPIPE": "0"},
        )

    @staticmethod
    def _landmark(x=0.1, y=0.2, z=0.0):
        return SimpleNamespace(x=x, y=y, z=z)

    @staticmethod
    def _fake_mediapipe():
        mp = MagicMock()

        mp.ImageFormat.SRGB = sentinel.srgb

        modules = {
            "mediapipe": mp,
        }

        return modules, mp

    # module start-up

    def test_mediapipe_disabled_by_environment(self):
        self.assertFalse(self.proc.MEDIAPIPE_OK)
        self.assertIsNone(self.proc.mp_drawing)

    def test_mediapipe_import_failure_disables_landmarks(self):
        with self.assertLogs("websocket_processing_no_mp", "WARNING"):
            proc = _load_module(
                "websocket_processing_no_mp", "websocket_processing.py",
                fake_modules={"mediapipe": None},  # makes `import mediapipe` fail
                env={"ENABLE_MEDIAPIPE": "1"},
            )
        self.assertFalse(proc.MEDIAPIPE_OK)

    def test_mediapipe_enabled_when_import_succeeds(self):
        mp = MagicMock()
        tasks = MagicMock()
        python = MagicMock()
        vision = MagicMock()
        components = MagicMock()
        containers = MagicMock()
        landmark = MagicMock()
        drawing_styles = MagicMock()
        drawing_utils = MagicMock()

        tasks.python = python
        python.vision = vision
        python.components = components
        components.containers = containers
        containers.landmark = landmark

        vision.PoseLandmarksConnections.POSE_LANDMARKS = sentinel.pose_connections
        vision.HandLandmarksConnections.HAND_CONNECTIONS = sentinel.hand_connections
        vision.drawing_styles = drawing_styles
        vision.drawing_utils = drawing_utils

        drawing_styles.get_default_pose_landmarks_style.return_value = (
            sentinel.pose_style
        )
        drawing_styles.get_default_hand_landmarks_style.return_value = (
            sentinel.hand_style
        )
        drawing_styles.get_default_hand_connections_style.return_value = (
            sentinel.hand_connection_style
        )

        proc = _load_module(
            "websocket_processing_with_mp",
            "websocket_processing.py",
            fake_modules={
                "mediapipe": mp,
                "mediapipe.tasks": tasks,
                "mediapipe.tasks.python": python,
                "mediapipe.tasks.python.vision": vision,
                "mediapipe.tasks.python.components": components,
                "mediapipe.tasks.python.components.containers": containers,
                "mediapipe.tasks.python.components.containers.landmark": landmark,
                "mediapipe.tasks.python.vision.drawing_styles": drawing_styles,
                "mediapipe.tasks.python.vision.drawing_utils": drawing_utils,
            },
            env={"ENABLE_MEDIAPIPE": "1"},
        )

        self.assertTrue(proc.MEDIAPIPE_OK)
        self.assertIs(proc.mp, mp)
        self.assertIs(proc.mp_vision, vision)
        self.assertIs(proc.mp_python, python)
        self.assertIs(proc.mp_landmark, landmark)
        self.assertIs(proc.mp_styles, drawing_styles)
        self.assertIs(proc.mp_drawing, drawing_utils)

        self.assertIs(proc.POSE_CONNECTIONS, sentinel.pose_connections)
        self.assertIs(proc.HAND_CONNECTIONS, sentinel.hand_connections)
        self.assertIs(proc.POSE_LANDMARK_STYLE, sentinel.pose_style)
        self.assertIs(proc.HAND_LANDMARK_STYLE, sentinel.hand_style)
        self.assertIs(
            proc.HAND_CONNECTION_STYLE,
            sentinel.hand_connection_style,
        )

    # build_landmarkers

    def test_build_landmarkers_returns_none_pair_without_mediapipe(self):
        self.assertEqual(self.proc.build_landmarkers(), (None, None))

    def test_build_landmarkers_creates_pose_and_hand_detectors(self):
        vision, python = MagicMock(), MagicMock()
        with patch.object(self.proc, "MEDIAPIPE_OK", True), \
                patch.object(self.proc, "mp_vision", vision), \
                patch.object(self.proc, "mp_python", python):
            pose, hand = self.proc.build_landmarkers()
        self.assertIs(pose, vision.PoseLandmarker.create_from_options.return_value)
        self.assertIs(hand, vision.HandLandmarker.create_from_options.return_value)
        self.assertEqual(vision.HandLandmarkerOptions.call_args[1]["num_hands"], 2)
        model_paths = [c[1]["model_asset_path"] for c in python.BaseOptions.call_args_list]
        self.assertEqual(
            [Path(path).name for path in model_paths],
            ["pose_landmarker_full.task", "hand_landmarker.task"],
        )

    def test_build_landmarkers_returns_none_pair_on_init_failure(self):
        vision = MagicMock()
        vision.PoseLandmarker.create_from_options.side_effect = RuntimeError("missing .task file")
        with patch.object(self.proc, "MEDIAPIPE_OK", True), \
                patch.object(self.proc, "mp_vision", vision), \
                patch.object(self.proc, "mp_python", MagicMock()):
            with self.assertLogs(self.proc.logger, "ERROR"):
                result = self.proc.build_landmarkers()
        self.assertEqual(result, (None, None))

    # apply_landmarks

    def test_apply_landmarks_returns_original_when_nothing_detected(self):
        image = Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()
        pose.detect.return_value = SimpleNamespace(pose_landmarks=[])
        hand.detect.return_value = SimpleNamespace(hand_landmarks=[])
        modules, mp = self._fake_mediapipe()
        with patch.object(self.proc, "mp", mp), \
        patch.object(self.proc, "mp_drawing", MagicMock()) as drawing:
            result = self.proc.apply_landmarks(image, pose, hand)
        self.assertIs(result, image)
        drawing.draw_landmarks.assert_not_called()

    def test_apply_landmarks_works_without_detectors(self):
        image = Image.new("RGB", (8, 8))
        modules, _ = self._fake_mediapipe()
        with patch.dict(sys.modules, modules):
            self.assertIs(self.proc.apply_landmarks(image, None, None), image)

    def test_apply_landmarks_draws_pose_and_every_hand(self):
        image = Image.new("RGB", (8, 6))
        pose, hand = MagicMock(), MagicMock()

        pose.detect.return_value = SimpleNamespace(
            pose_landmarks=[[self._landmark()] * 33]
        )

        hand.detect.return_value = SimpleNamespace(
            hand_landmarks=[
                [self._landmark()] * 21,
                [self._landmark()] * 21,
            ]
        )

        modules, mp = self._fake_mediapipe()

        pose_connections = sentinel.pose_connections
        hand_connections = sentinel.hand_connections

        landmark_module = MagicMock()
        landmark_module.NormalizedLandmark.side_effect = (
            lambda **kwargs: SimpleNamespace(**kwargs)
        )

        with patch.object(self.proc, "mp", mp), \
                patch.object(self.proc, "mp_landmark", landmark_module), \
                patch.object(
                    self.proc,
                    "POSE_CONNECTIONS",
                    pose_connections,
                ), \
                patch.object(
                    self.proc,
                    "HAND_CONNECTIONS", 
                    hand_connections,
                ), \
                patch.object(
                    self.proc,
                    "mp_drawing",
                    MagicMock(),
                ) as drawing:

            result = self.proc.apply_landmarks(image, pose, hand)

        self.assertEqual(drawing.draw_landmarks.call_count, 3)

        connections = [
            c[0][2]
            for c in drawing.draw_landmarks.call_args_list
        ]

        self.assertIs(connections[0], pose_connections)
        self.assertIs(connections[1], hand_connections)
        self.assertIs(connections[2], hand_connections)

        self.assertIsInstance(result, Image.Image)
        self.assertIsNot(result, image)
        self.assertEqual((result.mode, result.size), ("RGB", (8, 6)))

    def test_apply_landmarks_draws_hands_when_no_pose_found(self):
        image = Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()

        pose.detect.return_value = SimpleNamespace(
            pose_landmarks=[]
        )

        hand.detect.return_value = SimpleNamespace(
            hand_landmarks=[[self._landmark()] * 21]
        )

        modules, mp = self._fake_mediapipe()

        landmark_module = MagicMock()
        landmark_module.NormalizedLandmark.side_effect = (
            lambda **kwargs: SimpleNamespace(**kwargs)
        )

        with patch.object(self.proc, "mp", mp), \
                patch.object(self.proc, "mp_landmark", landmark_module), \
                patch.object(
                    self.proc,
                    "mp_drawing",
                    MagicMock(),
                ) as drawing:

            self.proc.apply_landmarks(image, pose, hand)

        self.assertEqual(drawing.draw_landmarks.call_count, 1)

    # process_frame

    def test_process_frame_applies_landmarks_when_enabled(self):
        image, annotated = Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()
        with patch.object(self.proc, "apply_landmarks", return_value=annotated) as apply:
            result = self.proc.process_frame(image, pose, hand, True)
        self.assertIs(result, annotated)
        apply.assert_called_once_with(image, pose, hand)

    def test_process_frame_passes_image_through_when_disabled(self):
        image = Image.new("RGB", (8, 8))
        with patch.object(self.proc, "apply_landmarks") as apply:
            result = self.proc.process_frame(image, None, None, False)
        self.assertIs(result, image)
        apply.assert_not_called()

    # save_test_video

    def test_save_test_video_writes_every_frame(self):
        frames = [Image.new("RGB", (8, 6))] * 3
        writer = MagicMock()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(self.proc.cv2, "VideoWriter", return_value=writer) as video_writer:
            path = self.proc.save_test_video(frames, output_dir=tmp)
            self.assertTrue(path.startswith(os.path.abspath(tmp)))
        self.assertTrue(path.endswith(".mp4"))
        self.assertEqual(writer.write.call_count, 3)
        writer.release.assert_called_once()
        _, _, fps, size = video_writer.call_args[0]
        self.assertEqual((fps, size), (15.0, (8, 6)))

    def test_save_test_video_with_no_frames_returns_empty_string(self):
        self.assertEqual(self.proc.save_test_video([]), "")

    def test_save_test_video_releases_writer_and_returns_empty_on_error(self):
        writer = MagicMock()
        writer.write.side_effect = RuntimeError("disk full")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(self.proc.cv2, "VideoWriter", return_value=writer), \
                self.assertLogs(self.proc.logger, "ERROR"):
            path = self.proc.save_test_video([Image.new("RGB", (8, 8))], output_dir=tmp)
        self.assertEqual(path, "")
        writer.release.assert_called_once()

    # run_inference

    def test_run_inference_frames_mode_uses_frames_endpoint(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"prediction": "hi", "confidence": 0.9}
        frames = [Image.new("RGB", (8, 8))] * 16
        result = self.proc.run_inference(frames, "frames", model_api)
        model_api.predict_from_frames.assert_called_once_with(frames)
        model_api.predict.assert_not_called()
        self.assertEqual(result["prediction"], "hi")
        self.assertGreaterEqual(result["total_latency_ms"], 0)

    def test_run_inference_video_mode_uses_video_endpoint(self):
        model_api = MagicMock()
        model_api.predict.return_value = {"prediction": "bye"}
        frames = [Image.new("RGB", (8, 8))] * 16
        result = self.proc.run_inference(frames, "video", model_api)
        model_api.predict.assert_called_once_with(frames)
        model_api.predict_from_frames.assert_not_called()
        self.assertEqual(result["prediction"], "bye")
        self.assertIn("total_latency_ms", result)

    def test_run_inference_hybrid_prefers_frames_path(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"prediction": "hi"}
        result = self.proc.run_inference([Image.new("RGB", (8, 8))] * 16, "hybrid", model_api)
        model_api.predict.assert_not_called()
        self.assertEqual(result["prediction"], "hi")

    def test_run_inference_hybrid_falls_back_to_video_on_error(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"error": "frames path down"}
        model_api.predict.return_value = {"prediction": "from video"}
        frames = [Image.new("RGB", (8, 8))] * 16
        with self.assertLogs(self.proc.logger, "WARNING"):
            result = self.proc.run_inference(frames, "hybrid", model_api)
        model_api.predict.assert_called_once_with(frames)
        self.assertEqual(result["prediction"], "from video")

    def test_run_inference_saves_test_video_only_when_asked(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {}
        frames = [Image.new("RGB", (8, 8))] * 16
        with patch.object(self.proc, "save_test_video") as save:
            self.proc.run_inference(frames, "frames", model_api)
            save.assert_not_called()
            self.proc.run_inference(frames, "frames", model_api, save_test_videos=True)
            save.assert_called_once_with(frames)


# FirebaseStartupTests

class FirebaseStartupTests(unittest.TestCase):

    DB_URL = "https://sightsign-default-rtdb.asia-southeast1.firebasedatabase.app/"

    def _run_init(self, admin, hosted):
        """Run firebase_admin_init.py with os.path.exists faked and firebase.json's
        read faked so the test doesn't depend on real files on disk."""
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith("firebase.json"):
                return mock_open(read_data=json.dumps({"databaseURL": self.DB_URL}))()
            return real_open(path, *args, **kwargs)

        with patch.dict(sys.modules, {"firebase_admin": admin}), \
                patch("os.path.exists", return_value=hosted), \
                patch("builtins.open", side_effect=fake_open):
            return runpy.run_path(str(BACKEND / "firebase_admin_init.py"))

    def test_admin_initializes_with_backend_relative_credentials(self):
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        self._run_init(admin, hosted=False)
        admin.credentials.Certificate.assert_called_once_with(str(BACKEND / "firebase-admin.json"))
        admin.initialize_app.assert_called_once_with(
            admin.credentials.Certificate.return_value, {"databaseURL": self.DB_URL}
        )

    def test_admin_uses_hosted_secret(self):
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        self._run_init(admin, hosted=True)
        admin.credentials.Certificate.assert_called_once_with("/etc/secrets/firebase-admin.json")
        admin.initialize_app.assert_called_once_with(
            admin.credentials.Certificate.return_value, {"databaseURL": self.DB_URL}
        )

    def test_admin_reuses_existing_app(self):
        admin = MagicMock()
        self._run_init(admin, hosted=False)
        admin.initialize_app.assert_not_called()
        admin.credentials.Certificate.assert_not_called()

    def test_admin_initialize_app_passes_cert_object(self):
        """initialize_app receives the return value of credentials.Certificate."""
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        sentinel = object()
        admin.credentials.Certificate.return_value = sentinel
        self._run_init(admin, hosted=False)
        admin.initialize_app.assert_called_once_with(sentinel, {"databaseURL": self.DB_URL})

    def test_admin_auth_is_exported(self):
        """authentication.py and main.py import admin_auth from this module."""
        admin = MagicMock()
        result = self._run_init(admin, hosted=False)
        self.assertIs(result["admin_auth"], admin.auth)

# HistoryTests - unit tests for history.py (Realtime Database helpers)

class HistoryTests(unittest.TestCase):
    
    def setUp(self):
        self.db = MagicMock()
        firebase_admin = MagicMock()
        firebase_admin.db = self.db
        self.module = _load_module(
            "history_under_test", "history.py",
            {"firebase_admin_init": MagicMock(), "firebase_admin": firebase_admin},
        )
        self.ref = self.db.reference.return_value

    def test_history_is_stored_under_the_users_own_node(self):
        self.ref.get.return_value = None
        self.module.retrieve_history("alice")
        self.db.reference.assert_called_once_with("user/alice/history")

    def test_retrieve_returns_empty_list_when_nothing_stored(self):
        self.ref.get.return_value = None
        self.assertEqual(self.module.retrieve_history("alice"), [])

    def test_retrieve_returns_empty_list_for_legacy_non_dict_data(self):
        self.ref.get.return_value = ["old", "list", "format"]
        self.assertEqual(self.module.retrieve_history("alice"), [])

    def test_retrieve_returns_items_with_ids_newest_first(self):
        self.ref.get.return_value = {
            "-N1": {"translation": "hello", "timestamp": "2025-01-01T10:00:00"},
            "-N3": {"translation": "thanks", "timestamp": "2025-01-03T10:00:00"},
            "-N2": {"translation": "bye", "timestamp": "2025-01-02T10:00:00"},
        }
        self.assertEqual(
            self.module.retrieve_history("alice"),
            [
                {"id": "-N3", "translation": "thanks", "timestamp": "2025-01-03T10:00:00"},
                {"id": "-N2", "translation": "bye", "timestamp": "2025-01-02T10:00:00"},
                {"id": "-N1", "translation": "hello", "timestamp": "2025-01-01T10:00:00"},
            ],
        )

    def test_retrieve_defaults_missing_fields_and_sorts_them_last(self):
        self.ref.get.return_value = {
            "-N1": {},
            "-N2": {"translation": "hi", "timestamp": "2025-01-02T10:00:00"},
        }
        self.assertEqual(
            self.module.retrieve_history("alice"),
            [
                {"id": "-N2", "translation": "hi", "timestamp": "2025-01-02T10:00:00"},
                {"id": "-N1", "translation": "", "timestamp": ""},
            ],
        )

    def test_store_pushes_new_entry_and_returns_it_with_its_key(self):
        pushed = self.ref.push.return_value
        pushed.key = "-Nabc"
        item = self.module.store_translation("alice", "hello")
        self.assertEqual(item["id"], "-Nabc")
        self.assertEqual(item["translation"], "hello")
        self.module.datetime.fromisoformat(item["timestamp"])  # valid ISO timestamp
        pushed.set.assert_called_once_with(
            {"translation": "hello", "timestamp": item["timestamp"]}
        )

    def test_delete_translation_removes_existing_entry(self):
        child = self.ref.child.return_value
        child.get.return_value = {"translation": "hello"}
        self.assertTrue(self.module.delete_translation("alice", "-N1"))
        self.ref.child.assert_called_once_with("-N1")
        child.delete.assert_called_once()

    def test_delete_translation_reports_missing_entry(self):
        child = self.ref.child.return_value
        child.get.return_value = None
        self.assertFalse(self.module.delete_translation("alice", "-N1"))
        child.delete.assert_not_called()

    def test_delete_all_removes_existing_history(self):
        self.ref.get.return_value = {"-N1": {"translation": "hello"}}
        self.assertTrue(self.module.delete_all_translations("alice"))
        self.db.reference.assert_called_once_with("user/alice/history")
        self.ref.delete.assert_called_once()

    def test_delete_all_reports_no_history(self):
        self.ref.get.return_value = None
        self.assertFalse(self.module.delete_all_translations("alice"))
        self.ref.delete.assert_not_called()


# ISLModelAPITests - unit tests for model.py

class ISLModelAPITests(unittest.TestCase):
    """Unit tests for ISLModelAPI in model.py; all network calls mocked."""

    def _load_model_class(self):
        """Import ISLModelAPI without hitting the network."""
        spec = importlib.util.spec_from_file_location("model_under_test", BACKEND / "model.py")
        mod = importlib.util.module_from_spec(spec)
        mock_requests = MagicMock()
        with patch.dict(sys.modules, {"requests": mock_requests}):
            spec.loader.exec_module(mod)
        return mod.ISLModelAPI, mock_requests

    # check_health

    def test_check_health_returns_true_on_200_ok(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok"}
        api.session.get = MagicMock(return_value=resp)
        self.assertTrue(api.check_health())
        api.session.get.assert_called_once_with(api.health_url, timeout=3)

    def test_check_health_returns_false_on_non_200(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock()
        resp.status_code = 503
        resp.json.return_value = {}
        api.session.get = MagicMock(return_value=resp)
        self.assertFalse(api.check_health())

    def test_check_health_returns_false_on_wrong_status_value(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "starting"}
        api.session.get = MagicMock(return_value=resp)
        self.assertFalse(api.check_health())

    def test_check_health_returns_false_on_network_error(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.get = MagicMock(side_effect=ConnectionError("refused"))
        self.assertFalse(api.check_health())

    # predict_from_frames - validation

    def test_predict_from_frames_requires_exactly_16(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.post = MagicMock()
        result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 5)
        self.assertIn("error", result)
        self.assertIn("16", result["error"])
        api.session.post.assert_not_called()

    def test_predict_from_frames_rejects_empty_list(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.post = MagicMock()
        result = api.predict_from_frames([])
        self.assertIn("error", result)
        api.session.post.assert_not_called()

    def test_predict_from_frames_rejects_17_frames(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.post = MagicMock()
        result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 17)
        self.assertIn("error", result)
        api.session.post.assert_not_called()

    def test_predict_from_frames_success_returns_json(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"prediction": "hello", "confidence": 0.9}
        api.session.post = MagicMock(return_value=resp)
        frames = [Image.new("RGB", (224, 224))] * 16
        result = api.predict_from_frames(frames)
        self.assertEqual(result["prediction"], "hello")
        self.assertEqual(result["confidence"], 0.9)

    def test_predict_from_frames_posts_to_correct_endpoint(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=3)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        api.session.post = MagicMock(return_value=resp)
        api.predict_from_frames([Image.new("RGB", (224, 224))] * 16)
        call_kwargs = api.session.post.call_args
        self.assertEqual(call_kwargs[0][0], api.predict_frames_url)
        payload = call_kwargs[1]["json"]
        self.assertEqual(payload["top_k"], 3)
        self.assertEqual(len(payload["frames"]), 16)

    def test_predict_from_frames_retries_on_503(self):
        """On 503 the client retries once; two consecutive 503s return an error."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp_503 = MagicMock(status_code=503)
        api.session.post = MagicMock(return_value=resp_503)
        with patch("time.sleep"):  # avoid real sleep in tests
            result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 16)
        self.assertIn("error", result)
        self.assertEqual(api.session.post.call_count, 2)

    def test_predict_from_frames_first_503_then_200(self):
        """After a 503 retry the successful response is returned."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp_503 = MagicMock(status_code=503)
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"prediction": "world", "confidence": 0.8}
        api.session.post = MagicMock(side_effect=[resp_503, resp_200])
        with patch("time.sleep"):
            result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 16)
        self.assertEqual(result["prediction"], "world")

    def test_predict_from_frames_non_200_non_503_returns_error(self):
        """A 404 response is treated as a final error (no retry)."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock(status_code=404, text="Not Found")
        api.session.post = MagicMock(return_value=resp)
        result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 16)
        self.assertIn("error", result)

    def test_predict_from_frames_network_exception_returns_error(self):
        """A network exception from session.post is caught and returned as an error."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.post = MagicMock(side_effect=ConnectionError("timeout"))
        with patch("time.sleep"):
            result = api.predict_from_frames([Image.new("RGB", (224, 224))] * 16)
        self.assertIn("error", result)

    # predict (video path)

    def test_predict_empty_frames_returns_error(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        api.session.post = MagicMock()
        result = api.predict([])
        self.assertIn("error", result)
        api.session.post.assert_not_called()

    def test_predict_success_returns_json(self):
        """predict() posts to /predict and returns the API response on 200."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"prediction": "bye", "confidence": 0.7}
        api.session.post = MagicMock(return_value=resp)

        mock_cv2 = MagicMock()
        mock_cv2.VideoWriter_fourcc.return_value = 0
        mock_cv2.VideoWriter.return_value = MagicMock()
        mock_cv2.cvtColor.return_value = numpy.zeros((8, 8, 3), dtype=numpy.uint8)

        frames = [Image.new("RGB", (8, 8))] * 4
        with patch.dict(sys.modules, {"cv2": mock_cv2}), \
                patch("builtins.open", mock_open(read_data=b"fake_mp4")), \
                patch("os.path.exists", return_value=True), \
                patch("os.remove"):
            result = api.predict(frames)
        self.assertEqual(result["prediction"], "bye")

    def test_predict_non_200_returns_error(self):
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock(status_code=500, text="Server error")
        api.session.post = MagicMock(return_value=resp)

        mock_cv2 = MagicMock()
        mock_cv2.VideoWriter_fourcc.return_value = 0
        mock_cv2.VideoWriter.return_value = MagicMock()
        mock_cv2.cvtColor.return_value = numpy.zeros((8, 8, 3), dtype=numpy.uint8)

        frames = [Image.new("RGB", (8, 8))] * 4
        with patch.dict(sys.modules, {"cv2": mock_cv2}), \
                patch("builtins.open", mock_open(read_data=b"fake_mp4")), \
                patch("os.path.exists", return_value=True), \
                patch("os.remove"):
            result = api.predict(frames)
        self.assertIn("error", result)

    def test_predict_cleans_up_tmp_file_when_save_disabled(self):
        """predict() removes the temp mp4 file unless SAVE_TEST_VIDEOS=1."""
        ISLModelAPI, _ = self._load_model_class()
        api = ISLModelAPI(top_k=1)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        api.session.post = MagicMock(return_value=resp)

        mock_cv2 = MagicMock()
        mock_cv2.VideoWriter_fourcc.return_value = 0
        mock_cv2.VideoWriter.return_value = MagicMock()
        mock_cv2.cvtColor.return_value = numpy.zeros((8, 8, 3), dtype=numpy.uint8)

        frames = [Image.new("RGB", (8, 8))] * 2
        with patch.dict(sys.modules, {"cv2": mock_cv2}), \
                patch("builtins.open", mock_open(read_data=b"")), \
                patch("os.path.exists", return_value=True), \
                patch("os.remove") as mock_remove, \
                patch.dict("os.environ", {"SAVE_TEST_VIDEOS": "0"}):
            api.predict(frames)
        mock_remove.assert_called_once()

    # ISLModelAPI URL construction

    def test_model_api_urls_built_from_base_url(self):
        """All endpoint URLs are derived from BASE_URL with trailing slash stripped."""
        ISLModelAPI, _ = self._load_model_class()
        with patch.dict("os.environ", {"BASE_URL": "https://myhost.com/"}):
            api = ISLModelAPI(top_k=1)
        self.assertEqual(api.predict_frames_url, "https://myhost.com/predict_frames")
        self.assertEqual(api.predict_video_url, "https://myhost.com/predict")
        self.assertEqual(api.health_url, "https://myhost.com/health")


# AuthenticationHelperTests - unit tests for authentication.py

class AuthenticationHelperTests(unittest.TestCase):
    """authentication.py talks to the Firebase Identity REST API via `requests`
    and creates users through the Admin SDK (`admin_auth`); both are mocked."""

    CONFIG = {"apiKey": "test-key", "identityURL": "https://identity.example.com/v1"}

    def setUp(self):
        self.admin_auth = MagicMock()
        self.requests = MagicMock()
        self.requests.RequestException = real_requests.RequestException
        self.mod = self._load_auth_module()

    def _load_auth_module(self, hosted=False, opened_paths=None):
        spec = importlib.util.spec_from_file_location("auth_under_test", BACKEND / "authentication.py")
        mod = importlib.util.module_from_spec(spec)

        def fake_open(path, *args, **kwargs):
            if opened_paths is not None:
                opened_paths.append(str(path))
            return mock_open(read_data=json.dumps(self.CONFIG))()

        fakes = {
            "requests": self.requests,
            "firebase_admin_init": MagicMock(admin_auth=self.admin_auth),
        }
        with patch.dict(sys.modules, fakes), \
                patch("os.path.exists", return_value=hosted), \
                patch("builtins.open", side_effect=fake_open), \
                patch("dotenv.load_dotenv"):
            spec.loader.exec_module(mod)
        return mod

    def _respond(self, payload=None, status_error=None):
        resp = MagicMock()
        resp.json.return_value = payload if payload is not None else {}
        if status_error is not None:
            resp.raise_for_status.side_effect = status_error
        self.requests.post.return_value = resp
        return resp

    # configuration

    def test_reads_api_key_and_identity_url_from_config(self):
        self.assertEqual(self.mod.FIREBASE_API_KEY, "test-key")
        self.assertEqual(self.mod._IDENTITY_URL, "https://identity.example.com/v1")

    def test_authentication_uses_local_config_by_default(self):
        opened = []
        self._load_auth_module(hosted=False, opened_paths=opened)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].endswith("firebase.json"))
        self.assertNotIn("/etc/secrets", opened[0])

    def test_authentication_uses_hosted_config_when_present(self):
        """When /etc/secrets/firebase.json exists, it is used instead of the local one."""
        opened = []
        self._load_auth_module(hosted=True, opened_paths=opened)
        self.assertTrue(any("/etc/secrets/firebase.json" in p for p in opened))

    # _identity_request

    def test_identity_request_posts_to_endpoint_with_api_key(self):
        self._respond({"ok": True})
        result = self.mod._identity_request("accounts:foo", {"a": 1})
        self.assertEqual(result, {"ok": True})
        self.requests.post.assert_called_once_with(
            "https://identity.example.com/v1/accounts:foo",
            params={"key": "test-key"},
            json={"a": 1},
            timeout=10,
        )

    def test_identity_request_raises_on_http_error(self):
        self._respond(status_error=RuntimeError("400 Bad Request"))
        with self.assertRaises(RuntimeError):
            self.mod._identity_request("accounts:foo", {})

    # login_account

    def test_login_account_success(self):
        self._respond({"localId": "uid2", "idToken": "tok2"})
        result = self.mod.login_account("a@b.com", "pw")
        self.assertEqual(result, {"id": "uid2", "token": "tok2"})
        self.assertEqual(
            self.requests.post.call_args[0][0],
            "https://identity.example.com/v1/accounts:signInWithPassword",
        )
        self.assertEqual(
            self.requests.post.call_args[1]["json"],
            {"email": "a@b.com", "password": "pw", "returnSecureToken": True},
        )

    def test_login_account_coerces_password_to_string(self):
        self._respond({"localId": "u", "idToken": "t"})
        self.mod.login_account("a@b.com", 123456)
        self.assertEqual(self.requests.post.call_args[1]["json"]["password"], "123456")

    def test_login_account_returns_none_on_http_error(self):
        self._respond(status_error=RuntimeError("INVALID_PASSWORD"))
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertIsNone(self.mod.login_account("a@b.com", "wrong"))

    def test_login_account_returns_none_on_network_error(self):
        self.requests.post.side_effect = ConnectionError("timeout")
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertIsNone(self.mod.login_account("a@b.com", "pw"))

    def test_login_account_returns_none_on_unexpected_response(self):
        self._respond({"unexpected": "shape"})
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertIsNone(self.mod.login_account("a@b.com", "pw"))

    # register_account

    def test_register_account_creates_user_then_signs_in(self):
        self._respond({"localId": "uid1", "idToken": "tok1"})
        result = self.mod.register_account("a@b.com", "pw")
        self.assertEqual(result, {"id": "uid1", "token": "tok1"})
        self.admin_auth.create_user.assert_called_once_with(email="a@b.com", password="pw")
        self.requests.post.assert_called_once()  # the sign-in that fetches the ID token

    def test_register_account_returns_none_when_user_creation_fails(self):
        self.admin_auth.create_user.side_effect = Exception("email already exists")
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertIsNone(self.mod.register_account("a@b.com", "pw"))
        self.requests.post.assert_not_called()

    def test_register_account_returns_none_when_sign_in_fails(self):
        self._respond(status_error=RuntimeError("boom"))
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertIsNone(self.mod.register_account("a@b.com", "pw"))

    # forgot_password

    def test_forgot_password_sends_reset_email(self):
        self._respond({})
        self.assertTrue(self.mod.forgot_password("a@b.com"))
        self.assertEqual(
            self.requests.post.call_args[0][0],
            "https://identity.example.com/v1/accounts:sendOobCode",
        )
        self.assertEqual(
            self.requests.post.call_args[1]["json"],
            {"requestType": "PASSWORD_RESET", "email": "a@b.com"},
        )

    def test_forgot_password_hides_failures_from_the_caller(self):
        """Always True so the API cannot be used to discover which emails exist."""
        self._respond(status_error=RuntimeError("EMAIL_NOT_FOUND"))
        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertTrue(self.mod.forgot_password("nobody@b.com"))

    # email_verify

    def test_email_verify_success(self):
        self._respond({})

        result = self.mod.email_verify("id_token_123")

        self.assertTrue(result)

        self.assertEqual(
            self.requests.post.call_args[1]["json"],
            {
                "requestType": "VERIFY_EMAIL",
                "idToken": "id_token_123",
            },
        )


    def test_email_verify_exception_returns_false(self):
        self._respond(status_error=RuntimeError("INVALID_ID_TOKEN"))

        with self.assertLogs(self.mod.logger, "ERROR"):
            self.assertFalse(self.mod.email_verify("bad_token"))


if __name__ == "__main__":
    unittest.main()