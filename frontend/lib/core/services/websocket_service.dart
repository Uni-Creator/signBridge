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

/// WebSocket frame transports.
///
/// Must stay in sync with the server's VALID_TRANSPORTS
/// (websocket_handler.py). This is the transport for THIS socket only -
/// it has nothing to do with the ISLF container used to talk to
/// ISLModelAPI.
abstract class WsTransport {
  static const jpegBinary = 'jpeg_binary';
  static const jsonBase64 = 'json_base64';
  static const h264 = 'h264';
  static const h265 = 'h265';

  /// Transports that can actually carry frames today. h264/h265 are
  /// reserved in the protocol but not implemented client- or
  /// server-side yet.
  static const implemented = {jpegBinary, jsonBase64};
}

/// Thrown by [WebSocketService.sendFrame] when asked to send a frame
/// under a transport that is reserved but not implemented yet.
class TransportNotImplementedException implements Exception {
  final String transport;
  TransportNotImplementedException(this.transport);

  @override
  String toString() =>
      'TransportNotImplementedException: $transport is reserved but not implemented';
}

class WebSocketService {
  static String get wsUrl => dotenv.env['SLT_WS_URL']!;

  WebSocketChannel? _channel;
  bool _isConnected = false;
  StreamSubscription? _subscription;

  /// Transport negotiated with the server for this connection.
  /// Defaults to the server's own default so pre-handshake behavior
  /// (if sendConfig is never called) still matches.
  String _transport = WsTransport.jpegBinary;
  String get transport => _transport;

  OnTranslationCallback? onTranslation;
  OnErrorCallback? onError;
  OnConnectionCallback? onConnectionChange;

  bool get isConnected => _isConnected;

  Future<void> connect(String token) async {
    try {
      final uri = Uri.parse(wsUrl);

      // Firebase ID token stays in the Authorization header. Never put
      // it in the query string (it would end up in server/proxy logs).
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

      // Config handshake acknowledgment.
      if (data['type'] == 'config_ack') {
        final status = data['status'];

        if (status == 'accepted') {
          // Server confirmed the transport we asked for; nothing else
          // to do, sendFrame() already uses _transport.
          return;
        }

        // 'error' or 'not_implemented'.
        onError?.call(
          (data['error'] as String?) ??
              'Config rejected for transport ${data['transport']}',
        );
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

      // Server-side error (e.g. "Transport not implemented", "Invalid frame").
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

  /// Send one frame using whichever transport was last negotiated via
  /// [sendConfig].
  ///
  /// - jpeg_binary: sent as a raw binary WebSocket message (unchanged
  ///   production path).
  /// - json_base64: sent as {"type": "frame", "frame": "<base64>"}
  ///   (debugging/Postman path).
  /// - h264 / h265: reserved but not implemented anywhere in the
  ///   pipeline yet. Throws instead of silently sending bytes the
  ///   server will reject.
  ///
  /// Call [sendConfig] once, right after [connect], before calling
  /// this.
  void sendFrame(Uint8List jpegBytes) {
    if (!_isConnected || _channel == null) return;

    switch (_transport) {
      case WsTransport.jpegBinary:
        _channel!.sink.add(jpegBytes);
        break;

      case WsTransport.jsonBase64:
        _channel!.sink.add(
          jsonEncode({
            'type': 'frame',
            'frame': base64Encode(jpegBytes),
          }),
        );
        break;

      case WsTransport.h264:
      case WsTransport.h265:
        throw TransportNotImplementedException(_transport);

      default:
        throw TransportNotImplementedException(_transport);
    }
  }

  /// Negotiate mode/transport with the server. Call this immediately
  /// after [connect] resolves and BEFORE sending any frames - the
  /// server rejects frames sent before a valid config handshake for
  /// json_base64, and h264/h265 frames are rejected outright.
  ///
  /// Supported modes:
  /// - frames
  /// - video
  /// - hybrid
  ///
  /// Supported transports (see [WsTransport]):
  /// - jpeg_binary (default, production)
  /// - json_base64 (debugging/Postman)
  /// - h264 (reserved, not implemented)
  /// - h265 (reserved, not implemented)
  void sendConfig(
    String mode, {
    String transport = WsTransport.jpegBinary,
  }) {
    if (!_isConnected || _channel == null) return;

    _transport = transport;

    _channel!.sink.add(
      jsonEncode({
        'type': 'config',
        'version': 1,
        'mode': mode,
        'transport': transport,
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