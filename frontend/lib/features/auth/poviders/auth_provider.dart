import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../../core/services/api_service.dart';

class AuthProvider extends ChangeNotifier {
  String? _userId;
  String? _email;
  bool _isLoading = false;
  String? _error;

  String? get userId => _userId;
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
    _email = prefs.getString('email');
    notifyListeners();
  }

  Future<bool> login(String email, String password) async {
    _isLoading = true;
    _error = null;
    notifyListeners();

    try {
      final userId = await ApiService.login(email, password);
      if (userId.isNotEmpty) {
        _userId = userId;
        _email = email;
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('userId', userId);
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
      final userId = await ApiService.register(email, password);
      if (userId.isNotEmpty) {
        _userId = userId;
        _email = email;
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('userId', userId);
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
      final userId = await ApiService.forgotPassword(email);
      if (userId.isNotEmpty) {
        _userId = userId;
        _email = email;
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('userId', userId);
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

  Future<void> logout() async {
    _userId = null;
    _email = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('userId');
    await prefs.remove('email');
    notifyListeners();
  }
}
