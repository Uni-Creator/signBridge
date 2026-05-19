import 'dart:async';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:provider/provider.dart';
import 'package:image/image.dart' as img;

import '../../auth/poviders/auth_provider.dart';
import '../providers/translation_provider.dart';
import '../../../core/services/websocket_service.dart';

class TranslateScreen extends StatefulWidget {
  const TranslateScreen({super.key});

  @override
  State<TranslateScreen> createState() => _TranslateScreenState();
}

class _TranslateScreenState extends State<TranslateScreen>
    with WidgetsBindingObserver {
  CameraController? _cameraController;
  List<CameraDescription> _cameras = [];
  int _selectedCamera = 0;

  final WebSocketService _wsService = WebSocketService();
  final FlutterTts _tts = FlutterTts();

  bool _cameraInitialized = false;
  bool _isStreaming = false;
  bool _isSpeaking = false;
  String _statusMessage = 'Camera not started';
  double _confidence = 0;
  Timer? _frameTimer;

  // Inference Configuration
  String _inferenceMode = 'hybrid'; // frames, video, hybrid

  // Frame Throttling
  DateTime? _lastFrameTime;
  static const int FRAME_INTERVAL_MS = 80; // ~12 FPS

  // static const primaryColor = Color(0xFF2B2D5D);
  // static const accentColor = Color(0xFF4B6CF7);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initTts();
    _loadCameras();
    _setupWebSocket();
    _wsService.connect();
  }

  void _initTts() async {
    await _tts.setLanguage('en-US');
    await _tts.setSpeechRate(0.5);
    _tts.setStartHandler(() => setState(() => _isSpeaking = true));
    _tts.setCompletionHandler(() => setState(() => _isSpeaking = false));
  }

  Future<void> _loadCameras() async {
    try {
      _cameras = await availableCameras();
      if (_cameras.isNotEmpty) {
        await _initCamera(_cameras[_selectedCamera]);
      } else {
        setState(() => _statusMessage = 'No cameras found');
      }
    } catch (e) {
      setState(() => _statusMessage = 'Camera error: $e');
    }
  }

  Future<void> _initCamera(CameraDescription cam) async {
    // Dispose previous controller
    await _cameraController?.dispose();

    final controller = CameraController(
      cam,
      ResolutionPreset.medium,
      enableAudio: false,
      imageFormatGroup: ImageFormatGroup.jpeg,
    );
    _cameraController = controller;

    try {
      await controller.initialize();
      if (!mounted) return;
      setState(() {
        _cameraInitialized = true;
        _statusMessage = 'Camera ready. Tap Start to begin.';
      });
    } catch (e) {
      setState(() => _statusMessage = 'Camera init failed: $e');
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

  Future<void> _startStreaming() async {
    setState(() {
      _isStreaming = true;
      _statusMessage = 'Streaming to server...';
    });

    _cameraController?.startImageStream((CameraImage image) async {
      if (!_isStreaming || !_wsService.isConnected) return;

      final now = DateTime.now();
      if (_lastFrameTime != null && 
          now.difference(_lastFrameTime!).inMilliseconds < FRAME_INTERVAL_MS) {
        return;
      }
      _lastFrameTime = now;

      try {
        final jpegBytes = await _convertToPredictableJpeg(image);
        if (jpegBytes != null) {
          _wsService.sendFrame(jpegBytes);
        }
      } catch (e) {
        debugPrint('Frame stream error: $e');
      }
    });
  }


  Future<Uint8List?> _convertToPredictableJpeg(CameraImage image) async {
    try {
      // Manual conversion and resize to 224x224 (checklist mandatory)
      final dartImg = _convertCameraImage(image);
      if (dartImg == null) return null;

      final resized = img.copyResize(dartImg, width: 224, height: 224);
      return Uint8List.fromList(img.encodeJpg(resized, quality: 85));
    } catch (e) {
      return null;
    }
  }

  img.Image? _convertCameraImage(CameraImage image) {
    try {
      final int width = image.width;
      final int height = image.height;
      final img.Image res = img.Image(width: width, height: height);

      // Simple YUV420 to RGB conversion (fast enough for 224x224)
      if (image.format.group == ImageFormatGroup.yuv420) {
        final Plane yPlane = image.planes[0];
        final Plane uPlane = image.planes[1];
        final Plane vPlane = image.planes[2];

        for (int y = 0; y < height; y++) {
          for (int x = 0; x < width; x++) {
            final int yPos = y * yPlane.bytesPerRow + x;
            final int uvPos = (y >> 1) * uPlane.bytesPerRow + (x >> 1);

            final int yp = yPlane.bytes[yPos];
            final int up = uPlane.bytes[uvPos];
            final int vp = vPlane.bytes[uvPos];

            // Standard YUV to RGB
            int r = (yp + 1.402 * (vp - 128)).round().clamp(0, 255);
            int g = (yp - 0.344136 * (up - 128) - 0.714136 * (vp - 128)).round().clamp(0, 255);
            int b = (yp + 1.772 * (up - 128)).round().clamp(0, 255);

            res.setPixelRgb(x, y, r, g, b);
          }
        }
        return res;
      } else if (image.format.group == ImageFormatGroup.bgra8888) {
        // iOS / Some Androids
        return img.Image.fromBytes(
          width: width,
          height: height,
          bytes: image.planes[0].bytes.buffer,
          format: img.Format.uint8,
          numChannels: 4,
        );
      }
    } catch (e) {
      debugPrint('Conversion error: $e');
    }
    return null;
  }

  Future<void> _stopStreaming() async {
    await _cameraController?.stopImageStream();
    setState(() {
      _isStreaming = false;
      _statusMessage = 'Stopped. Tap Start to resume.';
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
    await translationProvider.saveTranslation(userId, current);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Translation saved to history!'),
          backgroundColor: Color(0xFF2BB673),
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  void _switchCamera() async {
    if (_cameras.length < 2 || _cameraController == null) return;
    
    final bool wasStreaming = _isStreaming;
    
    // Stop and UI feedback
    if (wasStreaming) await _stopStreaming();
    
    setState(() {
      _cameraInitialized = false;
      _statusMessage = 'Switching camera...';
    });

    // Cycle index
    _selectedCamera = (_selectedCamera + 1) % _cameras.length;

    // Dispose and Re-init
    await _cameraController?.dispose();
    _cameraController = null;
    
    await _initCamera(_cameras[_selectedCamera]);
    
    // Resume streaming if needed
    if (wasStreaming && _cameraInitialized) {
      await _startStreaming();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (_cameraController == null) {
      return;
    }
    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) {
      _stopStreaming();
      _cameraController?.dispose();
      setState(() {
        _cameraInitialized = false;
      });
    } else if (state == AppLifecycleState.resumed) {
      if (_cameras.isNotEmpty) {
        _initCamera(_cameras[_selectedCamera]);
      }
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _frameTimer?.cancel();
    _wsService.disconnect();
    _cameraController?.dispose();
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
                      color: Colors.white.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                          color: Colors.white.withOpacity(0.3), width: 1),
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
                          ? Colors.green.withOpacity(0.3)
                          : Colors.red.withOpacity(0.3),
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
                        color: Colors.blueAccent.withOpacity(0.3),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: Colors.blueAccent.withOpacity(0.5)),
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
                      onTap: _switchCamera,
                      child: Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.15),
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
                        color: Colors.white.withOpacity(0.12),
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(
                            color: Colors.white.withOpacity(0.2), width: 1),
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
                                backgroundColor: Colors.white.withOpacity(0.2),
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
                        color: Colors.white.withOpacity(0.1),
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
                      onPressed: _cameraInitialized
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
                ? color.withOpacity(0.4)
                : Colors.white.withOpacity(0.15),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: isActive ? color : Colors.white.withOpacity(0.2),
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
