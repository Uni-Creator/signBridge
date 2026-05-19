import 'package:flutter/material.dart';
import '../../../core/services/api_service.dart';

class HistoryItem {
  final String translation;
  final DateTime timestamp;

  HistoryItem({required this.translation, required this.timestamp});

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

  Future<void> loadHistory(String userId) async {
    _isLoadingHistory = true;
    notifyListeners();
    try {
      final rawHistory = await ApiService.getHistory(userId);
      _history = rawHistory.map((r) => HistoryItem.fromString(r)).toList();
    } catch (_) {
      _history = [];
    }
    _isLoadingHistory = false;
    notifyListeners();
  }

  Future<void> saveTranslation(String userId, String translation) async {
    final item = HistoryItem(
      translation: translation,
      timestamp: DateTime.now(),
    );
    _history.insert(0, item);
    notifyListeners();
    await ApiService.postHistory(userId, item.toStorageString());
  }
}
