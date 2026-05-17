import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;

class ApiService {
  // Change this to your local machine IP when running on a physical device
  // Use 10.0.2.2 for Android emulator
  static String get baseUrl => dotenv.env['API_BASE_URL']!;

  static Future<String> login(String email, String password) async {
    final response = await http.post(
      Uri.parse('$baseUrl/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    ).timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);
    return data['id'] ?? '';
  }

  static Future<String> register(String email, String password) async {
    final response = await http.post(
      Uri.parse('$baseUrl/register'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    ).timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);
    return data['id'] ?? '';
  }

  static Future<List<String>> getHistory(String userId) async {
    final response = await http.get(
      Uri.parse('$baseUrl/history?id=$userId'),
      headers: {'Content-Type': 'application/json'},
    ).timeout(const Duration(seconds: 10));

    final data = jsonDecode(response.body);
    final hist = data['history'];
    if (hist is List) return hist.cast<String>();
    return [];
  }

  static Future<void> postHistory(String userId, String translation) async {
    await http.post(
      Uri.parse('$baseUrl/history'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'id': userId, 'translation': translation}),
    ).timeout(const Duration(seconds: 10));
  }
}
