# SignBridge API Documentation

> **API REQUEST HANDLERS:** `main.py`, `authentication.py`, `history.py`, `websocket_handler.py`, `websocket_processing.py`, `model.py`, `frame_codec.py`, and the `postman/collections/sign_bridge`.

---

## Table of Contents

1. [Overview](#overview)
2. [Base URLs & Collection Variables](#base-urls--collection-variables)
3. [Authentication](#authentication)
4. [Rate Limits](#rate-limits)
5. [Error Response Format](#error-response-format)
6. [REST Endpoints](#rest-endpoints)
   - [Root](#1-root)
   - [Health Check](#2-health-check)
   - [Deep Health Check](#3-deep-health-check)
   - [Register](#4-register)
   - [Login](#5-login)
   - [Logout](#6-logout)
   - [Forgot Password](#7-forgot-password)
   - [Update Password](#8-update-password)
   - [Get Translation History](#9-get-translation-history)
   - [Store Translation](#10-store-translation)
   - [Delete Translation](#11-delete-translation)
   - [Clear History](#12-clear-history)
   - [SLT Health / Deep Health](#13-slt-health--deep-health)
   - [SLP Health / Deep Health](#14-slp-health--deep-health)
7. [WebSocket API: Live Translation (SLT)](#websocket-api-live-translation-slt)
   - [Connection](#connection)
   - [Authentication](#websocket-authentication)
   - [Server Messages After Connect](#server-messages-after-connect)
   - [Configuration Handshake](#configuration-handshake-client--server)
   - [Frame Messages (Client → Server)](#frame-messages-client--server)
   - [Prediction Response (Server → Client)](#prediction-response-server--client)
   - [End of Stream](#end-of-stream)
   - [Error Messages](#error-messages-server--client)
   - [Disconnect](#disconnect)
8. [Internal Backend → Model API Contract (ISLF)](#internal-backend--model-api-contract-islf)
9. [WebSocket API: Sign Production (SLP)](#websocket-api-sign-production-slp)
10. [Getting Started: Practical Sequence](#getting-started-practical-sequence)
11. [Postman Collection Variables](#postman-collection-variables)
12. [Security Notes](#security-notes)
13. [Running the Backend Locally](#running-the-backend-locally)

---

## Overview

**SignBridge** is a Python backend, now built on **FastAPI**, that provides:

- **REST APIs** for user authentication (Firebase) and translation history (Firebase Realtime Database).
- **WebSocket APIs** for real-time Indian Sign Language (ISL) work, split into two namespaces:
  - **`/slt/ws`** — Sign Language **Translation**: client streams camera frames, server returns predicted sign labels.
  - **`/slp/ws`** — Sign Language **Production**: reserved for the text-to-3D-pose-sequence pipeline (endpoints currently stubbed).

The backend is built with:

| Component | Library |
|---|---|
| HTTP server | FastAPI |
| WebSocket | Native FastAPI / ASGI WebSocket |
| Auth | Firebase Admin SDK 7.5 |
| Validation | Pydantic 2 |
| Rate limiting | SlowAPI |
| ML pipeline | MediaPipe 0.10 + remote ISL model API |
| Runtime | Python 3.10–3.12 |

> **Migration note:** the backend has moved from Flask + flask-sock + flask-limiter to FastAPI + native ASGI WebSockets + SlowAPI. The live-translation WebSocket route has moved from `/ws` to `/slt/ws`, and WebSocket authentication has moved from a query-string token to an `Authorization: Bearer` header sent during the handshake.

---

## Base URLs & Collection Variables

| Context | REST base URL | WebSocket base URL |
|---|---|---|
| Local development | `http://localhost:5000` | `ws://localhost:5000` |
| Physical device (same LAN) | `http://<SERVER_IP>:5000` | `ws://<SERVER_IP>:5000` |
| Android emulator | `http://10.0.2.2:5000` | `ws://10.0.2.2:5000` |
| Production (Render) | `https://<render-host>` | `wss://<render-host>` |

For production on Render, HTTPS/WSS TLS is provided by Render. The application itself does not need to terminate public TLS.

The Postman collection uses two variables:

| Variable | Purpose | Example value |
|---|---|---|
| `{{base_url}}` | REST base URL (no trailing slash) | `http://localhost:5000` |
| `{{live_url}}` | WebSocket base URL (no trailing slash) | `ws://localhost:5000` |

Set both variables in your environment or directly in the collection before running requests.

---

## Authentication

SignBridge uses **Firebase Authentication**. The backend never trusts a user-supplied ID: it always derives the user identity from a verified Firebase ID token.

### Protected REST endpoints

Add the following header to every protected request:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

The backend verifies the token with the Firebase Admin SDK (`verify_id_token(..., check_revoked=True)`) via the `require_auth` dependency, and extracts `decoded["uid"]` as the request's user identity. That uid is also stored on `request.state.user_id` and used as the rate-limit key for authenticated routes.

**Token error responses:**

| Condition | Status | Body |
|---|---|---|
| Header missing or not `Bearer …` | `401` | `{"error": "Missing or invalid token"}` |
| Token empty | `401` | `{"error": "Missing or invalid token"}` |
| Token expired | `401` | `{"error": "Token expired"}` |
| Token revoked | `401` | `{"error": "Token revoked"}` |
| Any other invalid token | `401` | `{"error": "Invalid token"}` |

### WebSocket endpoints

**The token is now passed as an `Authorization: Bearer <token>` header during the WebSocket handshake — not as a query parameter.** See [WebSocket Authentication](#websocket-authentication).

---

## Rate Limits

Global defaults (all endpoints), keyed by authenticated Firebase uid where available, falling back to remote address:

```
100 requests / minute
5 000 requests / day
```

Per-endpoint overrides:

| Endpoint | Limit | Error message |
|---|---|---|
| `GET /` | 20 / min | `Too many requests. Please try again later.` |
| `GET /health` | 10 / min | `Too many requests. Please try again later.` |
| `GET /health/deep` | 5 / min | `Too many requests. Please try again later.` |
| `POST /register` | 5 / min | `Too many registration attempts. Please try again later.` |
| `POST /login` | 5 / min | `Too many login attempts. Please try again later.` |
| `POST /logout` | 10 / min | `Too many logout requests. Please try again later.` |
| `POST /forgot-password` | 3 / min | `Too many forgot password attempts. Please try again later.` |
| `POST /update-password` | 3 / min | `Too many password update attempts. Please try again later.` |
| `GET /history` | 20 / min | `Too many history requests. Please try again later.` |
| `POST /history/store` | 50 / min | `Too many store requests. Please try again later.` |
| `DELETE /history/<id>` | 50 / min | `Too many delete history requests. Please try again later.` |
| `DELETE /history/clear` | 10 / min | `Too many clear requests. Please try again later.` |
| `GET /slt/health` | 5 / min | `Too many model requests. Please try again later.` |
| `GET /slt/health/deep` | 5 / min | `Too many model requests. Please try again later.` |
| `GET /slp/health` | 5 / min | `Too many model requests. Please try again later.` |
| `GET /slp/health/deep` | 5 / min | `Too many model requests. Please try again later.` |

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

### 1. Root

```
GET /
```

Confirms the server is running. No authentication required.

**Response: 200 OK**

```json
{
  "message": "SignBridge API is running",
  "version": "2.0"
}
```

---

### 2. Health Check

**Collection request:** *(not in collection — infra/container health check)*

```
GET /health
```

Lightweight liveness check. Does **not** depend on the database or the remote model server, so it is the endpoint container/platform health checks should target.

**Response: 200 OK**

```json
{
  "status": "ok"
}
```

---

### 3. Deep Health Check

```
GET /health/deep
```

Checks both the Firebase-backed database and the remote ISL model server, and reports a combined status.

**Response: 200 OK**

```json
{
  "status": "ok",
  "database": "ok",
  "model_server": { "...": "model API deep-health payload" }
}
```

**Response: 503 Service Unavailable** *(database or model server not ok)*

```json
{
  "status": "degraded",
  "database": "ok",
  "model_server": "error"
}
```

---

### 4. Register

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

**Response: 200 OK**

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

**Response: 400 Bad Request** *(registration failed, e.g. email already exists)*

```json
{
  "id": "",
  "token": "",
  "error": "Registration failed"
}
```

**Response: 400 Bad Request** *(validation error)*

```json
{
  "error": "Invalid request body",
  "details": [{ "field": "email", "message": "..." }]
}
```

**Response: 429 Too Many Requests**

```json
{
  "error": "Too many registration attempts. Please try again later."
}
```

**Postman automation:** After a successful response the collection's `afterResponse` script automatically sets `{{user_id}}` and `{{auth_token}}` collection variables.

---

### 5. Login

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

**Response: 200 OK**

```json
{
  "id": "eF3JZDs8xjSv9hyzCt8D7qgRILC3",
  "token": "<FIREBASE_ID_TOKEN>"
}
```

**Response: 400 Bad Request**

```json
{
  "id": "",
  "token": "",
  "error": "Login failed"
}
```

**Response: 429 Too Many Requests**

```json
{
  "error": "Too many login attempts. Please try again later."
}
```

**Postman automation:** Same as Register: sets `{{user_id}}` and `{{auth_token}}`.

---

### 6. Logout

**Collection request:** `logout`

```
POST /logout
```

Revokes all refresh tokens for the authenticated user (Firebase Admin `revoke_refresh_tokens`). Existing ID tokens remain valid until they expire (typically 1 hour).

**Authentication:** Required — Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Request body:** None.

**Response: 200 OK**

```json
{
  "message": "Logged out successfully"
}
```

**Response: 401 Unauthorized**

```json
{
  "error": "Missing or invalid token"
}
```

**Response: 500 Internal Server Error**

```json
{
  "error": "Logout failed"
}
```

---

### 7. Forgot Password

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

**Response: 200 OK**

```json
{
  "success": "Password reset email has been sent."
}
```

**Response: 429 Too Many Requests**

```json
{
  "error": "Too many forgot password attempts. Please try again later."
}
```

---

### 8. Update Password

**Collection request:** `update-password`

```
POST /update-password
```

Updates the password for the authenticated user (Firebase Admin `update_user`), then revokes existing refresh tokens.

**Authentication:** Required — Bearer token.

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

**Response: 200 OK**

```json
{
  "success": "Password has been updated."
}
```

**Response: 401 Unauthorized**

```json
{
  "error": "Missing or invalid token"
}
```

**Response: 500 Internal Server Error**

```json
{
  "error": "Password update failed."
}
```

**Postman automation:** On success the `afterResponse` script copies `{{new_pass}}` into `{{password}}` so subsequent login requests use the updated credential.

---

### 9. Get Translation History

**Collection request:** `get history`

```
GET /history
```

Returns the authenticated user's translation history, sorted newest-first.

**Authentication:** Required — Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Query parameters:** None. Do not supply a user ID: the backend derives it from the token.

**Response: 200 OK**

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

**Response: 401 Unauthorized**

```json
{
  "error": "Missing or invalid token"
}
```

**Response: 500 Internal Server Error**

```json
{
  "history": "",
  "error": "Failed to retrieve history"
}
```

---

### 10. Store Translation

**Collection request:** `store translation`

```
POST /history/store
```

Saves a translation string to the authenticated user's history in Firebase Realtime Database.

**Authentication:** Required — Bearer token.

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

**Response: 201 Created**

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

**Response: 400 Bad Request** *(validation error)*

```json
{
  "error": "Invalid request body",
  "details": [{ "field": "translation", "message": "..." }]
}
```

**Response: 500 Internal Server Error**

```json
{
  "error": "Failed to store history"
}
```

**Postman automation:** The `afterResponse` script sets `{{translation_id}}` to the returned `id`, enabling the Delete Translation request to run immediately after.

---

### 11. Delete Translation

**Collection request:** `delete translation`

```
DELETE /history/{{translation_id}}
```

Deletes a single translation item from the authenticated user's history.

**Authentication:** Required — Bearer token.

**Path variable:**

| Variable | Description | Example |
|---|---|---|
| `translation_id` | Firebase push key of the item to delete | `-P1zaRcQPKjyAnr-pAM2` |

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Response: 200 OK**

```json
{
  "message": "Translation deleted"
}
```

**Response: 404 Not Found** *(item does not exist or belongs to a different user)*

```json
{
  "error": "Translation not found"
}
```

**Response: 500 Internal Server Error**

```json
{
  "error": "Failed to delete history"
}
```

**Postman automation:** The `afterResponse` script resets `{{translation_id}}` to an empty string.

---

### 12. Clear History

**Collection request:** `clear history`

```
DELETE /history/clear
```

Deletes **all** translation history for the authenticated user.

**Authentication:** Required — Bearer token.

**Request headers:**

| Header | Value |
|---|---|
| `Authorization` | `Bearer {{auth_token}}` |

**Request body:** None.

**Response: 200 OK**

```json
{
  "message": "History deleted"
}
```

**Response: 404 Not Found** *(no history exists)*

```json
{
  "error": "No history found"
}
```

**Response: 500 Internal Server Error**

```json
{
  "error": "Failed to delete history"
}
```

---

### 13. SLT Health / Deep Health

```
GET /slt/health
GET /slt/health/deep
```

Health checks for the sign-language-**translation** (SLT) remote model server. Both require authentication.

**Authentication:** Required — Bearer token.

**`GET /slt/health` — Response: 200 OK**

```json
{
  "status": "healthy",
  "timestamp": "2026-09-25T10:15:00.000000"
}
```

**`GET /slt/health` — Response: 503 Service Unavailable**

```json
{
  "error": "Model not ready"
}
```

**`GET /slt/health/deep` — Response: 200 OK**

Returns the full deep-health payload from the remote ISL model API, including model, input, inference, memory, GPU, and runtime details.

**`GET /slt/health/deep` — Response: 503 Service Unavailable**

```json
{
  "error": "Model not ready"
}
```

---

### 14. SLP Health / Deep Health

```
GET /slp/health
GET /slp/health/deep
```

Placeholders for the sign-language-**production** (SLP) pipeline's model health checks — same shape as the SLT equivalents once implemented. **Not yet implemented** (currently stub handlers).

**Authentication:** Required — Bearer token.

---

## WebSocket API: Live Translation (SLT)

**Endpoint:** `WS /slt/ws` *(moved from `/ws`)*

The live translation API uses a native FastAPI WebSocket connection. Authentication is performed once during the WebSocket handshake, via the `require_ws_auth` dependency. After successful authentication, the server receives frames, processes them through the MediaPipe/SLT pipeline, and returns predictions.

### Connection

```text
Local:
ws://localhost:5000/slt/ws

Production:
wss://<render-host>/slt/ws
```

The client must send the Firebase ID token as a header during the handshake:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

**Do not send the Firebase ID token as a query parameter** — the query-parameter flow has been removed.

Example Flutter connection:

```dart
_channel = WebSocketChannel.connect(
  Uri.parse(wsUrl),
  headers: {
    'Authorization': 'Bearer $token',
  },
);
```

### WebSocket Authentication

The server verifies the Firebase ID token with the Firebase Admin SDK (`verify_id_token(..., check_revoked=True)`) before accepting the connection.

If the `Authorization` header is missing, malformed, or the token is invalid/expired/revoked, the handshake is rejected with WebSocket close code `1008` (policy violation) and a `reason` string (`"Missing or invalid token"`, `"Token expired"`, `"Token revoked"`, `"Invalid token"`, or `"Authentication failed"`). The application does not accept a user-supplied UID; the authenticated Firebase UID is the sole source of identity.

### Server Messages After Connect

Immediately after the connection is accepted:

```json
{
  "status": "connected",
  "message": "Ready for frames"
}
```

If MediaPipe is unavailable because of server memory/resource constraints, the server also sends:

```json
{
  "status": "info",
  "message": "Landmarks disabled on this server (memory limit). Accuracy may be lower."
}
```

If the remote model API is not yet healthy:

```json
{
  "status": "api_warming",
  "message": "Model API warming up, please wait..."
}
```

### Configuration Handshake (Client → Server)

The client should send a configuration message before streaming frames, to negotiate mode and transport:

```json
{
  "type": "config",
  "version": 1,
  "mode": "frames",
  "transport": "jpeg_binary"
}
```

| Field | Type | Values | Required |
|---|---|---|---|
| `type` | string | `"config"` | Yes |
| `version` | integer | `1` | Yes |
| `mode` | string | `"frames"`, `"video"`, `"hybrid"` | Yes |
| `transport` | string | `"jpeg_binary"`, `"json_base64"`, `"h264"`, `"h265"` | Yes |

If no config message is ever sent, the connection defaults to `mode="frames"`, `transport="jpeg_binary"` so pre-handshake clients keep working unchanged.

**Configuration acknowledgement (Server → Client):**

Accepted:

```json
{
  "type": "config_ack",
  "version": 1,
  "status": "accepted",
  "mode": "frames",
  "transport": "jpeg_binary"
}
```

Reserved but not yet implemented transport (`h264`/`h265`):

```json
{
  "type": "config_ack",
  "version": 1,
  "status": "not_implemented",
  "mode": "frames",
  "transport": "h264",
  "error": "h264 transport is reserved and not implemented yet"
}
```

Rejected (bad version, mode, or transport):

```json
{
  "type": "config_ack",
  "version": 1,
  "status": "error",
  "mode": "frames",
  "error": "Unsupported transport",
  "field": "transport"
}
```

Supported/reserved transports:

| Transport | Status | Client payload |
|---|---|---|
| `jpeg_binary` | Implemented (default) | Raw JPEG bytes as a binary WebSocket message |
| `json_base64` | Implemented | JSON object containing a Base64-encoded frame |
| `h264` | Reserved | Not implemented |
| `h265` | Reserved | Not implemented |

### Frame Messages (Client → Server)

Whichever transport is negotiated, every frame is decoded down to one common PIL RGB image before the sliding-window/inference pipeline runs.

#### 1. `jpeg_binary` — recommended production transport

Send the JPEG file bytes directly as a binary WebSocket message — no Base64, no JSON wrapper.

```text
WebSocket binary message
└── JPEG bytes
```

Example Python client:

```python
with open("frame.jpg", "rb") as f:
    websocket.send(f.read())
```

A binary WebSocket message is always treated as a raw JPEG frame, regardless of the negotiated transport.

#### 2. `json_base64` — debugging / Postman-friendly transport

Send:

```json
{
  "type": "frame",
  "frame": "<BASE64_ENCODED_JPEG>"
}
```

`type` may be omitted by clients, but when supplied it must be `"frame"`. The server validates the Base64 payload, decodes the image, validates its dimensions, and converts it to RGB.

#### 3. `h264` / `h265`

Reserved for future video-streaming support. A frame sent for one of these transports is rejected with:

```json
{
  "error": "Transport not implemented",
  "transport": "h264"
}
```

### Frame Validation Limits

| Limit | Value |
|---|---:|
| Maximum text message size | 2,000,000 characters |
| Maximum Base64 frame size (`json_base64`) | 2,000,000 characters |
| Maximum binary frame size (`jpeg_binary`) | 500,000 bytes |
| Maximum image dimension | 4096 × 4096 |
| Minimum image dimension | 1 × 1 |
| Minimum inter-frame interval | 0.08 s |
| Approximate maximum accepted rate | ~12.5 frames/s |
| Receive/idle timeout | 30 s |

Frames arriving faster than the configured interval are silently dropped (not queued).

### Server-Side Frame Processing Pipeline

1. Receive the WebSocket message (text or binary).
2. If it's a config or end-of-stream control message, handle it and continue.
3. Otherwise, decode it at the transport boundary into a PIL RGB image (`jpeg_binary` or `json_base64`).
4. Validate image size and dimensions.
5. Submit the frame to a dedicated MediaPipe executor for pose/hand landmark processing (when enabled) and resize to **224 × 224**.
6. JPEG-encode the processed frame once and append it to a rolling, order-preserving frame buffer.
7. Once **16 frames** (`CLIP_LENGTH`) are buffered, submit a sliding-window inference job on a separate inference executor (bounded by `MAX_CONCURRENT_INFERENCES = 2`), then retain the last 10 frames (`CLIP_LENGTH − CLIP_STRIDE`) so windows overlap.
8. Collect completed inference jobs and flush results back to the client strictly in sequence order, even if later windows finish first.
9. Continue collecting frames for the next window.

### Sliding Window

```text
CLIP_LENGTH = 16
CLIP_STRIDE = 6   (10-frame overlap between consecutive windows)
```

Each 16-frame window is packed into the ISLF binary container (see [Internal Backend → Model API Contract](#internal-backend--model-api-contract-islf)) and sent to the remote ISL model API's frame endpoint. Multiple windows may be in flight concurrently, bounded by `MAX_CONCURRENT_INFERENCES`.

```text
Flutter
   │
   │ WebSocket / WSS  (Authorization: Bearer <token> at handshake)
   ▼
FastAPI /slt/ws
   │
   ├── jpeg_binary / json_base64 decoding
   │
   ├── MediaPipe processing (dedicated executor)
   │
   └── 16-frame sliding window (dedicated inference executor)
          │
          │ ISLF binary container
          ▼
   Remote ISL Model API  (/predict_frames_bin)
```

### Prediction Response (Server → Client)

When an inference window completes and produces a non-empty label:

```json
{
  "label": "hello",
  "confidence": 0.95,
  "sequence": 3
}
```

| Field | Type | Description |
|---|---|---|
| `label` | string | Predicted ISL sign label. |
| `confidence` | number | Model confidence score in the range 0–1. |
| `sequence` | integer | Zero-based index of the sliding window this result corresponds to; useful for client-side debugging/ordering. |

A result with an empty prediction label, or one containing an `error` key, is logged server-side and **not** sent to the client.

### End of Stream

The client may send an explicit end-of-stream message to flush any remaining buffered frames before disconnecting:

```json
{
  "type": "end"
}
```

On receipt, the server finishes any in-flight landmark job, submits every remaining complete 16-frame window, waits for all outstanding inference jobs, sends their results in order, and then replies:

```json
{
  "status": "complete",
  "frames": 142,
  "inferences": 21
}
```

Any leftover frames (fewer than 16) are discarded without producing a partial prediction.

### Error Messages (Server → Client)

| Error / status | Meaning |
|---|---|
| `Missing or invalid token` / `Token expired` / `Token revoked` / `Invalid token` | WebSocket handshake authentication failure (connection closed with code `1008`) |
| `Invalid frame` | Frame could not be decoded or failed validation |
| Config `status: "error"` | Configuration message is malformed (bad `version`, `mode`, or `transport`); includes `field` |
| Config `status: "not_implemented"` | `transport` is `h264`/`h265` — reserved but not implemented |
| `Transport not implemented` | A frame arrived for an unimplemented transport |
| `Message too large` | Text message exceeded 2,000,000 characters |
| `api_warming` | Remote model API is still starting |

Invalid frame/configuration messages do not terminate the connection; the server keeps the session open and continues processing subsequent messages.

### Disconnect

The server closes the connection when:

- WebSocket authentication fails (handshake rejected before `accept()`).
- The client sends `{"type": "end"}` and the server finishes flushing results.
- The client disconnects.
- No message is received for **30 seconds** (idle timeout).
- An unrecoverable connection/protocol error occurs.

On cleanup: outstanding inference futures are cancelled, the in-flight MediaPipe job is cancelled or awaited, MediaPipe pose/hand detectors are closed, and the frame buffer is cleared.

### WebSocket Packet Logging

The backend logs packet sizes without logging image contents or authentication tokens.

Examples:

```text
[WS RX] binary JPEG frame: 42.18 KB (43192 bytes)
[WS TX] json: 71 B (71 bytes)
```

This is intended for operational debugging while avoiding sensitive payload logging.

---

## Internal Backend → Model API Contract (ISLF)

This layer is internal (backend ↔ remote ISL model API) and is not exposed to WebSocket clients directly, but is documented here because it determines SLT latency and payload size.

Once a 16-frame window is ready, the backend packs the frames' JPEG bytes into a compact binary container (`frame_codec.py`) rather than JSON/Base64, and POSTs it to the model API's `/predict_frames_bin` endpoint.

**Container layout (little-endian):**

```text
4 bytes    magic  b"ISLF"
2 bytes    frame count N
4×N bytes  length of each JPEG (uint32 each)
...        the N JPEG blobs, back to back
```

No Base64, no JSON, no zip — the JPEGs are already compressed, so the container adds only `6 + 4×N` bytes of overhead for `N` frames (36 bytes for the standard 16-frame window).

This is distinct from, and must never be confused with, the WebSocket-level `transport` field (`jpeg_binary` / `json_base64` / `h264` / `h265`), which only governs how a frame arrives on the **client-facing** socket. Every WebSocket transport funnels into the same ISLF-packed request to the model API.

The model API request is a single POST:

```
POST {BASE_URL}/predict_frames_bin?top_k=<n>
Content-Type: application/octet-stream

<ISLF-packed body>
```

with up to 2 retries on `503` (model warming up), a 15-second timeout, and a `/predict` (multipart MP4) fallback path used only in `hybrid` mode.

---

## WebSocket API: Sign Production (SLP)

**Endpoint:** `WS /slp/ws`

Reserved for the text-to-3D-pose-sequence sign production pipeline. Uses the same `require_ws_auth` handshake (Authorization header, Firebase ID token) as `/slt/ws`. **Not yet implemented** — the route and its `GET /slp/health` / `GET /slp/health/deep` companions currently exist as stub handlers with no behavior.

---

## Getting Started: Practical Sequence

Follow these steps to go from zero to a live sign-language translation session using the Postman collection.

### Step 1: Set collection variables

In the `sign_bridge` collection, set:

| Variable | Value |
|---|---|
| `base_url` | `http://localhost:5000` (or your server address) |
| `live_url` | `ws://localhost:5000` |
| `email` | `"your-test-email@example.com"` **(include the quotes: the request body uses `{{email}}` unquoted)** |
| `password` | `"yourPassword123"` |

### Step 2: Register a new account

Send **`POST /register`**.

On success `{{user_id}}` and `{{auth_token}}` are set automatically by the collection script.

### Step 3: (Optional) Log in with an existing account

Send **`POST /login`** if you already have an account. This also sets `{{auth_token}}`.

### Step 4: Store a translation

Set `{{translation}}` to `"hello"` (include quotes), then send **`POST /history/store`**.

`{{translation_id}}` is set automatically.

### Step 5: Retrieve history

Send **`GET /history`** to confirm the stored item appears.

### Step 6: Delete the translation

Send **`DELETE /history/{{translation_id}}`** to remove the item.

### Step 7: Connect to the WebSocket

Open the **`live translation`** WebSocket request. The URL is now **`{{live_url}}/slt/ws`** *(no `token` query parameter)*, and the request must set an `Authorization: Bearer {{auth_token}}` header on the handshake instead.

Click **Connect**. You should receive:

```json
{"status": "connected", "message": "Ready for frames"}
```

### Step 8: (Optional) Negotiate a transport

Send a config message to pick a transport explicitly:

```json
{"type": "config", "version": 1, "mode": "frames", "transport": "jpeg_binary"}
```

If you're testing in Postman, `json_base64` is easier since it doesn't require a binary frame:

```json
{"type": "config", "version": 1, "mode": "frames", "transport": "json_base64"}
```

### Step 9: Send frames

For `json_base64`, send Base64-encoded camera frames using:

```json
{"frame": "<BASE64_IMAGE>"}
```

For `jpeg_binary`, send raw JPEG bytes as a binary WebSocket message.

After every 16 accepted frames (with 6-frame stride thereafter) the server returns a prediction:

```json
{"label": "hello", "confidence": 0.92, "sequence": 0}
```

### Step 10: End the stream (optional)

Send `{"type": "end"}` to flush the final window and receive a `"status": "complete"` summary before disconnecting.

### Step 11: Update password (optional)

Set `{{new_pass}}` to `"newPassword456"` (with quotes), then send **`POST /update-password`**.

### Step 12: Log out

Send **`POST /logout`** to revoke refresh tokens.

---

## Postman Collection Variables

The `sign_bridge` collection defines the following variables (all start empty: set them before running):

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
| `auth_token` | `register` / `login` script | All protected requests, and the WebSocket `Authorization` header |

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
| WebSocket auth via `Authorization` header | ✅ Implemented (replaces query-parameter token) |
| WebSocket frame throttling | ✅ Implemented |
| WebSocket payload-size limits | ✅ Implemented |
| Image dimension limits | ✅ Implemented |
| WebSocket connection limit | ⚠️ Not documented as implemented |
| Token in WebSocket URL | ✅ Avoided |
| HTTPS / WSS | Deployment-dependent; Render provides TLS for public endpoints |
| Firebase Database Rules | Not verified in this documentation |
| Distributed rate-limit storage | Not verified in this documentation |
| SLP endpoints (`/slp/health`, `/slp/ws`) | ⚠️ Stubbed, not yet implemented, do not rely on them |

### Transport Security

Binary JPEG, JSON/Base64, H.264, and H.265 are **transport formats, not security mechanisms**. The internal ISLF container used between the backend and the model API is likewise a packing format, not encryption.

For production:

```text
Flutter
  │
  │ WSS + Authorization: Bearer <Firebase ID token> (at handshake)
  ▼
Render / FastAPI /slt/ws
```

Use `wss://` in production. TLS protects frames and the Firebase token while they are in transit.

Base64 does **not** encrypt data and adds approximately 33% encoding overhead. `jpeg_binary` is therefore the preferred transport for the production Flutter client.

Do not log:

- Firebase ID tokens.
- Raw image payloads.
- Base64 frame contents.

Logging packet sizes and message types is acceptable for diagnostics.

Do not expose `firebase.json`, `firebase-admin.json`, service-account credentials, or other Firebase secrets in version control or client-side assets.

---

## Running the Backend Locally

The backend is an ASGI/FastAPI application.

```bash
# 1. Check Python
python --version

# 2. Create and activate a virtual environment
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows PowerShell
.\venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure Firebase and required environment variables

# 5. Start the FastAPI server
uvicorn main:app --host 0.0.0.0 --port 5000
```

Local endpoints:

```text
REST:
http://127.0.0.1:5000

WebSocket (translation):
ws://127.0.0.1:5000/slt/ws

WebSocket (production, stubbed):
ws://127.0.0.1:5000/slp/ws

Health:
http://127.0.0.1:5000/health
```

For development with automatic reload:

```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

### Health Checks

Basic health:

```http
GET /health
```

Deep health:

```http
GET /health/deep
```

The basic health endpoint should be preferred for platform/container health checks because it does not depend on the database or the remote model service being healthy.

### SLT Endpoints

```text
GET  /slt/health
GET  /slt/health/deep
WS   /slt/ws
```

### SLP Endpoints

```text
GET  /slp/health         (stub)
GET  /slp/health/deep    (stub)
WS   /slp/ws             (stub)
```

### Tests

Run the offline regression suite with:

```bash
python -m unittest discover -s tests -v
```

The production deployment should expose the FastAPI ASGI application through an ASGI-compatible server/runtime. On Render, configure the service to bind to `0.0.0.0` and the port supplied by the `PORT` environment variable.