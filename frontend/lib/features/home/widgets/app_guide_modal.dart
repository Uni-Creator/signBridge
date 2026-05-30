import 'package:flutter/material.dart';

void showAppGuide(BuildContext context) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (context) => const AppGuideModal(),
  );
}

class AppGuideModal extends StatefulWidget {
  const AppGuideModal({super.key});

  @override
  State<AppGuideModal> createState() => _AppGuideModalState();
}

class _AppGuideModalState extends State<AppGuideModal> {
  final PageController _pageCtrl = PageController();
  int _currentIndex = 0;

  static const primaryColor = Color(0xFF1E2158);
  static const accentColor = Color(0xFF4B6CF7);

  final List<Map<String, dynamic>> _steps = [
    {
      'icon': Icons.camera_alt_rounded,
      'color': const Color(0xFF0F6E56),
      'bg': const Color(0xFFE6FAF3),
      'title': 'Sign Language Translation (SLT)',
      'desc':
          'Point your camera at someone signing. The AI will detect the hand gestures in real-time and translate them into text on your screen.',
    },
    {
      'icon': Icons.keyboard_rounded,
      'color': const Color(0xFF4B6CF7),
      'bg': const Color(0xFFEEF2FF),
      'title': 'Sign Language Production (SLP)',
      'desc':
          'Type English text into the app. The system will convert your words into a 3D avatar or video demonstrating the correct sign language poses.',
    },
    {
      'icon': Icons.history_rounded,
      'color': const Color(0xFFBA7517),
      'bg': const Color(0xFFFEF3E2),
      'title': 'History & Saved',
      'desc':
          'Review past translations in the History tab. Save important phrases to access them quickly later.',
    },
  ];

  @override
  void dispose() {
    _pageCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: MediaQuery.of(context).size.height * 0.55,
      decoration: const BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.only(
          topLeft: Radius.circular(28),
          topRight: Radius.circular(28),
        ),
      ),
      child: Column(
        children: [
          // Drag handle
          Center(
            child: Container(
              margin: const EdgeInsets.only(top: 12, bottom: 20),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey.shade300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          
          const Text(
            'How to use SignBridge',
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.bold,
              color: primaryColor,
            ),
          ),
          const SizedBox(height: 24),

          // PageView for steps
          Expanded(
            child: PageView.builder(
              controller: _pageCtrl,
              onPageChanged: (idx) => setState(() => _currentIndex = idx),
              itemCount: _steps.length,
              itemBuilder: (context, index) {
                final step = _steps[index];
                return Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 32),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        width: 80,
                        height: 80,
                        decoration: BoxDecoration(
                          color: step['bg'],
                          shape: BoxShape.circle,
                        ),
                        child: Icon(step['icon'], size: 40, color: step['color']),
                      ),
                      const SizedBox(height: 32),
                      Text(
                        step['title'],
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w700,
                          color: primaryColor,
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        step['desc'],
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 14,
                          height: 1.6,
                          color: Colors.black54,
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),

          // Indicators and Next button
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 0, 24, 32),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                // Dots
                Row(
                  children: List.generate(
                    _steps.length,
                    (index) => AnimatedContainer(
                      duration: const Duration(milliseconds: 300),
                      margin: const EdgeInsets.only(right: 6),
                      height: 8,
                      width: _currentIndex == index ? 24 : 8,
                      decoration: BoxDecoration(
                        color: _currentIndex == index
                            ? accentColor
                            : Colors.grey.shade300,
                        borderRadius: BorderRadius.circular(4),
                      ),
                    ),
                  ),
                ),
                
                // Next / Got it Button
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: primaryColor,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  onPressed: () {
                    if (_currentIndex < _steps.length - 1) {
                      _pageCtrl.nextPage(
                        duration: const Duration(milliseconds: 300),
                        curve: Curves.easeIn,
                      );
                    } else {
                      Navigator.pop(context);
                    }
                  },
                  child: Text(
                    _currentIndex == _steps.length - 1 ? 'Got it!' : 'Next',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
