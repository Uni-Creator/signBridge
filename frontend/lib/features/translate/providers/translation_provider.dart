import 'package:flutter/material.dart';
import '../../../core/services/api_service.dart';

class HistoryItem {
  final String? id;
  final String translation;
  final DateTime timestamp;

  HistoryItem({this.id, required this.translation, required this.timestamp});

  factory HistoryItem.fromMap(Map<String, String> map) {
    return HistoryItem(
      id: map['id'],
      translation: map['translation'] ?? '',
      timestamp: DateTime.tryParse(map['timestamp'] ?? '') ?? DateTime.now(),
    );
  }

  factory HistoryItem.fromString(String raw) {
    // Format: "translation|timestamp" or just "translation"
    final parts = raw.split('|');
    return HistoryItem(
      translation: parts[0],
      timestamp: parts.length > 1
          ? DateTime.tryParse(parts[1]) ?? DateTime.now()
          : DateTime.now(),
    );
  }

  String toStorageString() => '$translation|${timestamp.toIso8601String()}';
}

class TranslationProvider extends ChangeNotifier {
  String _currentTranslation = '';
  List<HistoryItem> _history = [];
  bool _isLoadingHistory = false;
  bool _isConnected = false;

  String get currentTranslation => _currentTranslation;
  List<HistoryItem> get history => _history;
  bool get isLoadingHistory => _isLoadingHistory;
  bool get isConnected => _isConnected;

  void updateTranslation(String text) {
    _currentTranslation = text;
    notifyListeners();
  }

  void setConnected(bool val) {
    _isConnected = val;
    notifyListeners();
  }

  void clearCurrentTranslation() {
    _currentTranslation = '';
    notifyListeners();
  }

  Future<void> loadHistory(String userId, {String? token}) async {
    _isLoadingHistory = true;
    notifyListeners();
    try {
      final rawHistory = await ApiService.getHistory(token ?? '');
      _history = rawHistory.map((r) => HistoryItem.fromMap(r)).toList();
    } catch (_) {
      _history = [];
    }
    _isLoadingHistory = false;
    notifyListeners();
  }

  Future<void> saveTranslation(String userId, String translation, {String? token}) async {
    final item = HistoryItem(
      translation: translation,
      timestamp: DateTime.now(),
    );
    _history.insert(0, item);
    notifyListeners();
    await ApiService.postHistory(translation, token ?? '');
  }

  Future<void> deleteHistoryItem(String id, {String? token}) async {
    final index = _history.indexWhere((item) => item.id == id);
    if (index == -1) return;
    final removed = _history.removeAt(index);
    notifyListeners();
    try {
      await ApiService.deleteHistoryItem(id, token ?? '');
    } catch (_) {
      _history.insert(index, removed);
      notifyListeners();
      rethrow;
    }
  }

  Future<void> clearHistory({String? token}) async {
    final previousHistory = List<HistoryItem>.from(_history);
    _history.clear();
    notifyListeners();
    try {
      await ApiService.clearHistory(token ?? '');
    } catch (_) {
      _history = previousHistory;
      notifyListeners();
      rethrow;
    }
  }
}
