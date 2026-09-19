import 'dart:convert';

import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;

class ApiService {
  static String get baseUrl => dotenv.env['API_BASE_URL']!;

  // Authentication

  static Future<Map<String, String>> login(
    String email,
    String password,
  ) async {
    final response = await http
        .post(
          Uri.parse('$baseUrl/login'),
          headers: {
            'Content-Type': 'application/json',
          },
          body: jsonEncode({
            'email': email,
            'password': password,
          }),
        )
        .timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception(
        data['error']?.toString() ?? 'Login failed',
      );
    }

    return {
      'id': data['id']?.toString() ?? '',
      'token': data['token']?.toString() ?? '',
    };
  }

  static Future<Map<String, String>> register(
    String email,
    String password,
  ) async {
    final response = await http
        .post(
          Uri.parse('$baseUrl/register'),
          headers: {
            'Content-Type': 'application/json',
          },
          body: jsonEncode({
            'email': email,
            'password': password,
          }),
        )
        .timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception(
        data['error']?.toString() ?? 'Registration failed',
      );
    }

    return {
      'id': data['id']?.toString() ?? '',
      'token': data['token']?.toString() ?? '',
    };
  }

  static Future<bool> forgotPassword(
    String email,
  ) async {
    final response = await http
        .post(
          Uri.parse('$baseUrl/forgot-password'),
          headers: {
            'Content-Type': 'application/json',
          },
          body: jsonEncode({
            'email': email,
          }),
        )
        .timeout(const Duration(seconds: 10));

    if (response.statusCode < 200 ||
        response.statusCode >= 300) {
      return false;
    }

    final data = jsonDecode(response.body);
    final success = data['success'];

    return success == true ||
        (success is String && success.trim().isNotEmpty);
  }

  // History

  static Future<List<Map<String, String>>> getHistory(
    String token,
  ) async {
    final response = await http
        .get(
          Uri.parse('$baseUrl/history'),
          headers: {
            'Authorization': 'Bearer $token',
          },
        )
        .timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);

    if (response.statusCode < 200 ||
        response.statusCode >= 300) {
      throw Exception(
        data['error']?.toString() ?? 'Failed to retrieve history',
      );
    }

    final history = data['history'];

    if (history is! List) {
      return [];
    }

    return history
        .whereType<Map>()
        .map<Map<String, String>>(
          (item) => {
            'id': item['id']?.toString() ?? '',
            'translation':
                item['translation']?.toString() ?? '',
            'timestamp':
                item['timestamp']?.toString() ?? '',
          },
        )
        .toList();
  }

  static Future<void> postHistory(
    String translation,
    String token,
  ) async {
    final response = await http
        .post(
          Uri.parse('$baseUrl/history'),
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer $token',
          },
          body: jsonEncode({
            'translation': translation,
          }),
        )
        .timeout(const Duration(seconds: 10));

    if (response.statusCode < 200 ||
        response.statusCode >= 300) {
      final data = jsonDecode(response.body);

      throw Exception(
        data['error']?.toString() ??
            data['detail']?.toString() ??
            'Failed to store history',
      );
    }
  }
}