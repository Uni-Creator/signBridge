import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:sign_bridge/core/services/api_service.dart';

void main() {
  setUp(() {
    dotenv.loadFromString(envString: 'API_BASE_URL=https://example.test');
  });

  Future<bool> resetWith(String body, int status) {
    return http.runWithClient(
      () => ApiService.forgotPassword('person@example.test'),
      () => MockClient((request) async {
        expect(request.url.path, '/forgot-password');
        expect(request.method, 'POST');
        return http.Response(body, status);
      }),
    );
  }

  test('accepts backend confirmation message', () async {
    expect(
        await resetWith(
            '{"success":"Password reset email sent successfully."}', 200),
        isTrue);
  });

  test('accepts boolean success', () async {
    expect(await resetWith('{"success":true}', 200), isTrue);
  });

  test('rejects empty, missing and false success', () async {
    for (final body in [
      '{"success":""}',
      '{"success":" "}',
      '{"success":false}',
      '{}'
    ]) {
      expect(await resetWith(body, 200), isFalse);
    }
  });

  test('does not treat an HTTP failure as success', () async {
    expect(await resetWith('{"success":"sent"}', 500), isFalse);
  });
}
