import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:provider/provider.dart';
import 'package:flutter/foundation.dart';
import '../../../core/services/camera_frame_converter.dart';

import '../../auth/providers/auth_provider.dart';
import '../providers/translation_provider.dart';
import '../../../core/services/websocket_service.dart';

class TranslateScreen extends StatefulWidget {
  const TranslateScreen({super.key, this.webSocketService});

  final WebSocketService? webSocketService;

  @override
  State<TranslateScreen> createState() => _TranslateScreenState();
}

class _TranslateScreenState extends State<TranslateScreen>
    with WidgetsBindingObserver {
  CameraController? _cameraController;
  List<CameraDescription> _cameras = [];
  int _selectedCamera = 0;

  late final WebSocketService _wsService;
  final FlutterTts _tts = FlutterTts();

  bool _cameraInitialized = false;
  bool _isStreaming = false;
  bool _isSpeaking = false;
  String _statusMessage = 'Camera not started';
  double _confidence = 0;
  Future<void> _cameraTask = Future<void>.value();
  int _cameraOperations = 0;
  bool _cameraActive = true;
  final Stopwatch _frameClock = Stopwatch()..start();

  // Inference Configuration
  String _inferenceMode = 'hybrid'; // frames, video, hybrid

  // Frame Throttling
  int? _lastFrameTime;
  static const int _frameIntervalMs = 80; // ~12 FPS

  // static const primaryColor = Color(0xFF2B2D5D);
  // static const accentColor = Color(0xFF4B6CF7);

  @override
  void initState() {
    super.initState();
    _wsService = widget.webSocketService ?? WebSocketService();
    WidgetsBinding.instance.addObserver(this);
    _initTts();
    _loadCameras();
    _setupWebSocket();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final token = context.read<AuthProvider>().token ?? '';
      _wsService.connect(token);
    });
  }

  void _initTts() async {
    await _tts.setLanguage('en-US');
    await _tts.setSpeechRate(0.5);
    if (!mounted) return;
    _tts.setStartHandler(() {
      if (mounted) setState(() => _isSpeaking = true);
    });
    _tts.setCompletionHandler(() {
      if (mounted) setState(() => _isSpeaking = false);
    });
  }

  // Native camera operations must finish in order, including disposal.
  Future<void> _queueCameraOperation(Future<void> Function() action) {
    _cameraOperations++;
    if (mounted) setState(() {});
    _cameraTask = _cameraTask.then((_) => action()).catchError((Object error) {
      if (mounted) {
        setState(() => _statusMessage = 'Camera error: $error');
      }
    }).whenComplete(() {
      _cameraOperations--;
      if (mounted) setState(() {});
    });
    return _cameraTask;
  }

  Future<void> _loadCameras() async {
    try {
      final cameras = await availableCameras();
      if (!mounted) return;
      _cameras = cameras;
      if (_cameras.isEmpty) {
        setState(() => _statusMessage = 'No cameras found');
        return;
      }
      await _queueCameraOperation(() => _initCamera(_cameras[_selectedCamera]));
    } catch (e) {
      if (mounted) setState(() => _statusMessage = 'Camera error: $e');
    }
  }

  Future<void> _releaseCamera() async {
    final controller = _cameraController;
    _cameraController = null;
    _cameraInitialized = false;
    _isStreaming = false;
    if (controller == null) return;
    try {
      if (controller.value.isStreamingImages) {
        await controller.stopImageStream();
      }
    } finally {
      await controller.dispose();
    }
  }

  Future<void> _initCamera(CameraDescription cam) async {
    await _releaseCamera();
    if (!mounted || !_cameraActive) return;
    final controller = CameraController(
      cam,
      ResolutionPreset.medium,
      enableAudio: false,
      imageFormatGroup: defaultTargetPlatform == TargetPlatform.iOS
          ? ImageFormatGroup.bgra8888
          : ImageFormatGroup.yuv420,
    );
    _cameraController = controller;
    try {
      await controller.initialize();
      if (!mounted || !_cameraActive) return;
      setState(() {
        _cameraInitialized = true;
        _statusMessage = 'Camera ready. Tap Start to begin.';
      });
    } catch (_) {
      await _releaseCamera();
      rethrow;
    }
  }

  void _setupWebSocket() {
    _wsService.onTranslation = (label, confidence) {
      if (!mounted) return;
      setState(() {
        _confidence = confidence;
      });
      context.read<TranslationProvider>().updateTranslation(label);
    };

    _wsService.onConnectionChange = (connected) {
      if (!mounted) return;
      context.read<TranslationProvider>().setConnected(connected);
      if (connected) _wsService.sendConfig(_inferenceMode);
      setState(() {
        _statusMessage = connected 
            ? (_isStreaming ? 'Connected to server. Streaming...' : 'Connected to server. Ready.') 
            : 'Disconnected';
      });
    };

    _wsService.onError = (err) {
      if (!mounted) return;
      setState(() => _statusMessage = 'Error: $err');
    };
  }

  Future<void> _startStreaming() => _queueCameraOperation(_startCameraStream);

  Future<void> _startCameraStream() async {
    final controller = _cameraController;
    if (!mounted || !_cameraActive || controller == null ||
        !controller.value.isInitialized || controller.value.isStreamingImages) {
      return;
    }
    if (!_wsService.isConnected) {
      setState(() => _statusMessage = 'Connect to the server before starting.');
      return;
    }
    _lastFrameTime = null;
    await controller.startImageStream((CameraImage image) {
      if (!mounted || !_cameraActive || !_isStreaming ||
          controller != _cameraController || !_wsService.isConnected) {
        return;
      }
      final now = _frameClock.elapsedMilliseconds;
      if (_lastFrameTime != null && now - _lastFrameTime! < _frameIntervalMs) {
        return;
      }
      _lastFrameTime = now;
      try {
        _wsService.sendFrame(CameraFrameConverter.toJpeg(image));
      } catch (e) {
        // Stop a broken stream instead of silently showing "Analyzing" forever.
        setState(() {
          _isStreaming = false;
          _statusMessage = 'Frame conversion failed: $e';
        });
        unawaited(_queueCameraOperation(_stopCameraStream));
      }
    });
    if (!mounted || !_cameraActive) return;
    setState(() {
      _isStreaming = true;
      _statusMessage = 'Streaming to server...';
    });
  }

  Future<void> _stopCameraStream() async {
    _isStreaming = false;
    final controller = _cameraController;
    if (controller != null && controller.value.isStreamingImages) {
      await controller.stopImageStream();
    }
  }

  Future<void> _stopStreaming() {
    _isStreaming = false;
    return _queueCameraOperation(() async {
      await _stopCameraStream();
      if (mounted) setState(() => _statusMessage = 'Stopped. Tap Start to resume.');
    });
  }

  Future<void> _speak(String text) async {
    if (text.isEmpty) return;
    await _tts.speak(text);
  }

  Future<void> _saveTranslation() async {
    final translationProvider = context.read<TranslationProvider>();
    final authProvider = context.read<AuthProvider>();
    final current = translationProvider.currentTranslation;
    if (current.isEmpty) return;
    final userId = authProvider.userId ?? 'guest';
    final token = authProvider.token;
    try {
      await translationProvider.saveTranslation(userId, current, token: token);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Translation saved to history!'),
            backgroundColor: Color(0xFF2BB673),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to save translation: $e'),
            backgroundColor: Colors.red,
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    }
  }

  Future<void> _switchCamera() => _queueCameraOperation(() async {
    if (!mounted || !_cameraActive || _cameras.length < 2) return;
    final wasStreaming = _isStreaming;
    setState(() {
      _cameraInitialized = false;
      _statusMessage = 'Switching camera...';
    });
    _selectedCamera = (_selectedCamera + 1) % _cameras.length;
    await _initCamera(_cameras[_selectedCamera]);
    if (wasStreaming && mounted && _cameraActive && _cameraInitialized) {
      await _startCameraStream();
    }
  });

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _cameraActive = true;
      if (_cameras.isNotEmpty) {
        unawaited(_queueCameraOperation(() => _initCamera(_cameras[_selectedCamera])));
      }
    } else {
      _cameraActive = false;
      _isStreaming = false;
      unawaited(_queueCameraOperation(_releaseCamera));
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _cameraActive = false;
    _isStreaming = false;
    _wsService.onTranslation = null;
    _wsService.onConnectionChange = null;
    _wsService.onError = null;
    unawaited(_wsService.disconnect());
    // Chain cleanup after an in-progress start/stop/initialization operation.
    _cameraTask = _cameraTask.then((_) => _releaseCamera()).catchError((Object e) {
      debugPrint('Camera cleanup failed: $e');
    });
    _tts.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final translationProvider = context.watch<TranslationProvider>();
    final currentTranslation = translationProvider.currentTranslation;

    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          // ── Camera Preview ──
          if (_cameraInitialized && _cameraController != null)
            Positioned.fill(
              child: CameraPreview(_cameraController!),
            )
          else
            Positioned.fill(
              child: Container(
                color: Colors.black,
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.camera_alt,
                        color: Colors.white54, size: 64),
                    const SizedBox(height: 16),
                    Text(
                      _statusMessage,
                      style:
                          const TextStyle(color: Colors.white70, fontSize: 16),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            ),

          // ── Top Overlay ──
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              padding: EdgeInsets.only(
                top: MediaQuery.of(context).padding.top + 8,
                left: 16,
                right: 16,
                bottom: 12,
              ),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.black87, Colors.transparent],
                ),
              ),
              child: Row(
                children: [
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                          color: Colors.white.withValues(alpha: 0.3), width: 1),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 7,
                          height: 7,
                          decoration: BoxDecoration(
                            color:
                                _isStreaming ? Colors.greenAccent : Colors.grey,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          _isStreaming ? 'LIVE' : 'OFFLINE',
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            letterSpacing: 1.2,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const Spacer(),
                  // Connection indicator
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: translationProvider.isConnected
                          ? Colors.green.withValues(alpha: 0.3)
                          : Colors.red.withValues(alpha: 0.3),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          translationProvider.isConnected
                              ? Icons.cloud_done
                              : Icons.cloud_off,
                          color: translationProvider.isConnected
                              ? Colors.greenAccent
                              : Colors.redAccent,
                          size: 14,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          translationProvider.isConnected
                              ? 'Server'
                              : 'No Server',
                          style: const TextStyle(
                              color: Colors.white, fontSize: 11),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Inference Mode Toggle
                  GestureDetector(
                    onTap: () {
                      final modes = ['frames', 'video', 'hybrid'];
                      final next = modes[(modes.indexOf(_inferenceMode) + 1) % modes.length];
                      setState(() => _inferenceMode = next);
                      _wsService.sendConfig(next);
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text('Mode: ${next.toUpperCase()}'),
                          duration: const Duration(milliseconds: 500),
                        ),
                      );
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                      decoration: BoxDecoration(
                        color: Colors.blueAccent.withValues(alpha: 0.3),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: Colors.blueAccent.withValues(alpha: 0.5)),
                      ),
                      child: Row(
                        children: [
                          Icon(
                            _inferenceMode == 'frames' ? Icons.bolt : 
                            _inferenceMode == 'video' ? Icons.movie : Icons.auto_awesome,
                            color: Colors.blueAccent, size: 14,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            _inferenceMode.toUpperCase(),
                            style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.bold),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Switch Camera
                  if (_cameras.length > 1)
                    GestureDetector(
                      onTap: _cameraOperations == 0 ? _switchCamera : null,
                      child: Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.15),
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.flip_camera_ios,
                            color: Colors.white, size: 20),
                      ),
                    ),
                ],
              ),
            ),
          ),

          // ── Bottom Translation Panel ──
          Positioned(
            bottom: 0,
            left: 0,
            right: 0,
            child: Container(
              padding: EdgeInsets.only(
                left: 20,
                right: 20,
                top: 20,
                bottom: MediaQuery.of(context).padding.bottom + 20,
              ),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.bottomCenter,
                  end: Alignment.topCenter,
                  colors: [Colors.black87, Colors.transparent],
                ),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // Translation result box
                  if (currentTranslation.isNotEmpty) ...[
                    AnimatedContainer(
                      duration: const Duration(milliseconds: 300),
                      width: double.infinity,
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(
                            color: Colors.white.withValues(alpha: 0.2), width: 1),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              const Text(
                                'DETECTED',
                                style: TextStyle(
                                    color: Colors.greenAccent,
                                    fontSize: 10,
                                    fontWeight: FontWeight.bold,
                                    letterSpacing: 1.5),
                              ),
                              const Spacer(),
                              if (_confidence > 0)
                                Text(
                                  '${(_confidence * 100).toStringAsFixed(0)}%',
                                  style: const TextStyle(
                                      color: Colors.white70, fontSize: 12),
                                ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            currentTranslation,
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 28,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          // Confidence bar
                          if (_confidence > 0) ...[
                            const SizedBox(height: 8),
                            ClipRRect(
                              borderRadius: BorderRadius.circular(4),
                              child: LinearProgressIndicator(
                                value: _confidence,
                                backgroundColor: Colors.white.withValues(alpha: 0.2),
                                valueColor: const AlwaysStoppedAnimation(
                                    Colors.greenAccent),
                                minHeight: 4,
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    // Action buttons
                    Row(
                      children: [
                        _actionButton(
                          icon: _isSpeaking
                              ? Icons.volume_up
                              : Icons.volume_up_outlined,
                          label: 'Speak',
                          color: const Color(0xFFE67E22),
                          onTap: () => _speak(currentTranslation),
                          isActive: _isSpeaking,
                        ),
                        const SizedBox(width: 8),
                        _actionButton(
                          icon: Icons.save_outlined,
                          label: 'Save',
                          color: const Color(0xFF2BB673),
                          onTap: _saveTranslation,
                        ),
                        const SizedBox(width: 8),
                        _actionButton(
                          icon: Icons.clear,
                          label: 'Clear',
                          color: Colors.red.shade400,
                          onTap: () => context
                              .read<TranslationProvider>()
                              .clearCurrentTranslation(),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                  ] else if (_isStreaming) ...[
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 12),
                      margin: const EdgeInsets.only(bottom: 12),
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white70,
                            ),
                          ),
                          SizedBox(width: 10),
                          Text(
                            'Analyzing gestures...',
                            style: TextStyle(color: Colors.white70),
                          ),
                        ],
                      ),
                    ),
                  ],

                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(_statusMessage,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: Colors.white70, fontSize: 12)),
                  ),
                  // Start / Stop button
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: _isStreaming
                            ? Colors.red.shade600
                            : const Color(0xFF4B6CF7),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(14),
                        ),
                        elevation: 4,
                      ),
                      onPressed: _cameraInitialized && _cameraOperations == 0
                          ? (_isStreaming ? _stopStreaming : _startStreaming)
                          : null,
                      icon: Icon(_isStreaming
                          ? Icons.stop_circle_outlined
                          : Icons.play_circle_outlined),
                      label: Text(
                        _isStreaming ? 'Stop Streaming' : 'Start Translating',
                        style: const TextStyle(
                            fontSize: 16, fontWeight: FontWeight.w600),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _actionButton({
    required IconData icon,
    required String label,
    required Color color,
    required VoidCallback onTap,
    bool isActive = false,
  }) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            color: isActive
                ? color.withValues(alpha: 0.4)
                : Colors.white.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: isActive ? color : Colors.white.withValues(alpha: 0.2),
              width: 1,
            ),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: isActive ? color : Colors.white, size: 22),
              const SizedBox(height: 4),
              Text(label,
                  style: TextStyle(
                    color: isActive ? color : Colors.white70,
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  )),
            ],
          ),
        ),
      ),
    );
  }
}
