# Camera frame consistency

This change addresses how camera frames reach the model. It does not change the model, its weights, the 224 × 224 input size, or JPEG quality.

## Why the preview can look fine while translation gets bad frames

The camera preview and the frames sent for inference take different paths. The camera plugin displays the preview, but our Dart code converts the raw frame buffers to JPEG. A bug in that conversion can corrupt model input without visibly corrupting the preview.

Android commonly supplies YUV420 frames: one plane for brightness (Y) and two smaller planes for colour (U and V). These buffers are not necessarily packed tightly. `bytesPerRow` tells us where the next row starts, and `bytesPerPixel` tells us how far apart adjacent samples are.

The old converter assumed colour samples were adjacent and used the U plane's index to read V too. That works for some layouts but reads the wrong bytes for others. The new converter uses each plane's own row and pixel strides. Tests deliberately insert padding and use different U/V layouts to catch this kind of device-dependent error.

## What changed

### 1. Explicit frame formats and correct conversion

Android now requests YUV420 for streaming. iOS requests BGRA8888. Previously the app requested JPEG, but its conversion function did not handle JPEG at all.

The converter checks the format actually received and supports YUV420, BGRA8888 and JPEG. BGRA frames now use the correct blue/green/red order and respect row padding and buffer offsets. Invalid dimensions, missing planes and truncated buffers produce explicit errors. Unsupported layouts are rejected instead of being guessed.

The iOS camera permission description is also included. Audio capture stays disabled.

### 2. Camera operations finish in order

Starting, stopping, switching and releasing the camera are asynchronous operations. Previously, switching cameras disposed the same controller twice, and backgrounding could stop and dispose it at the same time. A quick return to the app could start a new controller before the previous one finished releasing the camera.

These operations now run through one sequential queue. Switching waits for release before initializing the next camera. Leaving the screen queues cleanup after any pending initialization. Callbacks check whether the screen is still mounted before updating it.

The camera is released when the app leaves the foreground. On return, the preview is reinitialized and the user taps Start again. Background capture is not enabled.

### 3. Streaming state and errors reflect what happened

The screen waits for `startImageStream` to complete before marking itself as streaming. Controls are disabled during pending camera operations. If conversion fails, it immediately displays the error and stops the stream, instead of continuing to show an indefinite analyzing state.

Frame throttling uses a stopwatch, so a change to the phone's wall clock does not affect the interval. The existing 80 ms interval remains. The selected inference mode is also sent when the socket connects, so the initial `HYBRID` selection matches the configuration sent to the server.

### 4. Regression tests and fresh-checkout setup

The tests cover packed and padded YUV, independent U/V strides, odd dimensions, BGRA channel order and buffer offsets, JPEG output, and malformed frames. Camera widget tests cover initialization, startup failure, disposal ordering, camera switching, frame delivery and conversion errors.

The old generated counter test was replaced with a splash-to-login test. The password-reset test now uses `loadFromString`, which is the API provided by the installed dotenv version.

The unused `assets/` declaration was removed because that directory does not exist in the repository. Copy the provided environment example before running the app or tests:

```powershell
cd frontend
Copy-Item .env.example .env
flutter pub get
flutter test
flutter analyze
```

The example uses `10.0.2.2` for the Android emulator. For a physical phone, set both URLs to a reachable backend address. Keep `.env` local; it is ignored by Git.

## Validation and limits

Local validation passed: **19 Flutter tests** and **10 backend regression tests**. Dart analysis reports **no errors**, with four existing unused-field warnings in the home screen and 83 informational lint/deprecation findings across the project. It is not a completely clean analyzer run.

Flutter tests ran on Windows with Flutter 3.47.4 / Dart 3.13.3 and camera 0.11.4, CameraX 0.6.30 and image 4.8.0. The newer Flutter SDK resolved its own pinned test/framework dependencies locally; those unrelated lockfile version changes were excluded from this contribution. Run `flutter pub get` with your SDK before reproducing the tests.

The tests use synthetic frames and a fake camera platform/socket, so they do not require Firebase, a camera or a live inference server. They cannot confirm a particular handset's camera driver behaviour. The initial target for manual verification is the Realme P3 5G on Android 16, software RMX5070_16.0.5.1010 (EX01).

Frame conversion still runs on the Dart UI isolate. Moving it to a worker isolate may be worthwhile after measuring frame time on the phone. Rotation, mirroring, cropping and YUV colour-range calibration are unchanged; those need comparison against the model's training preprocessing before making further transformations.

## Realme test checklist

1. Start translation with the rear camera. Confirm labels arrive and the mode shown is the intended mode.
2. Stop and restart several times. The controls should recover normally and the camera should not report an already-streaming error.
3. Switch front/rear cameras, both before starting and during streaming. Check that translation resumes after switching during a stream.
4. Background and reopen the app; then lock/unlock the phone. The preview should recover, with streaming stopped until Start is tapped.
5. Leave the translation screen while the camera is starting. Reopen it and check for camera-in-use errors or crashes.
6. Deny camera permission and confirm the error is visible. Grant permission in settings and reopen the screen.
7. Compare a few signs under the same lighting and distance. Record failures and latency; do not infer an accuracy gain from these code changes alone.

If colours or orientation still look wrong in inference, inspect a development-only decoded frame and compare it with the signer and the expected model orientation. Avoid saving or logging users' frames by default.

## References

- [Camera plane metadata](https://pub.dev/documentation/camera/latest/camera/Plane-class.html)
- [Camera plugin lifecycle and permissions](https://pub.dev/packages/camera)
- [CameraX streaming format notes](https://pub.dev/packages/camera_android_camerax)
