"""
Ordered-flow helper functions for the live E2E suite.

These are plain functions, not tests. The live suite exercises one
ordered, self-contained lifecycle on a freshly generated throwaway
account (register -> login -> authenticated calls -> logout ->
revocation check -> re-login -> websocket), and TestCase methods
shouldn't rely on each other's execution order. So the ordered scenario
lives in a single test method
(test_live_e2e.py::TestLiveE2E.test_complete_live_backend_flow) that
calls these helpers in sequence; each helper makes one HTTP call plus the
assertions that make sense immediately after it, and returns whatever the
next step needs.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class LiveTestState:
    """Mutable state threaded through the ordered live scenario.

    email/password/new_password are set up front (see unique_email() in
    live_config.py); everything else is filled in as the scenario runs:

        register()                 -> uid
        login(email, password)     -> token            (JWT #1)
        update_password(new_password)
        ... history ops using token (JWT #1) ...
        logout(token)               -> old_token
        login(email, new_password) -> new_token         (JWT #2)
        websocket using new_token
    """

    email: str
    password: str
    new_password: str
    token: str | None = None
    old_token: str | None = None
    new_token: str | None = None
    uid: str | None = None
    history_id: str | None = None


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str) -> dict:
    resp = client.post("/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body.get("token"), f"login response missing token: {body}"
    assert body.get("id"), f"login response missing id: {body}"
    return body


def register(client, email: str, password: str) -> dict:
    resp = client.post("/register", json={"email": email, "password": password})
    assert resp.status_code == 200, f"register failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body.get("token"), f"register response missing token: {body}"
    assert body.get("id"), f"register response missing id: {body}"
    return body


def forgot_password(client, email: str) -> None:
    resp = client.post("/forgot-password", json={"email": email})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": "Password reset email has been sent."}


def update_password(client, token: str, new_password: str) -> None:
    resp = client.post(
        "/update-password",
        json={"password": new_password},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, f"update-password failed: {resp.status_code} {resp.text}"
    assert resp.json() == {"success": "Password has been updated."}


def logout(client, token: str) -> None:
    resp = client.post("/logout", headers=auth_headers(token))
    assert resp.status_code == 200, f"logout failed: {resp.status_code} {resp.text}"
    assert resp.json() == {"message": "Logged out successfully"}


def assert_token_rejected(client, token: str) -> None:
    """A revoked/expired token must be rejected on a protected route."""
    resp = client.get("/history", headers=auth_headers(token))
    assert resp.status_code == 401, (
        f"expected the logged-out token to be rejected, got "
        f"{resp.status_code}: {resp.text}"
    )


def get_history(client, token: str) -> list:
    resp = client.get("/history", headers=auth_headers(token))
    assert resp.status_code == 200, f"get history failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "history" in body
    return body["history"]


def store_translation(client, token: str, text: str) -> dict:
    resp = client.post(
        "/history/store",
        json={"translation": text},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, f"store history failed: {resp.status_code} {resp.text}"
    item = resp.json()
    assert item.get("translation") == text
    assert item.get("id")
    return item


def delete_translation(client, token: str, translation_id: str) -> None:
    resp = client.delete(f"/history/{translation_id}", headers=auth_headers(token))
    assert resp.status_code == 200, f"delete history failed: {resp.status_code} {resp.text}"
    assert resp.json() == {"message": "Translation deleted"}


def clear_history(client, token: str) -> None:
    resp = client.delete("/history/clear", headers=auth_headers(token))
    assert resp.status_code in (200, 404), (
        f"clear history failed unexpectedly: {resp.status_code} {resp.text}"
    )


def get_slt_deep_health(client, token: str) -> dict:
    resp = client.get("/slt/health/deep", headers=auth_headers(token))
    assert resp.status_code in (200, 503), (
        f"unexpected status from /slt/health/deep: {resp.status_code} {resp.text}"
    )
    return resp.json()
