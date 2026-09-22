import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

typedef OnTranslationCallback = void Function(
  String label,
  double confidence,
);

typedef OnErrorCallback = void Function(String error);

typedef OnConnectionCallback = void Function(bool connected);

class WebSocketService {
  static String get wsUrl => dotenv.env['WS_URL']!;

  WebSocketChannel? _channel;
  bool _isConnected = false;
  StreamSubscription? _subscription;

  OnTranslationCallback? onTranslation;
  OnErrorCallback? onError;
  OnConnectionCallback? onConnectionChange;

  bool get isConnected => _isConnected;

  Future<void> connect(String token) async {
    try {
      final uri = Uri.parse(wsUrl);

      _channel = WebSocketChannel.connect(
        uri,
        headers: {
          'Authorization': 'Bearer $token',
        },
      );

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
      if (message is! String) {
        onError?.call('Invalid WebSocket message');
        return;
      }

      final data = jsonDecode(message);

      if (data is! Map<String, dynamic>) {
        onError?.call('Invalid WebSocket response');
        return;
      }

      // Model inference result.
      if (data['label'] != null) {
        final label = data['label'] as String;

        final confidence =
            (data['confidence'] as num?)?.toDouble() ?? 1.0;

        onTranslation?.call(label, confidence);
        return;
      }

      // Server-side error.
      if (data['error'] != null) {
        onError?.call(data['error'].toString());
        return;
      }

      // Informational/status messages.
      if (data['status'] != null) {
        return;
      }
    } catch (e) {
      onError?.call('Parse error: $e');
    }
  }

  /// Send a JPEG frame as base64.
  void sendFrame(Uint8List jpegBytes) {
    if (!_isConnected || _channel == null) return;

    final base64Frame = base64Encode(jpegBytes);

    _channel!.sink.add(
      jsonEncode({
        'frame': base64Frame,
      }),
    );
  }

  /// Change inference mode.
  ///
  /// Supported modes:
  /// - frames
  /// - video
  /// - hybrid
  void sendConfig(String mode) {
    if (!_isConnected || _channel == null) return;

    _channel!.sink.add(
      jsonEncode({
        'type': 'config',
        'mode': mode,
      }),
    );
  }

  /// Signal that all frames for the current video/sequence
  /// have been sent.
  ///
  /// The backend uses this to process the remaining buffered
  /// frames and return the final inference result.
  void sendEndOfStream() {
    if (!_isConnected || _channel == null) return;

    _channel!.sink.add(
      jsonEncode({
        'type': 'end',
      }),
    );
  }

  Future<void> disconnect() async {
    await _subscription?.cancel();
    _subscription = null;

    await _channel?.sink.close();
    _channel = null;

    _isConnected = false;
    onConnectionChange?.call(false);
  }
}