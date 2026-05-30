import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../auth/poviders/auth_provider.dart';
import '../../translate/screens/translate_screen.dart';
import '../../history/screens/history_screen.dart';
import '../../auth/screens/login_screen.dart';

class SignBridgeHome extends StatefulWidget {
  const SignBridgeHome({super.key});

  @override
  State<SignBridgeHome> createState() => _SignBridgeHomeState();
}

class _SignBridgeHomeState extends State<SignBridgeHome>
    with TickerProviderStateMixin {
  int _selectedIndex = 0;
  late AnimationController _fadeCtrl;
  late Animation<double> _fadeAnim;

  static const primaryColor = Color(0xFF2B2D5D);
  static const pageBg = Color(0xFFF5F7FF);

  final List<Widget> _screens = const [
    _HomeTab(),
    TranslateScreen(),
    HistoryScreen(),
  ];

  @override
  void initState() {
    super.initState();
    _fadeCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 300));
    _fadeAnim = CurvedAnimation(parent: _fadeCtrl, curve: Curves.easeIn);
    _fadeCtrl.forward();
  }

  @override
  void dispose() {
    _fadeCtrl.dispose();
    super.dispose();
  }

  void _onNavTap(int index) {
    if (_selectedIndex == index) return;
    _fadeCtrl.reset();
    setState(() => _selectedIndex = index);
    _fadeCtrl.forward();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: pageBg,
      body: FadeTransition(
        opacity: _fadeAnim,
        child: _screens[_selectedIndex],
      ),
      bottomNavigationBar: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          boxShadow: [
            BoxShadow(
                color: Colors.black.withOpacity(0.08),
                blurRadius: 20,
                offset: const Offset(0, -4))
          ],
        ),
        child: SafeArea(
          child: Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _navItem(0, Icons.home_outlined, Icons.home, 'Home'),
                _navItem(
                    1, Icons.translate_outlined, Icons.translate, 'Translate'),
                _navItem(2, Icons.history_outlined, Icons.history, 'History'),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _navItem(int index, IconData icon, IconData activeIcon, String label) {
    final isSelected = _selectedIndex == index;
    return GestureDetector(
      onTap: () => _onNavTap(index),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: EdgeInsets.symmetric(
            horizontal: isSelected ? 20 : 14, vertical: 10),
        decoration: BoxDecoration(
          color: isSelected
              ? primaryColor.withOpacity(0.12)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isSelected ? activeIcon : icon,
              color: isSelected ? primaryColor : Colors.grey,
              size: 22,
            ),
            if (isSelected) ...[
              const SizedBox(width: 6),
              Text(
                label,
                style: const TextStyle(
                  color: primaryColor,
                  fontWeight: FontWeight.w600,
                  fontSize: 13,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ──────────────── Home Tab ────────────────
class _HomeTab extends StatelessWidget {
  const _HomeTab();

  static const primaryColor = Color(0xFF2B2D5D);
  static const pageBg = Color(0xFFF5F7FF);
  static const accentColor = Color(0xFF4B6CF7);

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();

    return Scaffold(
      backgroundColor: pageBg,
      appBar: AppBar(
        backgroundColor: primaryColor,
        elevation: 0,
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(6),
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.15),
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Icon(Icons.sign_language,
                  color: Colors.white, size: 20),
            ),
            const SizedBox(width: 10),
            const Text(
              'SignBridge',
              style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 20),
            ),
          ],
        ),
        actions: [
          if (auth.isLoggedIn)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: PopupMenuButton<String>(
                icon: CircleAvatar(
                  radius: 17,
                  backgroundColor: Colors.white.withOpacity(0.2),
                  child: Text(
                    (auth.email ?? 'G')[0].toUpperCase(),
                    style: const TextStyle(
                        color: Colors.white, fontWeight: FontWeight.bold),
                  ),
                ),
                onSelected: (v) async {
                  if (v == 'logout') {
                    await context.read<AuthProvider>().logout();
                    if (context.mounted) {
                      Navigator.of(context).pushAndRemoveUntil(
                        MaterialPageRoute(
                            builder: (_) => const LoginScreen()),
                        (r) => false,
                      );
                    }
                  }
                },
                itemBuilder: (_) => [
                  PopupMenuItem(
                    value: 'email',
                    enabled: false,
                    child: Text(auth.email ?? '',
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                  ),
                  const PopupMenuDivider(),
                  const PopupMenuItem(
                    value: 'logout',
                    child: Row(
                      children: [
                        Icon(Icons.logout, color: Colors.red, size: 18),
                        SizedBox(width: 8),
                        Text('Logout',
                            style: TextStyle(color: Colors.red)),
                      ],
                    ),
                  ),
                ],
              ),
            )
          else
            TextButton(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const LoginScreen()),
              ),
              child: const Text('Login',
                  style: TextStyle(color: Colors.white)),
            ),
        ],
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final isMobile = constraints.maxWidth < 650;
          return SingleChildScrollView(
            child: Container(
              width: double.infinity,
              alignment: Alignment.topCenter,
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 900),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Hero banner
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(28),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(
                          colors: [
                            Color(0xFF2B2D5D),
                            Color(0xFF4B6CF7),
                          ],
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                        ),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(Icons.sign_language,
                              color: Colors.white, size: 36),
                          const SizedBox(height: 16),
                          const Text(
                            'Real-time Sign\nLanguage Translation',
                            style: TextStyle(
                              fontSize: 26,
                              fontWeight: FontWeight.bold,
                              color: Colors.white,
                              height: 1.3,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Point your camera at any sign language gesture and get instant translations',
                            style: TextStyle(
                              fontSize: 14,
                              color: Colors.white.withOpacity(0.75),
                            ),
                          ),
                          const SizedBox(height: 20),
                          ElevatedButton.icon(
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.white,
                              foregroundColor: primaryColor,
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 24, vertical: 12),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(10),
                              ),
                            ),
                            onPressed: () {
                              final home = context.findAncestorStateOfType<
                                  _SignBridgeHomeState>();
                              home?._onNavTap(1);
                            },
                            icon: const Icon(Icons.camera_alt, size: 18),
                            label: const Text(
                              'Start Translating',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                          ),
                        ],
                      ),
                    ),

                    const SizedBox(height: 28),

                    // Feature cards
                    Wrap(
                      spacing: 16,
                      runSpacing: 16,
                      alignment: WrapAlignment.center,
                      children: [
                        _featureCard(
                          icon: Icons.camera_alt_outlined,
                          title: 'Capture Signs',
                          subtitle:
                              'Live camera feed with real-time frame analysis',
                          isMobile: isMobile,
                          color: const Color(0xFF4B6CF7),
                        ),
                        _featureCard(
                          icon: Icons.text_fields,
                          title: 'Text Translation',
                          subtitle:
                              'Convert signs to readable text instantly.',
                          isMobile: isMobile,
                          color: const Color(0xFF2BB673),
                        ),
                        _featureCard(
                          icon: Icons.volume_up_outlined,
                          title: 'Voice Output',
                          subtitle:
                              'Hear spoken translations for better communication.',
                          isMobile: isMobile,
                          color: const Color(0xFFE67E22),
                        ),
                      ],
                    ),

                    const SizedBox(height: 28),

                    _howItWorksCard(),

                    const SizedBox(height: 28),

                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Expanded(
                          child: ElevatedButton.icon(
                            style: ElevatedButton.styleFrom(
                              backgroundColor: primaryColor,
                              foregroundColor: Colors.white,
                              padding:
                                  const EdgeInsets.symmetric(vertical: 14),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(12),
                              ),
                            ),
                            onPressed: () {
                              final home = context.findAncestorStateOfType<
                                  _SignBridgeHomeState>();
                              home?._onNavTap(1);
                            },
                            icon: const Icon(Icons.camera_alt, size: 18),
                            label: const Text('Start Translating'),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: OutlinedButton.icon(
                            style: OutlinedButton.styleFrom(
                              padding:
                                  const EdgeInsets.symmetric(vertical: 14),
                              side: const BorderSide(color: primaryColor),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(12),
                              ),
                            ),
                            onPressed: () {
                              final home = context.findAncestorStateOfType<
                                  _SignBridgeHomeState>();
                              home?._onNavTap(2);
                            },
                            icon: const Icon(Icons.history,
                                color: primaryColor, size: 18),
                            label: const Text(
                              'View History',
                              style: TextStyle(color: primaryColor),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _featureCard({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool isMobile,
    required Color color,
  }) {
    return Container(
      width: isMobile ? double.infinity : 220,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(.06),
            blurRadius: 12,
            offset: const Offset(0, 4),
          )
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: color.withOpacity(0.12),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(icon, color: color, size: 24),
          ),
          const SizedBox(height: 14),
          Text(title,
              style: const TextStyle(
                fontWeight: FontWeight.bold,
                color: primaryColor,
                fontSize: 15,
              )),
          const SizedBox(height: 6),
          Text(subtitle,
              style: const TextStyle(color: Colors.black54, fontSize: 13)),
        ],
      ),
    );
  }

  Widget _howItWorksCard() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(.06),
            blurRadius: 12,
            offset: const Offset(0, 4),
          )
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.info_outline, color: primaryColor, size: 20),
              SizedBox(width: 8),
              Text(
                'How It Works',
                style: TextStyle(
                    fontWeight: FontWeight.bold,
                    color: primaryColor,
                    fontSize: 18),
              ),
            ],
          ),
          const SizedBox(height: 16),
          ..._steps.asMap().entries.map((e) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 26,
                      height: 26,
                      decoration: const BoxDecoration(
                        color: accentColor,
                        shape: BoxShape.circle,
                      ),
                      alignment: Alignment.center,
                      child: Text(
                        '${e.key + 1}',
                        style: const TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.bold,
                            fontSize: 12),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(e.value,
                          style: const TextStyle(fontSize: 14, height: 1.5)),
                    ),
                  ],
                ),
              )),
        ],
      ),
    );
  }

  static const _steps = [
    'Go to the Translate tab and allow camera permission.',
    'Point your camera at sign language gestures.',
    'The AI detects signs in real-time and shows the translation.',
    'Tap the speaker icon to hear the text read aloud.',
    'Save or view your translations in the History tab.',
  ];
}
