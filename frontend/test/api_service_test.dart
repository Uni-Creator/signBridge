import 'dart:async';
import 'dart:convert';

import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:sign_bridge/core/services/api_service.dart';

/// Runs [call] with an [http.Client] backed by [handler], the way
/// `http.runWithClient` scopes a mock client to the zone the call runs in.
Future<T> _run<T>(
  Future<T> Function() call,
  FutureOr<http.Response> Function(http.Request request) handler,
) {
  return http.runWithClient(
    call,
    () => MockClient((request) async => await handler(request)),
  );
}

/// Matches an [Exception] whose message (via `toString`) is exactly [message].
Matcher _exceptionWithMessage(String message) => predicate(
      (e) => e is Exception && e.toString() == 'Exception: $message',
      'Exception: $message',
    );

void main() {
  setUp(() {
    dotenv.loadFromString(envString: 'API_BASE_URL=https://example.test');
  });

  group('login', () {
    test('returns id and token, posting credentials as json', () async {
      late http.Request captured;
      final result = await _run(
        () => ApiService.login('a@b.com', 'secret'),
        (request) async {
          captured = request;
          return http.Response('{"id":"uid1","token":"tok1"}', 200);
        },
      );

      expect(result, {'id': 'uid1', 'token': 'tok1'});
      expect(captured.method, 'POST');
      expect(captured.url.path, '/login');
      expect(captured.headers['Content-Type'], 'application/json');
      expect(
        jsonDecode(captured.body),
        {'email': 'a@b.com', 'password': 'secret'},
      );
    });

    test('defaults a missing id or token to an empty string', () async {
      final result = await _run(
        () => ApiService.login('a@b.com', 'secret'),
        (request) async => http.Response('{}', 200),
      );
      expect(result, {'id': '', 'token': ''});
    });

    test('throws with the backend error message on failure', () async {
      final future = _run(
        () => ApiService.login('a@b.com', 'wrong'),
        (request) async => http.Response('{"error":"Login failed"}', 401),
      );
      await expectLater(future, throwsA(_exceptionWithMessage('Login failed')));
    });

    test('throws the default message when the backend omits one', () async {
      final future = _run(
        () => ApiService.login('a@b.com', 'wrong'),
        (request) async => http.Response('{}', 400),
      );
      await expectLater(future, throwsA(_exceptionWithMessage('Login failed')));
    });
  });

  group('register', () {
    test('returns id and token, posting credentials as json', () async {
      late http.Request captured;
      final result = await _run(
        () => ApiService.register('a@b.com', 'secret'),
        (request) async {
          captured = request;
          return http.Response('{"id":"uid1","token":"tok1"}', 200);
        },
      );

      expect(result, {'id': 'uid1', 'token': 'tok1'});
      expect(captured.method, 'POST');
      expect(captured.url.path, '/register');
      expect(captured.headers['Content-Type'], 'application/json');
      expect(
        jsonDecode(captured.body),
        {'email': 'a@b.com', 'password': 'secret'},
      );
    });

    test('defaults a missing id or token to an empty string', () async {
      final result = await _run(
        () => ApiService.register('a@b.com', 'secret'),
        (request) async => http.Response('{}', 200),
      );
      expect(result, {'id': '', 'token': ''});
    });

    test('throws with the backend error message on failure', () async {
      final future = _run(
        () => ApiService.register('a@b.com', 'secret'),
        (request) async =>
            http.Response('{"error":"Registration failed"}', 400),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Registration failed')),
      );
    });

    test('throws the default message when the backend omits one', () async {
      final future = _run(
        () => ApiService.register('a@b.com', 'secret'),
        (request) async => http.Response('{}', 500),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Registration failed')),
      );
    });
  });

  group('forgotPassword', () {
    test('posts to the correct endpoint', () async {
      late http.Request captured;
      await _run(
        () => ApiService.forgotPassword('person@example.test'),
        (request) async {
          captured = request;
          return http.Response('{"success":true}', 200);
        },
      );
      expect(captured.url.path, '/forgot-password');
      expect(captured.method, 'POST');
    });

    test('accepts backend confirmation message', () async {
      final result = await _run(
        () => ApiService.forgotPassword('person@example.test'),
        (request) async => http.Response(
          '{"success":"Password reset email sent successfully."}',
          200,
        ),
      );
      expect(result, isTrue);
    });

    test('accepts boolean success', () async {
      final result = await _run(
        () => ApiService.forgotPassword('person@example.test'),
        (request) async => http.Response('{"success":true}', 200),
      );
      expect(result, isTrue);
    });

    test('rejects empty, whitespace-only, missing and false success',
        () async {
      for (final body in [
        '{"success":""}',
        '{"success":" "}',
        '{"success":false}',
        '{}',
      ]) {
        final result = await _run(
          () => ApiService.forgotPassword('person@example.test'),
          (request) async => http.Response(body, 200),
        );
        expect(result, isFalse, reason: 'body: $body');
      }
    });

    test('does not treat an HTTP failure as success', () async {
      final result = await _run(
        () => ApiService.forgotPassword('person@example.test'),
        (request) async => http.Response('{"success":"sent"}', 500),
      );
      expect(result, isFalse);
    });
  });

  group('getHistory', () {
    test('sends the bearer token and returns parsed items', () async {
      late http.Request captured;
      final result = await _run(
        () => ApiService.getHistory('tok'),
        (request) async {
          captured = request;
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 1, 'translation': 'hello', 'timestamp': '2025-01-01'},
                {'translation': 'bye'},
              ],
            }),
            200,
          );
        },
      );

      expect(captured.method, 'GET');
      expect(captured.url.path, '/history');
      expect(captured.headers['Authorization'], 'Bearer tok');
      expect(result, [
        {'id': '1', 'translation': 'hello', 'timestamp': '2025-01-01'},
        {'id': '', 'translation': 'bye', 'timestamp': ''},
      ]);
    });

    test('ignores non-map entries in the history list', () async {
      final result = await _run(
        () => ApiService.getHistory('tok'),
        (request) async => http.Response(
          jsonEncode({
            'history': ['not a map', 42],
          }),
          200,
        ),
      );
      expect(result, isEmpty);
    });

    test('returns an empty list when the history field is missing', () async {
      final result = await _run(
        () => ApiService.getHistory('tok'),
        (request) async => http.Response('{}', 200),
      );
      expect(result, isEmpty);
    });

    test('returns an empty list when the history field is not a list',
        () async {
      final result = await _run(
        () => ApiService.getHistory('tok'),
        (request) async => http.Response('{"history":"oops"}', 200),
      );
      expect(result, isEmpty);
    });

    test('throws with the backend error message on failure', () async {
      final future = _run(
        () => ApiService.getHistory('tok'),
        (request) async => http.Response(
          '{"error":"Failed to retrieve history"}',
          500,
        ),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Failed to retrieve history')),
      );
    });

    test('throws the default message when the backend omits one', () async {
      final future = _run(
        () => ApiService.getHistory('tok'),
        (request) async => http.Response('{}', 401),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Failed to retrieve history')),
      );
    });
  });

  group('postHistory', () {
    test('sends the translation and bearer token as json', () async {
      late http.Request captured;
      await _run(
        () => ApiService.postHistory('hello', 'tok'),
        (request) async {
          captured = request;
          return http.Response('', 201);
        },
      );

      expect(captured.method, 'POST');
      expect(captured.url.path, '/history/store');
      expect(captured.headers['Authorization'], 'Bearer tok');
      expect(captured.headers['Content-Type'], 'application/json');
      expect(jsonDecode(captured.body), {'translation': 'hello'});
    });

    test('completes without throwing on a 2xx response', () async {
      await expectLater(
        _run(
          () => ApiService.postHistory('hello', 'tok'),
          (request) async => http.Response('', 201),
        ),
        completes,
      );
    });

    test('returns id, translation, and timestamp when provided in response', () async {
      final result = await _run(
        () => ApiService.postHistory('hello', 'tok'),
        (request) async => http.Response(
          jsonEncode({
            'id': 'hist_123',
            'translation': 'hello',
            'timestamp': '2026-09-20T12:00:00.000Z',
          }),
          201,
        ),
      );

      expect(result['id'], 'hist_123');
      expect(result['translation'], 'hello');
      expect(result['timestamp'], '2026-09-20T12:00:00.000Z');
    });

    test('throws using the error field when present', () async {
      final future = _run(
        () => ApiService.postHistory('hello', 'tok'),
        (request) async => http.Response(
          '{"error":"Failed to store history"}',
          500,
        ),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Failed to store history')),
      );
    });

    test('falls back to the detail field when error is missing', () async {
      final future = _run(
        () => ApiService.postHistory('hello', 'tok'),
        (request) async => http.Response(
          '{"detail":"translation too long"}',
          400,
        ),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('translation too long')),
      );
    });

    test('uses the default message when neither field is present', () async {
      final future = _run(
        () => ApiService.postHistory('hello', 'tok'),
        (request) async => http.Response('{}', 500),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Failed to store history')),
      );
    });
  });

  group('deleteHistoryItem', () {
    test('sends DELETE to /history/<id> with bearer token', () async {
      late http.Request captured;
      await _run(
        () => ApiService.deleteHistoryItem('item123', 'tok'),
        (request) async {
          captured = request;
          return http.Response('{"message":"Translation deleted"}', 200);
        },
      );

      expect(captured.method, 'DELETE');
      expect(captured.url.path, '/history/item123');
      expect(captured.headers['Authorization'], 'Bearer tok');
    });

    test('completes without throwing on a 2xx response', () async {
      await expectLater(
        _run(
          () => ApiService.deleteHistoryItem('item123', 'tok'),
          (request) async => http.Response('', 200),
        ),
        completes,
      );
    });

    test('throws with the backend error message on failure', () async {
      final future = _run(
        () => ApiService.deleteHistoryItem('missing', 'tok'),
        (request) async => http.Response(
          '{"error":"Translation not found"}',
          404,
        ),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('Translation not found')),
      );
    });
  });

  group('clearHistory', () {
    test('sends DELETE to /history/clear with bearer token', () async {
      late http.Request captured;
      await _run(
        () => ApiService.clearHistory('tok'),
        (request) async {
          captured = request;
          return http.Response('{"message":"History deleted"}', 200);
        },
      );

      expect(captured.method, 'DELETE');
      expect(captured.url.path, '/history/clear');
      expect(captured.headers['Authorization'], 'Bearer tok');
    });

    test('completes without throwing on a 2xx response', () async {
      await expectLater(
        _run(
          () => ApiService.clearHistory('tok'),
          (request) async => http.Response('', 200),
        ),
        completes,
      );
    });

    test('throws with the backend error message on failure', () async {
      final future = _run(
        () => ApiService.clearHistory('tok'),
        (request) async => http.Response(
          '{"error":"No history found"}',
          404,
        ),
      );
      await expectLater(
        future,
        throwsA(_exceptionWithMessage('No history found')),
      );
    });
  });
}