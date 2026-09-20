import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../../core/services/api_service.dart';

class HistoryItem {
  final String? id;
  final String translation;
  final DateTime timestamp;

  HistoryItem({this.id, required this.translation, required this.timestamp});

  HistoryItem copyWith({
    String? id,
    String? translation,
    DateTime? timestamp,
  }) {
    return HistoryItem(
      id: id ?? this.id,
      translation: translation ?? this.translation,
      timestamp: timestamp ?? this.timestamp,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'translation': translation,
        'timestamp': timestamp.toIso8601String(),
      };

  factory HistoryItem.fromJson(Map<String, dynamic> json) {
    return HistoryItem(
      id: json['id']?.toString(),
      translation: json['translation']?.toString() ?? '',
      timestamp: DateTime.tryParse(json['timestamp']?.toString() ?? '') ??
          DateTime.now(),
    );
  }

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

  DateTime? _lastFetched;
  String? _currentUserId;
  String? _currentToken;
  Timer? _syncTimer;

  static const Duration cacheTtl = Duration(minutes: 5);
  static const Duration periodicSyncInterval = Duration(minutes: 5);

  String get currentTranslation => _currentTranslation;
  List<HistoryItem> get history => List.unmodifiable(_history);
  bool get isLoadingHistory => _isLoadingHistory;
  bool get isConnected => _isConnected;
  DateTime? get lastFetched => _lastFetched;

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

  String _cacheKey(String userId) => 'history_cache_$userId';

  Future<void> _persistCache(String userId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final list = _history.map((item) => item.toJson()).toList();
      await prefs.setString(_cacheKey(userId), jsonEncode(list));
    } catch (_) {}
  }

  Future<List<HistoryItem>> _loadPersistentCache(String userId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_cacheKey(userId));
      if (raw == null || raw.isEmpty) return [];
      final decoded = jsonDecode(raw);
      if (decoded is List) {
        return decoded
            .whereType<Map>()
            .map((m) => HistoryItem.fromJson(Map<String, dynamic>.from(m)))
            .toList();
      }
    } catch (_) {}
    return [];
  }

  void _startPeriodicSyncIfNeeded(String userId, String? token) {
    _currentToken = token;
    _syncTimer?.cancel();
    _syncTimer = Timer.periodic(periodicSyncInterval, (_) {
      _fetchSilently(userId, token: _currentToken);
    });
  }

  Future<void> _fetchSilently(String userId, {String? token}) async {
    if (token == null || token.isEmpty) return;
    try {
      final rawHistory = await ApiService.getHistory(token);
      _history = rawHistory.map((r) => HistoryItem.fromMap(r)).toList();
      _lastFetched = DateTime.now();
      await _persistCache(userId);
      notifyListeners();
    } catch (_) {
      // Background sync silently fails without disturbing the user
    }
  }

  /// Loads history using in-memory and persistent cache.
  /// If data is already in memory and fresh (within [cacheTtl]), does not query backend.
  /// Pass [force] = true (e.g. on pull-to-refresh) to bypass cache and fetch latest from backend.
  Future<void> loadHistory(String userId, {String? token, bool force = false}) async {
    // If user switched, switch active user cache
    if (_currentUserId != userId) {
      _currentUserId = userId;
      _lastFetched = null;
      _history = await _loadPersistentCache(userId);
      if (_history.isNotEmpty) {
        notifyListeners();
      }
    }

    _startPeriodicSyncIfNeeded(userId, token);

    // Cache hit: data is in memory and within TTL
    final isFresh = _lastFetched != null &&
        DateTime.now().difference(_lastFetched!) < cacheTtl;

    if (!force && _history.isNotEmpty) {
      if (isFresh) {
        // Return immediately from memory cache without backend request or loading spinner
        return;
      }
      // Cache is stale but exists: trigger silent background update so UI remains instant
      unawaited(_fetchSilently(userId, token: token));
      return;
    }

    // Full load: cache is empty or force reload requested
    if (_history.isEmpty) {
      _isLoadingHistory = true;
      notifyListeners();
    }

    try {
      final rawHistory = await ApiService.getHistory(token ?? '');
      _history = rawHistory.map((r) => HistoryItem.fromMap(r)).toList();
      _lastFetched = DateTime.now();
      await _persistCache(userId);
    } catch (e) {
      // If we don't have any cached history, ensure it remains empty
      if (_history.isEmpty) {
        _history = [];
      }
      // If force was true and we had existing cached history, keep existing history intact
      if (force) {
        _isLoadingHistory = false;
        notifyListeners();
        rethrow;
      }
    }

    _isLoadingHistory = false;
    notifyListeners();
  }

  /// Optimistic store: inserts item immediately at index 0 and updates in-memory/cache.
  /// When backend returns the created item, updates the item with the real ID in place.
  /// On error: rolls back the insertion and rethrows.
  Future<HistoryItem> saveTranslation(
    String userId,
    String translation, {
    String? token,
  }) async {
    final tempId = 'temp_${DateTime.now().microsecondsSinceEpoch}';
    final optimisticItem = HistoryItem(
      id: tempId,
      translation: translation,
      timestamp: DateTime.now(),
    );

    _history.insert(0, optimisticItem);
    _currentUserId = userId;
    notifyListeners();
    await _persistCache(userId);

    try {
      final result = await ApiService.postHistory(translation, token ?? '');
      final backendId = result['id'];
      final backendTimestamp = DateTime.tryParse(result['timestamp'] ?? '');

      final confirmedItem = HistoryItem(
        id: (backendId != null && backendId.isNotEmpty) ? backendId : tempId,
        translation: (result['translation'] != null &&
                result['translation']!.isNotEmpty)
            ? result['translation']!
            : translation,
        timestamp: backendTimestamp ?? optimisticItem.timestamp,
      );

      final index = _history.indexWhere((item) => item.id == tempId);
      if (index != -1) {
        _history[index] = confirmedItem;
      } else {
        _history.insert(0, confirmedItem);
      }

      await _persistCache(userId);
      notifyListeners();
      return confirmedItem;
    } catch (e) {
      // Rollback optimistic item
      _history.removeWhere((item) => item.id == tempId);
      await _persistCache(userId);
      notifyListeners();
      rethrow;
    }
  }

  /// Optimistic delete: removes item from frontend immediately.
  /// If backend returns an error, restores the item back at its original index and rethrows.
  Future<void> deleteHistoryItem(String id, {String? token}) async {
    final index = _history.indexWhere((item) => item.id == id);
    if (index == -1) return;

    final removedItem = _history.removeAt(index);
    notifyListeners();
    if (_currentUserId != null) {
      await _persistCache(_currentUserId!);
    }

    try {
      await ApiService.deleteHistoryItem(id, token ?? '');
    } catch (e) {
      // Rollback: restore item to original index
      if (index <= _history.length) {
        _history.insert(index, removedItem);
      } else {
        _history.add(removedItem);
      }
      if (_currentUserId != null) {
        await _persistCache(_currentUserId!);
      }
      notifyListeners();
      rethrow;
    }
  }

  /// Optimistic clear: clears list immediately.
  /// If backend returns an error, restores all previous items and rethrows.
  Future<void> clearHistory({String? token}) async {
    if (_history.isEmpty) return;
    final previousHistory = List<HistoryItem>.from(_history);
    _history.clear();
    notifyListeners();
    if (_currentUserId != null) {
      await _persistCache(_currentUserId!);
    }

    try {
      await ApiService.clearHistory(token ?? '');
    } catch (e) {
      _history = previousHistory;
      if (_currentUserId != null) {
        await _persistCache(_currentUserId!);
      }
      notifyListeners();
      rethrow;
    }
  }

  @override
  void dispose() {
    _syncTimer?.cancel();
    super.dispose();
  }
}
