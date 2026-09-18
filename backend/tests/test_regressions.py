"""Offline regressions: real Flask routes with external services replaced.

Run from the repository root: python -m unittest discover -s backend/tests -v
"""
import base64
import importlib.util
import json
from concurrent.futures import Future
from io import BytesIO
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import MagicMock, patch, call

import flask  # Load real runtime dependencies before temporarily replacing modules.
import numpy
from PIL import Image
from dotenv import load_dotenv

# Load environment variables from .env file for local testing
load_dotenv()

BACKEND = Path(__file__).resolve().parents[1]


# Helpers shared across test classes

def _make_jpeg_b64(width: int = 8, height: int = 8) -> str:
    """Return a base64-encoded JPEG of a blank RGB image."""
    buf = BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _frame_msg(width: int = 8, height: int = 8) -> str:
    """Return a JSON WebSocket frame message."""
    return json.dumps({"frame": _make_jpeg_b64(width, height)})


# BackendRegressionTests

class BackendRegressionTests(unittest.TestCase):
    def setUp(self):
        self.auth = MagicMock()
        self.auth.ExpiredIdTokenError = type("ExpiredIdTokenError", (Exception,), {})
        self.auth.verify_id_token.return_value = {"uid": "alice"}
        self.history = MagicMock()
        self.history.retrieve_history.return_value = ["hello"]
        sock = MagicMock()
        sock.Sock.return_value.route.side_effect = lambda path: lambda f: f
        modules = {
            "authentication": MagicMock(),
            "history": self.history,
            "firebase_admin_init": MagicMock(admin_auth=self.auth),
            "model": MagicMock(),
            "cv2": MagicMock(),
            "flask_cors": MagicMock(),
            "flask_sock": sock,
        }
        spec = importlib.util.spec_from_file_location("backend_under_test", BACKEND / "main.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(
            "os.environ", {"ENABLE_MEDIAPIPE": "0"}
        ), patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        self.addCleanup(self.module.executor.shutdown, wait=True)
        self.client = self.module.app.test_client()

    def test_history_uses_token_owner_even_with_another_id(self):
        response = self.client.get(
            "/history?id=bob", headers={"Authorization": "Bearer valid"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"history": ["hello"]})
        self.history.retrieve_history.assert_called_once_with("alice")

    def test_history_needs_no_caller_supplied_id(self):
        response = self.client.get("/history", headers={"Authorization": "Bearer valid"})
        self.assertEqual(response.status_code, 200)
        self.history.retrieve_history.assert_called_once_with("alice")

    def test_history_rejects_missing_invalid_and_expired_tokens(self):
        self.assertEqual(self.client.get("/history?id=bob").status_code, 401)
        for error in (ValueError("bad token"), self.auth.ExpiredIdTokenError()):
            self.auth.verify_id_token.side_effect = error
            response = self.client.get(
                "/history?id=bob", headers={"Authorization": "Bearer invalid"}
            )
            self.assertEqual(response.status_code, 401)
        self.history.retrieve_history.assert_not_called()

    def test_history_write_also_uses_token_owner(self):
        response = self.client.post(
            "/history", json={"id": "bob", "translation": "hello"},
            headers={"Authorization": "Bearer valid"},
        )
        self.assertEqual(response.status_code, 200)
        self.history.store_translation.assert_called_once_with("alice", "hello")

    def test_slow_landmarks_keep_pending_job_and_collect_its_result(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
        message = json.dumps({"frame": base64.b64encode(buffer.getvalue()).decode()})
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
        with patch.object(self.module, "executor", pool), patch.object(
            self.module, "build_landmarkers", return_value=(pose, hand)
        ), patch.object(self.module.time, "monotonic", side_effect=[1, 2, 3]), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        self.assertEqual(pool.submit.call_count, 2)
        first.result.assert_called_once()
        self.assertTrue(second.cancelled())
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    def test_disconnect_waits_for_running_landmarks_before_closing_detectors(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
        message = json.dumps({"frame": base64.b64encode(buffer.getvalue()).decode()})
        pose, hand = MagicMock(), MagicMock()
        pending = MagicMock()
        pending.cancel.return_value = False

        def finish():
            pose.close.assert_not_called()
            hand.close.assert_not_called()

        pending.result.side_effect = finish
        pool = MagicMock()
        pool.submit.return_value = pending
        ws = MagicMock()
        ws.receive.side_effect = [message, None]
        with patch.object(self.module, "executor", pool), patch.object(
            self.module, "build_landmarkers", return_value=(pose, hand)
        ), self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        pending.result.assert_called_once()
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    # TESTS – REST: index

    def test_index_returns_api_running_message(self):  
        """GET / returns a JSON status message with version 2.0."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertEqual(body["message"], "SignBridge API is running")
        self.assertEqual(body["version"], "2.0")

    # TESTS – REST: /register

    def test_register_success(self):  
        """POST /register with valid credentials returns id and token."""
        self.module.register_account = MagicMock(return_value={"id": "uid1", "token": "tok1"})
        response = self.client.post("/register", json={"email": "a@b.com", "password": "s3cr3t"})
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertEqual(body["id"], "uid1")
        self.assertEqual(body["token"], "tok1")

    def test_register_missing_email_returns_400(self):  
        """POST /register without email field returns 400."""
        response = self.client.post("/register", json={"password": "s3cr3t"})
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertIn("error", body)

    def test_register_missing_password_returns_400(self):  
        """POST /register without password field returns 400."""
        response = self.client.post("/register", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertIn("error", body)

    def test_register_empty_body_returns_400(self):  
        """POST /register with empty JSON object returns 400."""
        response = self.client.post("/register", json={})
        self.assertEqual(response.status_code, 400)

    def test_register_no_json_returns_400(self):  
        """POST /register with non-JSON body returns 400."""
        response = self.client.post("/register", data="not json",
                                    content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    def test_register_backend_failure_returns_400(self):  
        """POST /register returns 400 with error message when backend returns None."""
        self.module.register_account = MagicMock(return_value=None)
        response = self.client.post("/register", json={"email": "a@b.com", "password": "pw"})
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertEqual(body["error"], "Registration failed")
        self.assertEqual(body["id"], "")
        self.assertEqual(body["token"], "")

    # TESTS – REST: /login

    def test_login_success(self):  
        """POST /login with valid credentials returns id and token."""
        self.module.login_account = MagicMock(return_value={"id": "uid2", "token": "tok2"})
        response = self.client.post("/login", json={"email": "a@b.com", "password": "pw"})
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertEqual(body["id"], "uid2")
        self.assertEqual(body["token"], "tok2")

    def test_login_missing_email_returns_400(self):  
        """POST /login without email returns 400."""
        response = self.client.post("/login", json={"password": "pw"})
        self.assertEqual(response.status_code, 400)

    def test_login_missing_password_returns_400(self):  
        """POST /login without password returns 400."""
        response = self.client.post("/login", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 400)

    def test_login_empty_body_returns_400(self):  
        """POST /login with empty JSON returns 400."""
        response = self.client.post("/login", json={})
        self.assertEqual(response.status_code, 400)

    def test_login_no_json_returns_400(self):  
        """POST /login with non-JSON body returns 400."""
        response = self.client.post("/login", data="bad", content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    def test_login_backend_failure_returns_400(self):  
        """POST /login returns 400 when backend returns None (wrong credentials)."""
        self.module.login_account = MagicMock(return_value=None)
        response = self.client.post("/login", json={"email": "a@b.com", "password": "wrong"})
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertEqual(body["error"], "Login failed")

    # TESTS – REST: /forgot-password

    def test_forgot_password_success(self):  
        """POST /forgot-password returns success when backend succeeds."""
        self.module.forgot_password = MagicMock(return_value="Password reset email sent successfully.")
        response = self.client.post("/forgot-password", json={"email": "a@b.com"})
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertEqual(body["success"], "Password reset email sent successfully.")

    def test_forgot_password_missing_email_returns_400(self):  
        """POST /forgot-password without email returns 400."""
        response = self.client.post("/forgot-password", json={})
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.data)
        self.assertFalse(body["success"])

    def test_forgot_password_no_json_returns_400(self):  
        """POST /forgot-password with non-JSON body returns 400."""
        response = self.client.post("/forgot-password", data="bad", content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    def test_forgot_password_backend_not_called_on_missing_email(self):  
        """forgot_password helper must not be called when email is absent."""
        self.module.forgot_password = MagicMock()
        self.client.post("/forgot-password", json={})
        self.module.forgot_password.assert_not_called()

    # REST: /history (auth edge-cases, error paths)

    def test_require_auth_rejects_wrong_scheme(self):  
        """Authorization header with scheme other than Bearer is rejected."""
        response = self.client.get("/history", headers={"Authorization": "Basic abc123"})
        self.assertEqual(response.status_code, 401)
        body = json.loads(response.data)
        self.assertEqual(body["error"], "Missing or invalid token")

    def test_require_auth_rejects_bearer_with_invalid_token(self):  
        """Bearer token that fails verify_id_token returns 401."""
        self.auth.verify_id_token.side_effect = Exception("bad")
        response = self.client.get("/history", headers={"Authorization": "Bearer badtoken"})
        self.assertEqual(response.status_code, 401)

    def test_history_get_returns_correct_structure(self):  
        """GET /history returns a dict with key 'history'."""
        self.history.retrieve_history.return_value = ["a", "b"]
        response = self.client.get("/history", headers={"Authorization": "Bearer valid"})
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertIn("history", body)
        self.assertEqual(body["history"], ["a", "b"])

    def test_history_get_empty_list(self):  
        """GET /history returns empty list when user has no history."""
        self.history.retrieve_history.return_value = []
        response = self.client.get("/history", headers={"Authorization": "Bearer valid"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {"history": []})

    def test_history_post_missing_translation_returns_400(self):  
        """POST /history without translation returns 400."""
        response = self.client.post(
            "/history", json={"id": "alice"},
            headers={"Authorization": "Bearer valid"},
        )
        self.assertEqual(response.status_code, 400)
        self.history.store_translation.assert_not_called()

    def test_history_post_no_body_returns_400(self):  
        """POST /history with no JSON body returns 400 and does not store."""
        response = self.client.post(
            "/history",
            data="not json",
            content_type="text/plain",
            headers={"Authorization": "Bearer valid"},
        )
        self.assertEqual(response.status_code, 400)
        self.history.store_translation.assert_not_called()

    def test_history_post_store_exception_returns_500(self):  
        """POST /history returns 500 when store_translation raises."""
        self.history.store_translation.side_effect = RuntimeError("db down")
        response = self.client.post(
            "/history", json={"translation": "hello"},
            headers={"Authorization": "Bearer valid"},
        )
        self.assertEqual(response.status_code, 500)
        body = json.loads(response.data)
        self.assertEqual(body["message"], "error")

    def test_history_post_token_propagated_to_store(self):  
        """POST /history uses the uid from the verified token, not any body field."""
        self.auth.verify_id_token.return_value = {"uid": "carol"}
        response = self.client.post(
            "/history", json={"translation": "world"},
            headers={"Authorization": "Bearer carol_token"},
        )
        self.assertEqual(response.status_code, 200)
        self.history.store_translation.assert_called_once_with("carol", "world")

    # WebSocket authentication
    def test_websocket_rejects_missing_token(self):  
        """WS /ws without ?token= sends Unauthorized and closes."""
        # No token → verify_id_token raises
        self.auth.verify_id_token.side_effect = Exception("bad")
        ws = MagicMock()
        with self.module.app.test_request_context("/ws"):
            self.module.websocket_translate(ws)
        sent = json.loads(ws.send.call_args_list[0][0][0])
        self.assertEqual(sent["error"], "Unauthorized")
        ws.close.assert_called_once()

    def test_websocket_rejects_invalid_token(self):  
        """WS /ws with an invalid token sends Unauthorized and closes."""
        self.auth.verify_id_token.side_effect = ValueError("bad")
        ws = MagicMock()
        with self.module.app.test_request_context("/ws?token=garbage"):
            self.module.websocket_translate(ws)
        sent = json.loads(ws.send.call_args_list[0][0][0])
        self.assertEqual(sent["error"], "Unauthorized")
        ws.close.assert_called_once()

    def test_websocket_sends_connected_on_auth_success(self):  
        """Authenticated WS connection receives the 'connected' status message."""
        ws = MagicMock()
        ws.receive.return_value = None  # disconnect immediately
        with patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        first_msg = json.loads(ws.send.call_args_list[0][0][0])
        self.assertEqual(first_msg["status"], "connected")
        self.assertEqual(first_msg["message"], "Ready for frames")

    def test_websocket_config_command_updates_mode(self):  
        """Sending a config message switches the inference mode and confirms it."""
        ws = MagicMock()
        ws.receive.side_effect = [
            json.dumps({"type": "config", "mode": "video"}),
            None,
        ]
        with patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        config_ack = next((m for m in sent_msgs if m.get("status") == "config_updated"), None)
        self.assertIsNotNone(config_ack)
        self.assertEqual(config_ack["mode"], "video")

    def test_websocket_config_invalid_mode_not_acknowledged(self):  
        """An unrecognised config mode is silently ignored (no config_updated sent)."""
        ws = MagicMock()
        ws.receive.side_effect = [
            json.dumps({"type": "config", "mode": "unknown_mode"}),
            None,
        ]
        with patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        config_acks = [m for m in sent_msgs if m.get("status") == "config_updated"]
        self.assertEqual(len(config_acks), 0)

    def test_websocket_invalid_frame_sends_error_and_continues(self):  
        """A corrupted base64 frame triggers an error reply and does not crash."""
        ws = MagicMock()
        ws.receive.side_effect = [
            json.dumps({"frame": "!!!not_valid_base64!!!"}),
            None,
        ]
        pool = MagicMock()
        with patch.object(self.module, "executor", pool), \
                patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        error_msgs = [m for m in sent_msgs if "error" in m]
        self.assertTrue(any(m["error"] == "Invalid frame" for m in error_msgs))
        pool.submit.assert_not_called()

    def test_websocket_empty_frame_field_does_not_crash(self):  
        """A JSON message with an empty 'frame' value is silently skipped."""
        ws = MagicMock()
        ws.receive.side_effect = [
            json.dumps({"frame": ""}),
            None,
        ]
        pool = MagicMock()
        with patch.object(self.module, "executor", pool), \
                patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        pool.submit.assert_not_called()

    def test_websocket_model_unhealthy_sends_warming_message(self):  
        """If the model API is not ready, the client receives api_warming status."""
        self.module.model_api.check_health.return_value = False
        ws = MagicMock()
        ws.receive.return_value = None
        with patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        warming = next((m for m in sent_msgs if m.get("status") == "api_warming"), None)
        self.assertIsNotNone(warming)

    def test_websocket_inference_result_sent_to_client(self):  
        """When a completed inference future returns a label, it is sent via ws.send.

        Strategy: build a pre-resolved inference Future, wire it so that
        exactly one landmark submit and one inference submit occur (the 17th
        frame is the disconnect signal), and verify that the label is echoed
        back to the client.
        """
        message = _frame_msg()

        # Landmark future resolves immediately with a valid PIL image
        lm_future: Future = Future()
        lm_future.set_result(Image.new("RGB", (224, 224)))

        # Inference future resolves immediately with a label
        inf_future: Future = Future()
        inf_future.set_result({"prediction": "hello", "confidence": 0.95})

        submitted = []

        def fake_submit(fn, *args):
            submitted.append(fn)
            if getattr(fn, "__name__", "") == "_run_inference":
                return inf_future
            return lm_future

        pool = MagicMock()
        pool.submit.side_effect = fake_submit

        # Enough frames to trigger buffer-full dispatch.
        # CLIP_LENGTH = 16 frames → we need at least 17 messages to see a
        # dispatch: the 1st 16 frames that pass the rate limiter fill the
        # buffer, the 17th triggers the dispatch, then None disconnects.
        # Monotonic values spaced 1 s apart so every frame clears the delay.
        monotonic_vals = list(range(1, 20))  # more than enough
        ws = MagicMock()
        ws.receive.side_effect = [message] * 18 + [None]

        with patch.object(self.module, "executor", pool), \
                patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                patch.object(self.module.time, "monotonic", side_effect=monotonic_vals), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)

        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        label_msgs = [m for m in sent_msgs if "label" in m]
        self.assertTrue(len(label_msgs) >= 1, f"No label sent; all messages: {sent_msgs}")
        self.assertEqual(label_msgs[0]["label"], "hello")
        self.assertAlmostEqual(label_msgs[0]["confidence"], 0.95)

    def test_websocket_disconnect_cancels_pending_inference_future(self):  
        """When the connection drops the pending inference future is cancelled."""
        pending_inference = MagicMock()
        pending_inference.done.return_value = False
        pool = MagicMock()
        pool.submit.return_value = pending_inference

        message = _frame_msg()
        ws = MagicMock()
        ws.receive.side_effect = [message, None]

        with patch.object(self.module, "executor", pool), \
                patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)

        pending_inference.cancel.assert_called()

    def test_websocket_hybrid_mode_config(self):  
        """'hybrid' is a valid inference mode and is acknowledged."""
        ws = MagicMock()
        ws.receive.side_effect = [
            json.dumps({"type": "config", "mode": "hybrid"}),
            None,
        ]
        with patch.object(self.module, "build_landmarkers", return_value=(None, None)), \
                self.module.app.test_request_context("/ws?token=valid"):
            self.module.websocket_translate(ws)
        sent_msgs = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        ack = next((m for m in sent_msgs if m.get("status") == "config_updated"), None)
        self.assertIsNotNone(ack)
        self.assertEqual(ack["mode"], "hybrid")


# FirebaseStartupTests

class FirebaseStartupTests(unittest.TestCase):

    def test_admin_initializes_with_backend_relative_credentials(self):
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        with patch.dict(sys.modules, {"firebase_admin": admin}), patch(
            "os.path.exists", return_value=False
        ):
            runpy.run_path(str(BACKEND / "firebase_admin_init.py"))
        admin.credentials.Certificate.assert_called_once_with(str(BACKEND / "firebase-admin.json"))
        admin.initialize_app.assert_called_once_with(admin.credentials.Certificate.return_value)

    def test_admin_uses_hosted_secret(self):
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        with patch.dict(sys.modules, {"firebase_admin": admin}), patch(
            "os.path.exists", return_value=True
        ):
            runpy.run_path(str(BACKEND / "firebase_admin_init.py"))
        admin.credentials.Certificate.assert_called_once_with("/etc/secrets/firebase-admin.json")

    def test_admin_reuses_existing_app(self):
        admin = MagicMock()
        with patch.dict(sys.modules, {"firebase_admin": admin}):
            runpy.run_path(str(BACKEND / "firebase_admin_init.py"))
        admin.initialize_app.assert_not_called()
        admin.credentials.Certificate.assert_not_called()

    def test_history_reuses_authentication_firebase_configuration(self):
        authentication = MagicMock()
        with patch.dict(sys.modules, {"authentication": authentication}):
            result = runpy.run_path(str(BACKEND / "history.py"))
        self.assertIs(result["db"], authentication.firebase.database.return_value)

    # TEST – firebase_admin_init path resolution

    def test_admin_initialize_app_passes_cert_object(self):  
        """initialize_app receives the return value of credentials.Certificate."""
        admin = MagicMock()
        admin.get_app.side_effect = ValueError("No default app")
        sentinel = object()
        admin.credentials.Certificate.return_value = sentinel
        with patch.dict(sys.modules, {"firebase_admin": admin}), \
                patch("os.path.exists", return_value=False):
            runpy.run_path(str(BACKEND / "firebase_admin_init.py"))
        admin.initialize_app.assert_called_once_with(sentinel)


# ISLModelAPITests – unit tests for model.py

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

    # predict_from_frames – validation

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
                patch("builtins.open", unittest.mock.mock_open(read_data=b"fake_mp4")), \
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
                patch("builtins.open", unittest.mock.mock_open(read_data=b"fake_mp4")), \
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
                patch("builtins.open", unittest.mock.mock_open(read_data=b"")), \
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


# AuthenticationHelperTests – unit tests for authentication.py functions

class AuthenticationHelperTests(unittest.TestCase):
    """Unit tests for register_account, login_account, forgot_password, email_verify."""

    def _load_auth_module(self, mock_auth_obj):
        """Load authentication.py with pyrebase and firebase.json replaced."""
        firebase_mock = MagicMock()
        firebase_mock.auth.return_value = mock_auth_obj
        pyrebase_mock = MagicMock()
        pyrebase_mock.initialize_app.return_value = firebase_mock

        fake_config = {"apiKey": "test", "authDomain": "test.firebaseapp.com"}
        spec = importlib.util.spec_from_file_location("auth_under_test", BACKEND / "authentication.py")
        mod = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pyrebase": pyrebase_mock}), \
                patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(fake_config))), \
                patch("os.path.exists", return_value=False):
            spec.loader.exec_module(mod)
        return mod

    def test_register_account_success(self):  
        mock_auth = MagicMock()
        mock_auth.create_user_with_email_and_password.return_value = {
            "localId": "uid1", "idToken": "tok1"
        }
        mod = self._load_auth_module(mock_auth)
        result = mod.register_account("a@b.com", "pw")
        self.assertEqual(result, {"id": "uid1", "token": "tok1"})
        mock_auth.create_user_with_email_and_password.assert_called_once_with("a@b.com", "pw")

    def test_register_account_exception_returns_none(self):  
        mock_auth = MagicMock()
        mock_auth.create_user_with_email_and_password.side_effect = Exception("email taken")
        mod = self._load_auth_module(mock_auth)
        result = mod.register_account("a@b.com", "pw")
        self.assertIsNone(result)

    def test_login_account_success(self):  
        mock_auth = MagicMock()
        mock_auth.sign_in_with_email_and_password.return_value = {
            "localId": "uid2", "idToken": "tok2"
        }
        mod = self._load_auth_module(mock_auth)
        result = mod.login_account("a@b.com", "pw")
        self.assertEqual(result, {"id": "uid2", "token": "tok2"})

    def test_login_account_exception_returns_none(self):  
        mock_auth = MagicMock()
        mock_auth.sign_in_with_email_and_password.side_effect = Exception("wrong password")
        mod = self._load_auth_module(mock_auth)
        result = mod.login_account("a@b.com", "wrong")
        self.assertIsNone(result)

    def test_forgot_password_success(self):  
        mock_auth = MagicMock()
        mod = self._load_auth_module(mock_auth)
        result = mod.forgot_password("a@b.com")
        self.assertEqual(result, "Password reset email sent successfully.")
        mock_auth.send_password_reset_email.assert_called_once_with("a@b.com")

    def test_forgot_password_exception_returns_empty_string(self):  
        mock_auth = MagicMock()
        mock_auth.send_password_reset_email.side_effect = Exception("user not found")
        mod = self._load_auth_module(mock_auth)
        result = mod.forgot_password("nobody@b.com")
        self.assertEqual(result, "")

    def test_email_verify_success(self):  
        mock_auth = MagicMock()
        mod = self._load_auth_module(mock_auth)
        result = mod.email_verify("id_token_123")
        self.assertEqual(result, "Email verification link sent successfully.")
        mock_auth.send_email_verification.assert_called_once_with("id_token_123")

    def test_email_verify_exception_returns_empty_string(self):  
        mock_auth = MagicMock()
        mock_auth.send_email_verification.side_effect = Exception("invalid token")
        mod = self._load_auth_module(mock_auth)
        result = mod.email_verify("bad_token")
        self.assertEqual(result, "")

    def test_authentication_uses_hosted_config_when_present(self):  
        """When /etc/secrets/firebase.json exists, it is used instead of the local one."""
        pyrebase_mock = MagicMock()
        firebase_mock = MagicMock()
        pyrebase_mock.initialize_app.return_value = firebase_mock
        fake_config = {"apiKey": "hosted"}
        spec = importlib.util.spec_from_file_location("auth_hosted", BACKEND / "authentication.py")
        mod = importlib.util.module_from_spec(spec)
        opened_paths = []

        def fake_open(path, *args, **kwargs):
            opened_paths.append(str(path))
            return unittest.mock.mock_open(read_data=json.dumps(fake_config))()

        with patch.dict(sys.modules, {"pyrebase": pyrebase_mock}), \
                patch("os.path.exists", return_value=True), \
                patch("builtins.open", side_effect=fake_open):
            spec.loader.exec_module(mod)
        self.assertTrue(any("/etc/secrets/firebase.json" in p for p in opened_paths))


if __name__ == "__main__":
    unittest.main()
