import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../../core/services/api_service.dart';

class AuthProvider extends ChangeNotifier {
  String? _userId;
  String? _token;
  String? _email;
  bool _isLoading = false;
  String? _error;

  String? get userId => _userId;
  String? get token => _token;
  String? get email => _email;
  bool get isLoading => _isLoading;
  String? get error => _error;
  bool get isLoggedIn => _userId != null && _userId!.isNotEmpty;

  late final Future<void> initialization;

  AuthProvider() {
    initialization = _loadSession();
  }

  Future<void> _loadSession() async {
    final prefs = await SharedPreferences.getInstance();
    _userId = prefs.getString('userId');
    _token = prefs.getString('token');
    _email = prefs.getString('email');
    notifyListeners();
  }

  Future<bool> login(String email, String password) async {
    _isLoading = true;
    _error = null;
    notifyListeners();

    try {
      final authData = await ApiService.login(email, password);
      final userId = authData['id'] ?? '';
      final token = authData['token'] ?? '';
      if (userId.isNotEmpty) {
        _userId = userId;
        _token = token;
        _email = email;
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('userId', userId);
        if (token.isNotEmpty) await prefs.setString('token', token);
        await prefs.setString('email', email);
        _isLoading = false;
        notifyListeners();
        return true;
      } else {
        _error = 'Invalid credentials. Please try again.';
      }
    } catch (e) {
      _error = 'Connection error. Is the server running?';
    }

    _isLoading = false;
    notifyListeners();
    return false;
  }

  Future<bool> register(String email, String password) async {
    _isLoading = true;
    _error = null;
    notifyListeners();

    try {
      final authData = await ApiService.register(email, password);
      final userId = authData['id'] ?? '';
      final token = authData['token'] ?? '';
      if (userId.isNotEmpty) {
        _userId = userId;
        _token = token;
        _email = email;
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('userId', userId);
        if (token.isNotEmpty) await prefs.setString('token', token);
        await prefs.setString('email', email);
        _isLoading = false;
        notifyListeners();
        return true;
      } else {
        debugPrint(userId);
        _error = 'Registration failed. Email may already be in use.';
      }
    } catch (e) {
      debugPrint(e.toString());
      _error = 'Connection error. Is the server running?';
    }

    _isLoading = false;
    notifyListeners();
    return false;
  }

  Future<bool> forgotPassword(String email) async {
    _isLoading = true;
    _error = null;
    notifyListeners();

    try {
      final resId = await ApiService.forgotPassword(email);
      if (resId.isNotEmpty) {
        _isLoading = false;
        notifyListeners();
        return true;
      } else {
        _error = 'Password reset failed.';
      }
    } catch (e) {
      debugPrint(e.toString());
      _error = 'Connection error. Is the server running?';
    }

    _isLoading = false;
    notifyListeners();
    return false;
  }

  Future<void> logout() async {
    _userId = null;
    _token = null;
    _email = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('userId');
    await prefs.remove('token');
    await prefs.remove('email');
    notifyListeners();
  }
}
