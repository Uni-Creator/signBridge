# How to Run the SignSight Frontend

Follow these steps to set up and launch the Flutter mobile application.

## 1. Prerequisites

Ensure the following are installed on your system:

- **Flutter SDK 3.x+** — [Install Flutter](https://docs.flutter.dev/get-started/install)
- **Dart SDK** — bundled with Flutter
- **Android Studio** (for Android) or **Xcode** (for iOS/macOS)
- An **Android Emulator** or a physical device with USB debugging enabled

You can verify your Flutter setup by running:

```bash
flutter doctor
```

Resolve any issues flagged before proceeding.

## 2. Configuration (Backend URL)

The app communicates with the Flask backend over REST and WebSocket. You must point it to the correct server address:

1. Open `frontend/lib/services/api_service.dart` (or equivalent config file).
2. Update the base URL constants:

```dart
const String baseUrl = 'http://10.0.2.2:5000';       // Android Emulator
const String wsUrl  = 'ws://10.0.2.2:5000/ws';        // Android Emulator WebSocket
```

```dart
const String baseUrl = 'https://project.onrender.com';       // Render
const String wsUrl  = 'wss://project.onrender.com/ws';        // Render WebSocket
```

> [!NOTE]
> Use `10.0.2.2` to reach your local machine from an Android emulator.
> For a physical device on the same Wi-Fi network, use your machine's local IP (e.g., `192.168.x.x`).

## 3. Install Dependencies

Open a terminal in the `frontend` directory and run:

```bash
flutter pub get
```

## 4. Configure Firebase (Google Services)

The app uses Firebase for authentication. You need to add your own Firebase config files:

### Android
1. Go to your [Firebase Console](https://console.firebase.google.com/) → Project Settings → Your Apps → Android app.
2. Download `google-services.json`.
3. Place it at:
   ```
   backend/firebase.json
   ```
4. The file should be in the same format as below (replace with your actual values):
```firebase.json
{
  "apiKey": "YOUR_API_KEY",
  "authDomain": "YOUR_AUTH_DOMAIN",
  "databaseURL": "YOUR_DATABASE_URL",
  "projectId": "YOUR_PROJECT_ID",
  "storageBucket": "YOUR_STORAGE_BUCKET",
  "messagingSenderId": "YOUR_SENDER_ID",
  "appId": "YOUR_APP_ID"
}

```

### iOS
1. Download `GoogleService-Info.plist` from the same Firebase project.
2. Place it at:
   ```
   backend/firebase.plist
   ```

## 5. Run the App

Ensure a device or emulator is running, then execute:

```bash
flutter run
```

To target a specific device if multiple are connected:

```bash
flutter devices              # List available devices
flutter run -d <device_id>   # Run on a specific device
```

### Build Variants

| Command | Description |
|---|---|
| `flutter run` | Debug build (hot reload enabled) |
| `flutter run --release` | Release build (optimized, no debug tools) |
| `flutter build apk` | Generate a release APK |

> [!NOTE]
> The backend server must be running before launching the app. See `BACKEND_SETUP.md` for instructions.

## 6. Permissions

The app requires the following permissions, which are already declared in `AndroidManifest.xml`:

- **Camera** — for real-time gesture capture
- **Internet** — for communicating with the Flask backend
- **Microphone** *(if applicable)* — for audio feedback features

On first launch, accept the permission prompts when asked.

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `flutter doctor` shows missing dependencies | Follow the recommended fixes in the output |
| App cannot reach the backend | Confirm the backend is running and the URL/IP in the config is correct |
| Camera permission denied | Manually grant camera access in device Settings → Apps → signSight |
| Build fails on Android | Run `flutter clean` then `flutter pub get` and try again |
| Emulator connection refused | Ensure the backend is bound to `0.0.0.0` (not just `127.0.0.1`) |