import 'dart:async';

import 'package:camera_platform_interface/camera_platform_interface.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sign_bridge/core/services/websocket_service.dart';
import 'package:sign_bridge/features/auth/poviders/auth_provider.dart';
import 'package:sign_bridge/features/translate/providers/translation_provider.dart';
import 'package:sign_bridge/features/translate/screens/translate_screen.dart';

class TestSocket extends WebSocketService {
  bool connected = true;
  final modes = <String>[];
  final frames = <Uint8List>[];
  @override
  bool get isConnected => connected;
  @override
  Future<void> connect(String token) async =>
      onConnectionChange?.call(connected);
  @override
  Future<void> disconnect() async {}
  @override
  void sendConfig(String mode) => modes.add(mode);
  @override
  void sendFrame(Uint8List bytes) => frames.add(bytes);
}

class TestCamera extends CameraPlatform {
  final events = <String>[];
  final formats = <ImageFormatGroup>[];
  final images = StreamController<CameraImageData>.broadcast();
  Completer<void>? disposal;
  Completer<void>? initialization;
  bool failStart = false;
  int nextId = 0;

  @override
  Future<List<CameraDescription>> availableCameras() async => const [
        CameraDescription(
            name: 'rear',
            lensDirection: CameraLensDirection.back,
            sensorOrientation: 90),
        CameraDescription(
            name: 'front',
            lensDirection: CameraLensDirection.front,
            sensorOrientation: 270),
      ];

  @override
  Future<int> createCameraWithSettings(
      CameraDescription description, MediaSettings settings) async {
    events.add('create:${description.name}');
    return nextId++;
  }

  @override
  Future<void> initializeCamera(int cameraId,
      {ImageFormatGroup imageFormatGroup = ImageFormatGroup.unknown}) async {
    formats.add(imageFormatGroup);
    await initialization?.future;
  }

  @override
  Stream<CameraInitializedEvent> onCameraInitialized(int cameraId) =>
      Stream.value(
        CameraInitializedEvent(cameraId, 640, 480, ExposureMode.auto, false,
            FocusMode.auto, false),
      );
  @override
  Stream<CameraErrorEvent> onCameraError(int cameraId) =>
      StreamController<CameraErrorEvent>().stream;
  @override
  Stream<DeviceOrientationChangedEvent> onDeviceOrientationChanged() =>
      const Stream.empty();
  @override
  Widget buildPreview(int cameraId) => const SizedBox.expand();
  @override
  bool supportsImageStreaming() => true;
  @override
  Stream<CameraImageData> onStreamedFrameAvailable(int cameraId,
      {CameraImageStreamOptions? options}) {
    events.add('start:$cameraId');
    if (failStart) {
      throw CameraException('startFailed', 'Test camera start failure');
    }
    return images.stream;
  }

  @override
  Future<void> dispose(int cameraId) async {
    events.add('dispose:$cameraId');
    await disposal?.future;
    events.add('disposed:$cameraId');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late CameraPlatform original;
  late TestCamera camera;
  late TestSocket socket;

  setUp(() {
    original = CameraPlatform.instance;
    camera = TestCamera();
    socket = TestSocket();
    CameraPlatform.instance = camera;
    SharedPreferences.setMockInitialValues({});
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
            const MethodChannel('flutter_tts'), (_) async => 1);
  });
  tearDown(() async {
    CameraPlatform.instance = original;
    await camera.images.close();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('flutter_tts'), null);
  });

  Future<void> show(WidgetTester tester) async {
    await tester.pumpWidget(MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => AuthProvider()),
        ChangeNotifierProvider(create: (_) => TranslationProvider()),
      ],
      child: MaterialApp(home: TranslateScreen(webSocketService: socket)),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('Android requests YUV and sends initial inference mode',
      (tester) async {
    await show(tester);
    expect(camera.formats, [ImageFormatGroup.yuv420]);
    expect(socket.modes, ['hybrid']);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });

  testWidgets('camera startup failure stays stopped and shows an error',
      (tester) async {
    camera.failStart = true;
    await show(tester);
    await tester.tap(find.text('Start Translating'));
    await tester.pumpAndSettle();
    expect(find.text('LIVE'), findsNothing);
    expect(find.textContaining('Test camera start failure'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });

  testWidgets('resume waits for previous camera disposal', (tester) async {
    await show(tester);
    camera.disposal = Completer<void>();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pump();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    expect(camera.events, ['create:rear', 'dispose:0']);
    camera.disposal!.complete();
    await tester.pumpAndSettle();
    expect(camera.events,
        ['create:rear', 'dispose:0', 'disposed:0', 'create:rear']);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('leaving during initialization disposes after it finishes',
      (tester) async {
    camera.initialization = Completer<void>();
    await show(tester);
    await tester.pumpWidget(const SizedBox.shrink());
    expect(camera.events, ['create:rear']);
    camera.initialization!.complete();
    await tester.pumpAndSettle();
    expect(camera.events, ['create:rear', 'dispose:0', 'disposed:0']);
    expect(tester.takeException(), isNull);
  });

  testWidgets('switching cameras disposes the old controller exactly once',
      (tester) async {
    await show(tester);
    await tester.tap(find.byIcon(Icons.flip_camera_ios));
    await tester.pumpAndSettle();
    expect(camera.events,
        ['create:rear', 'dispose:0', 'disposed:0', 'create:front']);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });

  testWidgets('valid frames are sent only while streaming', (tester) async {
    await show(tester);
    await tester.tap(find.text('Start Translating'));
    await tester.pump();
    final image = CameraImageData(
      format: const CameraImageFormat(ImageFormatGroup.yuv420, raw: 35),
      width: 2,
      height: 2,
      planes: [
        CameraImagePlane(
            bytes: Uint8List.fromList([100, 100, 100, 100]),
            bytesPerRow: 2,
            bytesPerPixel: 1),
        CameraImagePlane(
            bytes: Uint8List.fromList([128]), bytesPerRow: 1, bytesPerPixel: 1),
        CameraImagePlane(
            bytes: Uint8List.fromList([128]), bytesPerRow: 1, bytesPerPixel: 1),
      ],
    );
    camera.images.add(image);
    await tester.pump();
    expect(socket.frames, hasLength(1));
    expect(socket.frames.single.take(2), [0xff, 0xd8]);
    await tester.tap(find.text('Stop Streaming'));
    await tester.pumpAndSettle();
    camera.images.add(image);
    await tester.pump();
    expect(socket.frames, hasLength(1));
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });

  testWidgets('bad frames stop streaming and show the conversion failure',
      (tester) async {
    await show(tester);
    await tester.tap(find.text('Start Translating'));
    await tester.pump();
    camera.images.add(const CameraImageData(
      format: CameraImageFormat(ImageFormatGroup.unknown, raw: -1),
      width: 2,
      height: 2,
      planes: [],
    ));
    await tester.pump();
    await tester.pumpAndSettle();
    expect(find.text('LIVE'), findsNothing);
    expect(find.textContaining('Frame conversion failed:'), findsOneWidget);
    expect(socket.frames, isEmpty);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });
}
