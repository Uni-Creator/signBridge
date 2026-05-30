# How to Run the SignBridge Backend

Follow these steps to set up and start the Python/Flask backend server.

## 1. Prerequisites
Ensure you have **Python 3.8+** installed on your system. You can check by running `python --version` in your terminal.

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
  "appId": "YOUR_APP_ID"
}   
```

2. Replace the placeholder values with those from your **Firebase Project Settings** (Project Settings > General > Your Apps > Web App config).

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
