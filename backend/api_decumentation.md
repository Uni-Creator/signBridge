# SignBridge API Documentation

> **API REQUEST HANDLERS:** `main.py`, `authentication.py`, `history.py`, `websocket_handler.py`, `websocket_processing.py`, and the `postman/collections/sign_bridge`.

---

## Table of Contents

1. [Overview](#overview)
2. [Base URLs & Collection Variables](#base-urls--collection-variables)
3. [Authentication](#authentication)
4. [Rate Limits](#rate-limits)
5. [Error Response Format](#error-response-format)
6. [REST Endpoints](#rest-endpoints)
   - [Health Check](#1-health-check)
   - [Register](#2-register)
   - [Login](#3-login)
   - [Logout](#4-logout)
   - [Forgot Password](#5-forgot-password)
   - [Update Password](#6-update-password)
   - [Get Translation History](#7-get-translation-history)
   - [Store Translation](#8-store-translation)
   - [Delete Translation](#9-delete-translation)
   - [Clear History](#10-clear-history)
7. [WebSocket API : Live Translation](#websocket-api--live-translation)
   - [Connection](#connection)
   - [Authentication](#websocket-authentication)
   - [Server Messages After Connect](#server-messages-after-connect)
   - [Configuration Messages (Client → Server)](#configuration-messages-client--server)
   - [Frame Messages (Client → Server)](#frame-messages-client--server)
   - [Prediction Response (Server → Client)](#prediction-response-server--client)
   - [Error Messages (Server → Client)](#error-messages-server--client)
   - [Disconnect](#disconnect)
8. [Getting Started : Practical Sequence](#getting-started--practical-sequence)
9. [Postman Collection Variables](#postman-collection-variables)
10. [Collection Coverage](#collection-coverage)
11. [Security Notes](#security-notes)
12. [Running the Backend Locally](#running-the-backend-locally)

---

## Overview

**SignBridge** is a Python/Flask backend that provides:

- **REST APIs** for user authentication (Firebase) and translation history (Firebase Realtime Database).
- **WebSocket API** (`/ws`) for real-time Indian Sign Language (ISL) recognition : the client streams camera frames and the server returns predicted sign labels.

The backend is built with:

| Component | Library |
|---|---|
| HTTP server | Flask 3.1 |
| WebSocket | flask-sock 0.7 |
| Auth | Firebase Admin SDK 7.5 |
| Validation | Pydantic 2 |
| Rate limiting | Flask-Limiter 4.1 |
| ML pipeline | MediaPipe 0.10 + remote ISL model API |
| Runtime | Python 3.10 |

---

## Base URLs & Collection Variables

| Context | REST base URL | WebSocket base URL |
|---|---|---|
| Local development | `http://localhost:5000` | `ws://localhost:5000` |
| Physical device (same LAN) | `http://<SERVER_IP>:5000` | `ws://<SERVER_IP>:5000` |
| Android emulator | `http://10.0.2.2:5000` | `ws://10.0.2.2:5000` |
| Production (Render / GCP) | Set via `PORT` env var (default `10000` for Gunicorn) | Same host, `wss://` |

The Postman collection uses two variables:

| Variable | Purpose | Example value |
|---|---|---|
| `{{base_url}}` | REST base URL (no trailing slash) | `http://localhost:5000` |
| `{{live_url}}` | WebSocket base URL (no trailing slash) | `ws://localhost:5000` |

Set both variables in your environment or directly in the collection before running requests.

---

## Authentication

SignBridge uses **Firebase Authentication**. The backend never trusts a user-supplied ID : it always derives the user identity from a verified Firebase ID token.

### Protected REST endpoints

Add the following header to every protected request:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

The backend verifies the token with Firebase Admin SDK (`check_revoked=True`) and extracts `decoded["uid"]` as the request's user identity.

**Token error responses:**

| Condition | Status | Body |
|---|---|---|
| Header missing or not `Bearer …` | `401` | `{"error": "Missing or invalid token"}` |
| Token empty | `401` | `{"error": "Missing or invalid token"}` |
| Token expired | `401` | `{"error": "Token expired"}` |
| Token revoked | `401` | `{"error": "Token revoked"}` |
| Any other invalid token | `401` | `{"error": "Invalid token"}` |

### WebSocket endpoint

The token is passed as a query parameter (see [WebSocket Authentication](#websocket-authentication)).

---

## Rate Limits

Global defaults (all endpoints):

```
100 requests / minute
5 000 requests / day
```

Per-endpoint overrides:

| Endpoint | Limit | Error message |
|---|---|---|
| `POST /register` | 5 / min | `Too many registration attempts. Please try again later.` |
| `POST /login` | 5 / min | `Too many login attempts. Please try again later.` |
| `POST /logout` | 10 / min | `Too many logout requests. Please try again later.` |
| `POST /forgot-password` | 3 / min | `Too many forgot password attempts. Please try again later.` |
| `POST /update-password` | 3 / min | `Too many password update attempts. Please try again later.` |
| `GET /history` | 20 / min | `Too many history requests. Please try again later.` |
| `POST /history/store` | 50 / min | `Too many store requests. Please try again later.` |
| `DELETE /history/<id>` | 50 / min | `Too many delete history requests. Please try again later.` |
| `DELETE /history/clear` | 10 / min | `Too many clear requests. Please try again later.` |

When a limit is exceeded the server returns:

```http
429 Too Many Requests
```

```json
{
  "error": "<rate-limit error message>"
}
```

---

## Error Response Format

All REST error responses are JSON objects with an `error` key:

```json
{
  "error": "<human-readable message>"
}
```

Validation errors additionally include a `details` array:

```json
{
  "error": "Invalid request body",
  "details": [
    {
      "field": "email",
      "message": "value is not a valid email address"
    }
  ]
}
```

---

## REST Endpoints

---

### 1. Health Check

**Collection request:** *(not in collection : backend-only endpoint)*

```
GET /
```

Confirms the server is running. No authentication required.

**Response : 200 OK**

```json
{
  "message": "SignBridge API is running",
  "version": "2.0"
}
```

---

### 2. Register

**Collection request:** `register`

```
POST /register
```

Creates a new Firebase user account and returns a Firebase ID token.

**Authentication:** Not required.

**Request headers:**

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

| Field | Type | Constraints |
|---|---|---|
| `email` | string | Required. Valid email format. |
| `password` | string | Required. 6–128 characters. |

Extra fields are rejected (`extra="forbid"`).

**Response : 200 OK**

```json
{
  "id": "eF3JZDs8xjSv9hyzCt8D7qgRILC3",
  "token": "<FIREBASE_ID_TOKEN>"
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Firebase UID |
| `token` | string | Firebase ID token (use as Bearer token) |

**Response : 400 Bad Request** *(registration failed, e.g. email already exists)*

```json
{
  "id": "",
  "token": "",
  "error": "Registration failed"
}
```

**Response : 400 Bad Request** *(validation error)*

```json
{
  "error": "Invalid request body",
  "details": [{ "field": "email", "message": "..." }]
}
```

**Response : 429 Too Many Requests**

```json
{
  "error": "Too many registration attempts. Please try again later."
}
```

**Postman automation:** After a successful response the collection's `afterResponse` script automatically sets `{{user_id}}` and `{{auth_token}}` collection variables.

---

### 3. Login

**Collection request:** `login`

```
POST /login
```

Authenticates an existing Firebase user and returns a fresh ID token.

**Authentication:** Not required.

**Request headers:**

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

| Field | Type | Constraints |
|---|---|---|
| `email` | string | Required. Valid email format. |
| `password` | string | Required. 6–128 characters. |

**Response : 200 OK**

```json
{
  "id": "eF3JZDs8xjSv9hyzCt8D7qgRILC3",
  "token": "<FIREBASE_ID_TOKEN>"
}
```

**Response : 400 Bad Request**

```json
{
  "id": "",
  "token": "",
  "error": "Login failed"
}
```

**Response : 429 Too Many Requests**

```json
{
  "error": "Too many login attempts. Please try again later."
}
```

**Postman automation:** Same as Register : sets `{{user_id}}` and `{{auth_token}}`.

---

### 4. Logout

**Collection request:** `logout`

```
POST /logout
```

Revokes all refresh tokens for the authenticated user (Firebase Admin `revoke_refresh_tokens`). Existing ID tokens remain valid until they expire (typically 1 hour).

**Authentication:** Required : Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Request body:** None.

**Response : 200 OK**

```json
{
  "message": "Logged out successfully"
}
```

**Response : 401 Unauthorized** *(invalid/missing token)*

```json
{
  "error": "Missing or invalid token"
}
```

**Response : 500 Internal Server Error**

```json
{
  "error": "Logout failed"
}
```

---

### 5. Forgot Password

**Collection request:** `forgot-password`

```
POST /forgot-password
```

Sends a Firebase password-reset email to the given address. The endpoint always returns a success response regardless of whether the email exists, to prevent account enumeration.

**Authentication:** Not required.

**Request headers:**

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

**Request body:**

```json
{
  "email": "user@example.com"
}
```

| Field | Type | Constraints |
|---|---|---|
| `email` | string | Required. Valid email format. |

**Response : 200 OK**

```json
{
  "success": "Password reset email has been sent."
}
```

**Response : 429 Too Many Requests**

```json
{
  "error": "Too many forgot password attempts. Please try again later."
}
```

---

### 6. Update Password

**Collection request:** `update-password`

```
POST /update-password
```

Updates the password for the authenticated user (Firebase Admin `update_user`).

**Authentication:** Required : Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |
| `Content-Type` | `application/json` |

**Request body:**

```json
{
  "password": "newSecurePassword1"
}
```

| Field | Type | Constraints |
|---|---|---|
| `password` | string | Required. 6–128 characters. New password. |

**Response : 200 OK**

```json
{
  "success": "Password has been updated."
}
```

**Response : 401 Unauthorized**

```json
{
  "error": "Missing or invalid token"
}
```

**Response : 500 Internal Server Error**

```json
{
  "error": "Password update failed."
}
```

**Postman automation:** On success the `afterResponse` script copies `{{new_pass}}` into `{{password}}` so subsequent login requests use the updated credential.

---

### 7. Get Translation History

**Collection request:** `get history`

```
GET /history
```

Returns the authenticated user's translation history, sorted newest-first.

**Authentication:** Required : Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Query parameters:** None. Do not supply a user ID : the backend derives it from the token.

**Response : 200 OK**

```json
{
  "history": [
    {
      "id": "-P1zadqpuQu6dB7DPS9s",
      "translation": "Something about you",
      "timestamp": "2026-09-20T21:46:16.286476"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `history` | array | List of translation items, newest first. Empty array when no history exists. |
| `history[].id` | string | Firebase Realtime Database push key. |
| `history[].translation` | string | The stored sign translation text. |
| `history[].timestamp` | string | ISO 8601 datetime (local server time). |

**Response : 401 Unauthorized**

```json
{
  "error": "Missing or invalid token"
}
```

**Response : 500 Internal Server Error**

```json
{
  "history": "",
  "error": "Failed to retrieve history"
}
```

---

### 8. Store Translation

**Collection request:** `store translation`

```
POST /history/store
```

Saves a translation string to the authenticated user's history in Firebase Realtime Database.

**Authentication:** Required : Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |
| `Content-Type` | `application/json` |

**Request body:**

```json
{
  "translation": "Something about you"
}
```

| Field | Type | Constraints |
|---|---|---|
| `translation` | string | Required. 1–5 000 characters. |

**Response : 201 Created**

```json
{
  "id": "-P1zaRcQPKjyAnr-pAM2",
  "timestamp": "2026-09-20T21:45:22.116040",
  "translation": "Something about you"
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Firebase push key for this item. |
| `timestamp` | string | ISO 8601 datetime. |
| `translation` | string | The stored text. |

**Response : 400 Bad Request** *(validation error)*

```json
{
  "error": "Invalid request body",
  "details": [{ "field": "translation", "message": "..." }]
}
```

**Response : 500 Internal Server Error**

```json
{
  "error": "Failed to store history"
}
```

**Postman automation:** The `afterResponse` script sets `{{translation_id}}` to the returned `id`, enabling the Delete Translation request to run immediately after.

---

### 9. Delete Translation

**Collection request:** `delete translation`

```
DELETE /history/{{translation_id}}
```

Deletes a single translation item from the authenticated user's history.

**Authentication:** Required : Bearer token.

**Path variable:**

| Variable | Description | Example |
|---|---|---|
| `translation_id` | Firebase push key of the item to delete | `-P1zaRcQPKjyAnr-pAM2` |

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Response : 200 OK**

```json
{
  "message": "Translation deleted"
}
```

**Response : 404 Not Found** *(item does not exist or belongs to a different user)*

```json
{
  "error": "Translation not found"
}
```

**Response : 500 Internal Server Error**

```json
{
  "error": "Failed to delete history"
}
```

**Postman automation:** The `afterResponse` script resets `{{translation_id}}` to an empty string.

---

### 10. Clear History

**Collection request:** `clear history`

```
DELETE /history/clear
```

Deletes **all** translation history for the authenticated user.

**Authentication:** Required : Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Request body:** None.

**Response : 200 OK**

```json
{
  "message": "History deleted"
}
```

**Response : 404 Not Found** *(no history exists)*

```json
{
  "error": "No history found"
}
```

**Response : 500 Internal Server Error**

```json
{
  "error": "Failed to delete history"
}
```

---

## WebSocket API : Live Translation

**Collection request:** `live translation`

---

### Connection

```
WS  {{live_url}}/ws?token={{auth_token}}
WSS <production-host>/ws?token=<FIREBASE_ID_TOKEN>
```

The Firebase ID token is passed as the `token` query parameter.

> **Security note:** Query parameters can appear in server logs and browser history. In production, prefer `wss://` and consider a short-lived WebSocket-specific token rather than the long-lived Firebase ID token.

---

### WebSocket Authentication

On connection the server immediately verifies the `token` query parameter using Firebase Admin SDK.

**If authentication fails:**

```json
{
  "error": "Unauthorized"
}
```

The connection is then closed (code `1000 Normal Closure`).

---

### Server Messages After Connect

After successful authentication the server sends one or more status messages:

**Always sent:**

```json
{
  "status": "connected",
  "message": "Ready for frames"
}
```

**Sent when MediaPipe is unavailable** (memory-constrained server):

```json
{
  "status": "info",
  "message": "Landmarks disabled on this server (memory limit). Accuracy may be lower."
}
```

**Sent when the remote model API is still warming up:**

```json
{
  "status": "api_warming",
  "message": "Model API warming up, please wait..."
}
```

---

### Configuration Messages (Client → Server)

The client can change the inference mode at any time during the session:

```json
{
  "type": "config",
  "mode": "frames"
}
```

| Field | Type | Values |
|---|---|---|
| `type` | string | Must be `"config"` |
| `mode` | string | `"frames"` · `"video"` · `"hybrid"` |

**Server acknowledgement:**

```json
{
  "status": "config_updated",
  "mode": "frames"
}
```

The default mode on connection is `"frames"`.

---

### Frame Messages (Client → Server)

Send camera frames as JSON with a Base64-encoded image:

```json
{
  "frame": "<BASE64_ENCODED_IMAGE>"
}
```

| Field | Type | Description |
|---|---|---|
| `frame` | string | Base64-encoded image (JPEG or PNG). |

**Server-side frame processing pipeline:**

1. Parse JSON and extract `frame`.
2. Base64-decode the payload.
3. Decode the image bytes into a PIL RGB image.
4. Apply MediaPipe pose + hand landmark detection (when enabled).
5. Resize the processed frame to **224 × 224** pixels.
6. Append to a rolling frame buffer (capacity: **16 frames**).
7. When the buffer reaches 16 frames, submit them to the remote ISL model API.
8. Clear the buffer and await the inference result.
9. Send the prediction back to the client.

**Frame rate limiting:**

The server enforces a minimum inter-frame interval of **0.08 s** (~12.5 frames/second). Frames arriving faster than this are silently discarded.

**If a frame cannot be decoded:**

```json
{
  "error": "Invalid frame"
}
```

The connection remains open; the client can continue sending frames.

---

### Prediction Response (Server → Client)

When inference completes and a sign is recognised:

```json
{
  "label": "hello",
  "confidence": 0.95
}
```

| Field | Type | Description |
|---|---|---|
| `label` | string | Predicted ISL sign label. |
| `confidence` | number (float 0–1) | Model confidence score. |

After a prediction is sent the frame buffer is cleared and the cycle restarts.

---

### Error Messages (Server → Client)

| Message | Meaning |
|---|---|
| `{"error": "Unauthorized"}` | Token missing or invalid : connection will close. |
| `{"error": "Invalid frame"}` | Frame could not be decoded : connection stays open. |

---

### Disconnect

The server closes the connection (code `1000 Normal Closure`) when:

- Authentication fails.
- The client closes the connection.
- No message is received for **30 seconds** (receive timeout).
- An unhandled exception occurs in the connection loop.

On disconnect the server cancels any pending inference futures, closes MediaPipe detectors, and clears the frame buffer.

---

## Getting Started : Practical Sequence

Follow these steps to go from zero to a live sign-language translation session using the Postman collection.

### Step 1 : Set collection variables

In the `sign_bridge` collection, set:

| Variable | Value |
|---|---|
| `base_url` | `http://localhost:5000` (or your server address) |
| `live_url` | `ws://localhost:5000` |
| `email` | `"your-test-email@example.com"` *(include the quotes : the request body uses `{{email}}` unquoted)* |
| `password` | `"yourPassword123"` |

### Step 2 : Register a new account

Send **`POST /register`**.

On success `{{user_id}}` and `{{auth_token}}` are set automatically by the collection script.

### Step 3 : (Optional) Log in with an existing account

Send **`POST /login`** if you already have an account. This also sets `{{auth_token}}`.

### Step 4 : Store a translation

Set `{{translation}}` to `"hello"` (include quotes), then send **`POST /history/store`**.

`{{translation_id}}` is set automatically.

### Step 5 : Retrieve history

Send **`GET /history`** to confirm the stored item appears.

### Step 6 : Delete the translation

Send **`DELETE /history/{{translation_id}}`** to remove the item.

### Step 7 : Connect to the WebSocket

Open the **`live translation`** WebSocket request. The URL is pre-filled as `{{live_url}}/ws?token={{auth_token}}`.

Click **Connect**. You should receive:

```json
{"status": "connected", "message": "Ready for frames"}
```

### Step 8 : Send frames

Send Base64-encoded camera frames using the message format:

```json
{"frame": "<BASE64_IMAGE>"}
```

After every 16 accepted frames the server returns a prediction:

```json
{"label": "hello", "confidence": 0.92}
```

### Step 9 : Update password (optional)

Set `{{new_pass}}` to `"newPassword456"` (with quotes), then send **`POST /update-password`**.

### Step 10 : Log out

Send **`POST /logout`** to revoke refresh tokens.

---

## Postman Collection Variables

The `sign_bridge` collection defines the following variables (all start empty : set them before running):

| Variable | Set by | Used by |
|---|---|---|
| `base_url` | User | All REST requests |
| `live_url` | User | `live translation` WebSocket |
| `email` | User | `register`, `login`, `forgot-password` |
| `password` | User / `update-password` script | `register`, `login` |
| `new_pass` | User | `update-password` |
| `translation` | User | `store translation` |
| `translation_id` | `store translation` script | `delete translation` |
| `user_id` | `register` / `login` script | Reference only |
| `auth_token` | `register` / `login` script | All protected requests |

---

## Security Notes

| Area | Status |
|---|---|
| Firebase REST authentication | ✅ Implemented |
| Firebase UID-based authorization | ✅ Implemented |
| History IDOR protection | ✅ Implemented at API layer |
| REST rate limiting | ✅ Implemented |
| Input validation (Pydantic) | ✅ Implemented |
| Generic auth error messages | ✅ Implemented |
| WebSocket authentication | ✅ Implemented |
| WebSocket frame throttling | ✅ Implemented |
| WebSocket payload-size limit | ⚠️ Not implemented |
| Image dimension/pixel limit | ⚠️ Not implemented |
| WebSocket connection limit | ⚠️ Not implemented |
| Token in WebSocket URL | ⚠️ Needs improvement (see note above) |
| HTTPS / WSS enforcement | Deployment-dependent |
| Firebase Database Rules | Not verified in this repo |
| Distributed rate-limit storage | Not verified (in-memory default) |

Do not expose `firebase.json`, `firebase-admin.json`, or any Firebase credentials in version control or client-side assets.

---

## Running the Backend Locally

```bash
# 1. Python 3.10 or 3.11 required
python --version

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate          # Linux / macOS
# .\venv\Scripts\activate         # Windows PowerShell

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure Firebase
#    - Place your Firebase web config in backend/firebase.json
#    - Place your service-account key in backend/firebase-admin.json
#    (See backend_run_guide.md for the firebase.json template)

# 5. Start the server
python main.py
# REST API  → http://127.0.0.1:5000/
# WebSocket → ws://127.0.0.1:5000/ws

# 6. Run offline regression tests (no credentials needed)
python -m unittest discover -s tests -v
```

For production deployments the server is configured for **Gunicorn** (`gunicorn.conf.py`):

```
worker_class = gthread
workers      = 1
threads      = 4
timeout      = 120
bind         = 0.0.0.0:<PORT>   # PORT env var, default 10000
```

Google App Engine deployment is configured in `app.yaml` (Python 3.9 runtime, all routes → `main.app`).
