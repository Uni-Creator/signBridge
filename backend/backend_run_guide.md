# How to Run the SignBridge Backend

Follow these steps to set up and start the Python/Flask backend server.

## 1. Prerequisites
Use **Python 3.10 or 3.11** for the pinned dependencies. You can check by running `python --version` in your terminal.

## 2. Configuration (Firebase)
The backend uses Firebase for authentication and translation history. You must configure your credentials:
1. Open backend/firebase.json.

template for firebase.json:
```
{
  "apiKey": "YOUR_API_KEY",
  "authDomain": "YOUR_AUTH_DOMAIN",
  "databaseURL": "YOUR_DATABASE_URL",
  "projectId": "YOUR_PROJECT_ID",
  "storageBucket": "YOUR_STORAGE_BUCKET",
  "messagingSenderId": "YOUR_MESSAGING_SENDER_ID",
  "appId": "YOUR_APP_ID",
  "identityURL":"googoleFirebaseIdentityURL"
}   
```

2. Replace the placeholder values with those from your **Firebase Project Settings** (Project Settings > General > Your Apps > Web App config).

3. Put a service-account key for the same Firebase project in `backend/firebase-admin.json`. The server initializes Firebase Admin with this key before verifying REST and WebSocket tokens. See the [Firebase Admin setup guide](https://firebase.google.com/docs/admin/setup) for generating a key. Keep it on the server; never put it in Flutter assets or commit it. Both Firebase JSON files are ignored by Git.

For hosted deployments, `/etc/secrets/firebase.json` and `/etc/secrets/firebase-admin.json` take precedence. Local fallback paths are relative to the backend files, so starting from the repository root also works (`python backend/main.py`). Authentication and history share the same Firebase client configuration.

The Realtime Database rules and credentials must also allow the server's history operations. These rules are not included in this repository; verify them in your own test Firebase project.

## 3. Setup Virtual Environment (Recommended)
Open a terminal in the `backend` directory and run:

```powershell
# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\activate
```

## 4. Install Dependencies
With the virtual environment active, install the required Python packages:

```powershell
pip install -r requirements.txt
```

## 5. Start the Server
Run the main script to start the Flask and WebSocket server:

```powershell
python main.py
```

### Server Details:
- **REST API**: `http://127.0.0.1:5000/`
- **WebSocket**: `ws://127.0.0.1:5000/ws`
- **For Android Emulator**: If you're testing from an emulator, it should connect to `http://10.0.2.2:5000`.

> [!NOTE]
> Ensure no other service is using port 5000 before starting.

## Offline regression tests

From the repository root, run:

```powershell
python -m unittest discover -s backend/tests -v
```

The tests require Flask, NumPy and Pillow. They replace Firebase, the remote model API, MediaPipe and WebSocket transport with test doubles; no credentials or network calls are needed. They exercise real Flask request handling and the frame-processing loop. A live Firebase and camera smoke test is still needed before deployment.
