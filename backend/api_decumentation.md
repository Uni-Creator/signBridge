# SignBridge API Documentation

## Overview

The SignBridge backend provides REST APIs for authentication and translation history, along with a WebSocket API for real-time sign-language recognition.

### Base URL

For local development:

```text
http://localhost:5000
```

For a physical device on the same network:

```text
http://<SERVER_IP>:5000
```

For an Android emulator:

```text
http://10.0.2.2:5000
```

The production base URL depends on the deployment environment.

---

# Authentication

SignBridge uses Firebase Authentication.

Protected REST endpoints require a Firebase ID token in the `Authorization` header:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

The backend verifies the token using Firebase Admin SDK.

The authenticated Firebase UID is taken from the verified token:

```text
Firebase ID token
        │
        ▼
Firebase Admin verification
        │
        ▼
decoded["uid"]
        │
        ▼
request.user_id
```

Clients should not rely on sending a user ID to establish ownership of protected resources.

---

# API Endpoints

## 1. Health Check

### `GET /`

Returns a basic response confirming that the backend is running.

### Request

```http
GET /
```

### Response

```json
{
  "message": "SignBridge API is running",
  "version": "2.0"
}
```

### Authentication

Not required.

---

# Authentication APIs

## 2. Register

### `POST /register`

Creates a new Firebase user account.

### Request

```http
POST /register
Content-Type: application/json
```

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

### Validation

The request must contain:

- `email`
- `password`

The backend validates:

- Email is present.
- Email is a string.
- Email follows the expected email format.
- Password is present.
- Password is a string.
- Password contains at least 6 characters.

### Success Response

```http
200 OK
```

```json
{
  "id": "firebase-user-id",
  "token": "firebase-id-token"
}
```

### Failure Response

```http
400 Bad Request
```

```json
{
  "id": "",
  "token": "",
  "error": "Registration failed"
}
```

### Rate Limit

```text
5 requests / minute
```

Exceeding the limit returns:

```http
429 Too Many Requests
```

```json
{
  "error": "Too many registration attempts. Please try again later."
}
```

---

# 3. Login

### `POST /login`

Authenticates an existing Firebase user.

### Request

```http
POST /login
Content-Type: application/json
```

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

### Success Response

```http
200 OK
```

```json
{
  "id": "firebase-user-id",
  "token": "firebase-id-token"
}
```

### Failure Response

```http
400 Bad Request
```

```json
{
  "id": "",
  "token": "",
  "error": "Login failed"
}
```

### Rate Limit

```text
5 requests / minute
```

---

# 4. Forgot Password

### `POST /forgot-password`

Requests a Firebase password-reset email.

### Request

```http
POST /forgot-password
Content-Type: application/json
```

```json
{
  "email": "user@example.com"
}
```

### Success Response

```http
200 OK
```

```json
{
  "success": "Password reset email has been sent."
}
```

The endpoint intentionally returns a generic confirmation instead of exposing Firebase-specific errors to the client.

### Rate Limit

```text
3 requests / minute
```

This endpoint should remain generic to reduce the risk of email/account enumeration.

---

# History APIs

All history endpoints require authentication.

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

The backend obtains the user ID from the verified Firebase token.

---

# 5. Get Translation History

### `GET /history`

Returns the authenticated user's translation history.

### Request

```http
GET /history
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

No user ID should be supplied through the query string.

### Success Response

```http
200 OK
```

```json
{
  "history": [
    {
      "id": "-Oabc123",
      "translation": "hello",
      "timestamp": "2026-09-20T03:20:15.123456"
    },
    {
      "id": "-Oabc456",
      "translation": "thank you",
      "timestamp": "2026-09-20T03:18:42.123456"
    }
  ]
}
```

### Authentication Failure

```http
401 Unauthorized
```

Possible responses:

```json
{
  "error": "Missing or invalid token"
}
```

or:

```json
{
  "error": "Token expired"
}
```

or:

```json
{
  "error": "Invalid token"
}
```

### Rate Limit

```text
20 requests / minute
```

---

# 6. Store Translation

### `POST /history/store`

Stores a translation in the authenticated user's history.

### Request

```http
POST /history/store
Authorization: Bearer <FIREBASE_ID_TOKEN>
Content-Type: application/json
```

```json
{
  "translation": "hello"
}
```

The client does **not** need to send a user ID.

The backend determines the user from the verified Firebase token.

### Success Response

```http
201 Created
```

Example:

```json
{
  "id": "-Oabc123",
  "translation": "hello",
  "timestamp": "2026-09-20T03:20:15.123456"
}
```

### Missing Translation

```http
400 Bad Request
```

```json
{
  "error": "Missing translation"
}
```

### Rate Limit

```text
50 requests / minute
```

---

# 7. Delete One Translation

### `DELETE /history/<translation_id>`

Deletes one history item belonging to the authenticated user.

### Request

```http
DELETE /history/-Oabc123
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

### Success Response

```http
200 OK
```

```json
{
  "message": "Translation deleted"
}
```

### Translation Not Found

```http
404 Not Found
```

```json
{
  "error": "Translation not found"
}
```

### Authentication

Required.

The backend uses:

```text
Firebase token → authenticated UID
```

and then attempts deletion under that user's history.

A client cannot select another user's UID through the request.

### Rate Limit

```text
50 requests / minute
```

---

# 8. Clear Translation History

### `DELETE /history/clear`

Deletes all translation history belonging to the authenticated user.

### Request

```http
DELETE /history/clear
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

### Success Response

```http
200 OK
```

```json
{
  "message": "History deleted"
}
```

### No History

```http
404 Not Found
```

```json
{
  "error": "No history found"
}
```

### Rate Limit

```text
10 requests / minute
```

---

# WebSocket API

## 9. Real-Time Sign Detection

### `WS /ws`

Provides real-time sign-language recognition.

The WebSocket connection requires a Firebase ID token.

### Current Connection Format

```text
ws://<SERVER>/ws?token=<FIREBASE_ID_TOKEN>
```

Example:

```text
ws://192.168.1.10:5000/ws?token=eyJhbGciOi...
```

> Production deployments should use `wss://` rather than `ws://`.

### Authentication

The server verifies the Firebase ID token before accepting frames.

An invalid token results in:

```json
{
  "error": "Unauthorized"
}
```

The WebSocket connection is then closed.

---

# WebSocket Connection

After successful authentication:

```json
{
  "status": "connected",
  "message": "Ready for frames"
}
```

If MediaPipe is unavailable, the server may additionally send:

```json
{
  "status": "info",
  "message": "Landmarks disabled on this server (memory limit). Accuracy may be lower."
}
```

---

# WebSocket Configuration

The client can change the inference mode using:

```json
{
  "type": "config",
  "mode": "frames"
}
```

Supported modes:

```text
frames
video
hybrid
```

### Example

```json
{
  "type": "config",
  "mode": "hybrid"
}
```

The server responds with:

```json
{
  "status": "config_updated",
  "mode": "hybrid"
}
```

---

# Sending Frames

Frames are sent as JSON containing a Base64-encoded image:

```json
{
  "frame": "<BASE64_ENCODED_IMAGE>"
}
```

The server:

1. Parses the JSON.
2. Extracts the `frame` field.
3. Decodes Base64.
4. Decodes the image.
5. Converts it to RGB.
6. Runs MediaPipe processing when enabled.
7. Resizes the processed frame to `224 × 224`.
8. Buffers frames.
9. Runs inference after 16 frames are available.

---

# Frame Rate Limiting

The server currently enforces:

```text
FRAME_DELAY = 0.08 seconds
```

This allows approximately:

```text
12.5 accepted frames / second / connection
```

Frames arriving faster than this are discarded.

---

# WebSocket Prediction Response

When inference produces a prediction:

```json
{
  "label": "hello",
  "confidence": 0.95
}
```

Where:

| Field | Type | Description |
|---|---|---|
| `label` | string | Predicted sign |
| `confidence` | number | Model confidence |

---

# WebSocket Processing

The WebSocket architecture is divided into two layers.

## `websocket_handler.py`

Responsible for:

- Firebase authentication
- Connection lifecycle
- Receiving messages
- Configuration commands
- Frame-rate limiting
- Frame decoding
- Frame buffering
- Scheduling asynchronous processing
- Returning predictions
- Cleanup

This separation is explicitly reflected in the handler implementation.

## `websocket_processing.py`

Responsible for:

- MediaPipe
- Landmark detection
- Frame processing
- Model inference
- Optional test-video generation

The WebSocket route in `main.py` therefore remains a thin controller:

```python
@sock.route("/ws")
def websocket_translate(ws):
    handle_websocket(ws, model_api, executor)
```

---

# Error Responses

The REST API generally uses the following status codes:

| Status | Meaning |
|---|---|
| `200` | Request succeeded |
| `201` | Resource created |
| `400` | Invalid request |
| `401` | Authentication required/failed |
| `404` | Resource not found |
| `429` | Rate limit exceeded |
| `500` | Internal server error |

Rate-limit failures are converted into JSON responses.

---

# Security

## Implemented

### Firebase Authentication

Protected endpoints verify Firebase ID tokens using Firebase Admin SDK.

```text
Authorization: Bearer <token>
```

The UID is extracted only after successful verification.

### User Isolation

History operations use:

```python
request.user_id
```

rather than trusting a user ID supplied by the client.

This prevents a client from simply changing:

```text
?id=another-user
```

to access another user's history.

### Rate Limiting

Current endpoint limits:

| Endpoint | Limit |
|---|---:|
| `POST /register` | 5/min |
| `POST /login` | 5/min |
| `POST /forgot-password` | 3/min |
| `GET /history` | 20/min |
| `POST /history/store` | 50/min |
| `DELETE /history/<id>` | 50/min |
| `DELETE /history/clear` | 10/min |

The application also has default limits of:

```text
100 requests / minute
5000 requests / day
```



### Input Validation

Authentication endpoints validate email type, email format, password type, and minimum password length.

### Generic Authentication Errors

Invalid authentication tokens return generic errors rather than Firebase exception details.

### WebSocket Authentication

WebSocket clients must authenticate before frames are processed.

---

# Security Considerations

The following should be addressed before treating the WebSocket API as production-hardened.

## 1. Do not send Firebase tokens in URLs

Current:

```text
/ws?token=<FIREBASE_ID_TOKEN>
```

Query parameters can potentially appear in:

- reverse-proxy logs
- access logs
- monitoring systems
- debugging tools
- browser/network history

The current implementation explicitly retrieves the token from the query string.

Prefer an authenticated WebSocket handshake/header mechanism supported by the deployment stack, or use a short-lived WebSocket-specific token.

---

## 2. Add WebSocket message-size limits

The current frame decoder accepts a Base64 payload and decodes it without an explicit maximum payload size.

A malicious client could send extremely large messages.

Recommended controls:

```text
Maximum WebSocket message size
Maximum Base64 payload size
Maximum decoded image size
Maximum image dimensions
```

---

## 3. Add image-dimension limits

Before expensive MediaPipe processing, reject images exceeding a reasonable maximum width/height.

For example:

```text
MAX_WIDTH
MAX_HEIGHT
MAX_PIXELS
```

This protects against memory-exhaustion attacks.

---

## 4. Add WebSocket connection limits

The current:

```text
FRAME_DELAY = 0.08
```

limits frames **per connection**.

It does not prevent an attacker from opening many simultaneous WebSocket connections.

Consider:

```text
Maximum connections per IP
Maximum authenticated connections per UID
Maximum global WebSocket connections
Connection timeout
```

---

## 5. Use HTTPS/WSS in production

Production deployment should use:

```text
https://
```

for REST APIs and:

```text
wss://
```

for WebSockets.

Do not send Firebase tokens or camera frames over unencrypted `http://` / `ws://` in production.

---

## 6. Firebase Realtime Database rules

The Flask API authenticates users, but Firebase Database Rules should independently enforce user isolation if clients can access Firebase directly.

The intended rule concept is:

```text
user/<uid>/history
```

should only be accessible when:

```text
auth.uid == uid
```

---

## 7. Production rate-limit storage

Flask-Limiter should use a shared persistent backend when the application runs across multiple processes/instances.

An in-memory/local limiter can otherwise result in limits being applied independently by different workers.

---

## 8. Do not log sensitive information

Avoid logging:

- Firebase ID tokens
- passwords
- raw camera frames
- Base64 image data
- sensitive user information

The current authentication routes log email addresses and Firebase IDs. Review is needed for whether these identifiers are necessary in production logs.

---

# Security Status

| Area | Current status |
|---|---|
| Firebase REST authentication | Implemented |
| Firebase UID-based authorization | Implemented |
| History IDOR protection | Implemented at API layer |
| REST rate limiting | Implemented |
| Input validation | Implemented |
| Generic auth errors | Implemented |
| WebSocket authentication | Implemented |
| WebSocket frame throttling | Implemented |
| WebSocket payload-size limit | **Missing** |
| Image dimension/pixel limit | **Missing** |
| WebSocket connection limit | **Missing** |
| Token exposed in WebSocket URL | **Needs improvement** |
| HTTPS/WSS enforcement | Deployment-dependent |
| Firebase Database Rules | **Not verified** |
| Distributed rate-limit storage | **Not verified** |

The REST API has a reasonable authentication/authorization foundation. The main remaining security work is around **WebSocket resource exhaustion, credential transport, and deployment-level protections** rather than the basic Firebase authentication mechanism.