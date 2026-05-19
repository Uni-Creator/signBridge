import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import '../../auth/poviders/auth_provider.dart';
import '../../translate/providers/translation_provider.dart';

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  static const primaryColor = Color(0xFF2B2D5D);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadHistory());
  }

  void _loadHistory() {
    final auth = context.read<AuthProvider>();
    final userId = auth.userId ?? 'guest';
    context.read<TranslationProvider>().loadHistory(userId);
  }

  @override
  Widget build(BuildContext context) {
    final translationProvider = context.watch<TranslationProvider>();
    final history = translationProvider.history;

    return Scaffold(
      backgroundColor: const Color(0xFFF5F7FF),
      appBar: AppBar(
        backgroundColor: primaryColor,
        elevation: 0,
        title: const Text(
          'Translation History',
          style: TextStyle(
              color: Colors.white, fontWeight: FontWeight.bold, fontSize: 20),
        ),
        actions: [
          if (history.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.refresh, color: Colors.white),
              onPressed: _loadHistory,
            ),
        ],
      ),
      body: translationProvider.isLoadingHistory
          ? const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  CircularProgressIndicator(color: primaryColor),
                  SizedBox(height: 12),
                  Text('Loading history...', style: TextStyle(color: Colors.black54)),
                ],
              ),
            )
          : history.isEmpty
              ? _emptyState()
              : RefreshIndicator(
                  color: primaryColor,
                  onRefresh: () async => _loadHistory(),
                  child: ListView.separated(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                    itemCount: history.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 10),
                    itemBuilder: (context, index) {
                      final item = history[index];
                      return _HistoryCard(item: item, index: index);
                    },
                  ),
                ),
    );
  }

  Widget _emptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: primaryColor.withOpacity(0.07),
              shape: BoxShape.circle,
            ),
            child: const Icon(Icons.history, size: 56, color: primaryColor),
          ),
          const SizedBox(height: 20),
          const Text(
            'No translations yet',
            style: TextStyle(
                fontSize: 20, fontWeight: FontWeight.bold, color: primaryColor),
          ),
          const SizedBox(height: 8),
          Text(
            'Start translating to see your history here.',
            style: TextStyle(fontSize: 14, color: Colors.grey.shade500),
          ),
        ],
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  final HistoryItem item;
  final int index;

  const _HistoryCard({required this.item, required this.index});

  static const primaryColor = Color(0xFF2B2D5D);

  @override
  Widget build(BuildContext context) {
    final dateStr =
        DateFormat('MMM d, yyyy · h:mm a').format(item.timestamp);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.05),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: primaryColor.withOpacity(0.1),
              borderRadius: BorderRadius.circular(10),
            ),
            alignment: Alignment.center,
            child: Text(
              '${index + 1}',
              style: const TextStyle(
                  color: primaryColor,
                  fontWeight: FontWeight.bold,
                  fontSize: 14),
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.translation,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF1A1A2E),
                  ),
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Icon(Icons.access_time,
                        size: 13, color: Colors.grey),
                    const SizedBox(width: 4),
                    Text(
                      dateStr,
                      style: const TextStyle(
                          color: Colors.grey, fontSize: 12),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const Icon(Icons.sign_language,
              color: primaryColor, size: 18),
        ],
      ),
    );
  }
}
