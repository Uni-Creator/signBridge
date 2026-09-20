import 'dart:async';
import 'dart:convert';

import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sign_bridge/features/translate/providers/translation_provider.dart';

Future<T> _run<T>(
  Future<T> Function() call,
  FutureOr<http.Response> Function(http.Request request) handler,
) {
  return http.runWithClient(
    call,
    () => MockClient((request) async => await handler(request)),
  );
}

void main() {
  setUp(() {
    dotenv.loadFromString(envString: 'API_BASE_URL=https://example.test');
    SharedPreferences.setMockInitialValues({});
  });

  group('TranslationProvider - Caching', () {
    test('loads history from backend on initial fetch and persists to cache', () async {
      final provider = TranslationProvider();
      int backendHitCount = 0;

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          backendHitCount++;
          expect(request.url.path, '/history');
          return http.Response(
            jsonEncode({
              'history': [
                {
                  'id': 'h1',
                  'translation': 'Hello',
                  'timestamp': '2026-09-20T10:00:00.000Z',
                },
              ],
            }),
            200,
          );
        },
      );

      expect(backendHitCount, 1);
      expect(provider.history.length, 1);
      expect(provider.history.first.id, 'h1');
      expect(provider.history.first.translation, 'Hello');

      // Subsequent load without force should hit in-memory cache and NOT hit backend
      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          backendHitCount++;
          return http.Response('{}', 200);
        },
      );

      expect(backendHitCount, 1, reason: 'Should serve from memory cache without backend request');
    });

    test('force = true bypasses cache and queries backend', () async {
      final provider = TranslationProvider();
      int backendHitCount = 0;

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          backendHitCount++;
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'h1', 'translation': 'Hello', 'timestamp': '2026-09-20T10:00:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(backendHitCount, 1);

      // Refresh with force = true
      await _run(
        () => provider.loadHistory('user1', token: 'tok1', force: true),
        (request) async {
          backendHitCount++;
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'h2', 'translation': 'World', 'timestamp': '2026-09-20T10:05:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(backendHitCount, 2);
      expect(provider.history.length, 1);
      expect(provider.history.first.id, 'h2');
    });

    test('restores cached items from SharedPreferences for user if memory was empty', () async {
      SharedPreferences.setMockInitialValues({
        'history_cache_user1': jsonEncode([
          {'id': 'persisted_1', 'translation': 'Cached text', 'timestamp': '2026-09-20T08:00:00.000Z'},
        ]),
      });

      final provider = TranslationProvider();
      // int backendHitCount = 0;

      // When loadHistory is called, it loads the persistent cache immediately
      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          // backendHitCount++;
          return http.Response(
            jsonEncode({'history': []}),
            200,
          );
        },
      );

      // Memory should have been populated from persistent cache before backend call
      expect(provider.history.isNotEmpty, true);
    });
  });

  group('TranslationProvider - Optimistic Delete', () {
    test('removes item immediately and keeps removed on backend success', () async {
      final provider = TranslationProvider();

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'item1', 'translation': 'One', 'timestamp': '2026-09-20T10:00:00.000Z'},
                {'id': 'item2', 'translation': 'Two', 'timestamp': '2026-09-20T10:01:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(provider.history.length, 2);

      await _run(
        () => provider.deleteHistoryItem('item1', token: 'tok1'),
        (request) async {
          expect(request.url.path, '/history/item1');
          expect(request.method, 'DELETE');
          return http.Response(jsonEncode({'success': true}), 200);
        },
      );

      expect(provider.history.length, 1);
      expect(provider.history.first.id, 'item2');
    });

    test('rolls back removed item to its original position if backend delete fails', () async {
      final provider = TranslationProvider();

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'item1', 'translation': 'One', 'timestamp': '2026-09-20T10:00:00.000Z'},
                {'id': 'item2', 'translation': 'Two', 'timestamp': '2026-09-20T10:01:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(provider.history.length, 2);

      // Expect exception when backend returns error
      await expectLater(
        _run(
          () => provider.deleteHistoryItem('item1', token: 'tok1'),
          (request) async => http.Response(jsonEncode({'error': 'Server error'}), 500),
        ),
        throwsA(isA<Exception>()),
      );

      // Item1 should be restored back at index 0
      expect(provider.history.length, 2);
      expect(provider.history[0].id, 'item1');
      expect(provider.history[1].id, 'item2');
    });
  });

  group('TranslationProvider - Optimistic Store', () {
    test('inserts item immediately and updates ID with backend response without refetching', () async {
      final provider = TranslationProvider();

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'old1', 'translation': 'Old item', 'timestamp': '2026-09-20T09:00:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(provider.history.length, 1);

      await _run(
        () => provider.saveTranslation('user1', 'New translation', token: 'tok1'),
        (request) async {
          expect(request.url.path, '/history/store');
          expect(request.method, 'POST');
          return http.Response(
            jsonEncode({
              'id': 'backend_generated_id',
              'translation': 'New translation',
              'timestamp': '2026-09-20T11:00:00.000Z',
            }),
            201,
          );
        },
      );

      expect(provider.history.length, 2);
      expect(provider.history[0].id, 'backend_generated_id');
      expect(provider.history[0].translation, 'New translation');
      expect(provider.history[1].id, 'old1');
    });

    test('rolls back inserted item if backend store fails', () async {
      final provider = TranslationProvider();

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'old1', 'translation': 'Old item', 'timestamp': '2026-09-20T09:00:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(provider.history.length, 1);

      await expectLater(
        _run(
          () => provider.saveTranslation('user1', 'New translation', token: 'tok1'),
          (request) async => http.Response(jsonEncode({'error': 'Save failed'}), 500),
        ),
        throwsA(isA<Exception>()),
      );

      // List should only contain the original item; optimistic item must be gone
      expect(provider.history.length, 1);
      expect(provider.history[0].id, 'old1');
    });
  });

  group('TranslationProvider - Optimistic Clear', () {
    test('clears list immediately and restores on backend failure', () async {
      final provider = TranslationProvider();

      await _run(
        () => provider.loadHistory('user1', token: 'tok1'),
        (request) async {
          return http.Response(
            jsonEncode({
              'history': [
                {'id': 'item1', 'translation': 'One', 'timestamp': '2026-09-20T10:00:00.000Z'},
              ],
            }),
            200,
          );
        },
      );

      expect(provider.history.length, 1);

      await expectLater(
        _run(
          () => provider.clearHistory(token: 'tok1'),
          (request) async => http.Response(jsonEncode({'error': 'Clear failed'}), 500),
        ),
        throwsA(isA<Exception>()),
      );

      expect(provider.history.length, 1);
      expect(provider.history[0].id, 'item1');
    });
  });
}
