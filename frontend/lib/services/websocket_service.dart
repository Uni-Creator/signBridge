import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:web_socket_channel/web_socket_channel.dart';

typedef OnTranslationCallback = void Function(String label, double confidence);
typedef OnErrorCallback = void Function(String error);
typedef OnConnectionCallback = void Function(bool connected);

class WebSocketService {
  static const String wsUrl = 'wss://signsight-8tpx.onrender.com/ws';

  WebSocketChannel? _channel;
  bool _isConnected = false;
  StreamSubscription? _subscription;

  OnTranslationCallback? onTranslation;
  OnErrorCallback? onError;
  OnConnectionCallback? onConnectionChange;

  bool get isConnected => _isConnected;

  Future<void> connect() async {
    try {
      _channel = WebSocketChannel.connect(Uri.parse(wsUrl));
      await _channel!.ready;
      _isConnected = true;
      onConnectionChange?.call(true);

      _subscription = _channel!.stream.listen(
        (message) {
          _handleMessage(message);
        },
        onError: (err) {
          _isConnected = false;
          onConnectionChange?.call(false);
          onError?.call('WebSocket error: $err');
        },
        onDone: () {
          _isConnected = false;
          onConnectionChange?.call(false);
        },
      );
    } catch (e) {
      _isConnected = false;
      onConnectionChange?.call(false);
      onError?.call('Failed to connect: $e');
    }
  }

  void _handleMessage(dynamic message) {
    try {
      final data = jsonDecode(message as String);
      if (data['label'] != null) {
        final label = data['label'] as String;
        final confidence = (data['confidence'] as num?)?.toDouble() ?? 1.0;
        onTranslation?.call(label, confidence);
      }
    } catch (e) {
      onError?.call('Parse error: $e');
    }
  }

  /// Send a JPEG frame as base64
  void sendFrame(Uint8List jpegBytes) {
    if (!_isConnected || _channel == null) return;
    final base64Frame = base64Encode(jpegBytes);
    _channel!.sink.add(jsonEncode({'frame': base64Frame}));
  }

  /// Change inference mode (frames, video, hybrid)
  void sendConfig(String mode) {
    if (!_isConnected || _channel == null) return;
    _channel!.sink.add(jsonEncode({'type': 'config', 'mode': mode}));
  }

  Future<void> disconnect() async {
    await _subscription?.cancel();
    await _channel?.sink.close();
    _isConnected = false;
    onConnectionChange?.call(false);
  }
}
