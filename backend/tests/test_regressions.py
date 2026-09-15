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
from unittest.mock import MagicMock, patch

import flask  # Load real runtime dependencies before temporarily replacing modules.
import numpy
from PIL import Image
from dotenv import load_dotenv

# Load environment variables from .env file for local testing
load_dotenv()

BACKEND = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
