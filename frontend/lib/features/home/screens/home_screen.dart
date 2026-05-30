import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../auth/poviders/auth_provider.dart';
import '../../translate/screens/translate_screen.dart';
import '../../history/screens/history_screen.dart';
import '../../auth/screens/login_screen.dart';
import '../../saved/screens/saved_screen.dart';
import '../../settings/screens/settings_screen.dart';
import '../widgets/app_guide_modal.dart';
import 'package:shared_preferences/shared_preferences.dart';


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

  static const primaryColor = Color(0xFF1E2158);
  static const pageBg = Color(0xFFF5F7FF);

  final List<Widget> _screens = const [
    _HomeTab(),
    TranslateScreen(),   
    _SLPPlaceholder(),  
    HistoryScreen(),
  ];

  @override
  void initState() {
    super.initState();
    _fadeCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 280));
    _fadeAnim = CurvedAnimation(parent: _fadeCtrl, curve: Curves.easeIn);
    _fadeCtrl.forward();
    
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _checkFirstTime();
    });
  }

  Future<void> _checkFirstTime() async {
    final prefs = await SharedPreferences.getInstance();
    final hasSeenGuide = prefs.getBool('has_seen_guide') ?? false;
    if (!hasSeenGuide) {
      if (mounted) {
        showAppGuide(context);
      }
      await prefs.setBool('has_seen_guide', true);
    }
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
      bottomNavigationBar: _BottomNav(
        selectedIndex: _selectedIndex,
        onTap: _onNavTap,
      ),
    );
  }
}


class _BottomNav extends StatelessWidget {
  const _BottomNav({required this.selectedIndex, required this.onTap});

  final int selectedIndex;
  final ValueChanged<int> onTap;

  static const primaryColor = Color(0xFF1E2158);
  static const accentColor = Color(0xFF4B6CF7);

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.08),
            blurRadius: 20,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _navItem(0, Icons.home_outlined, Icons.home, 'Home'),
              _navItem(1, Icons.camera_alt_outlined, Icons.camera_alt, 'SLT'),
              _navItem(2, Icons.keyboard_outlined, Icons.keyboard, 'SLP'),
              _navItem(3, Icons.history_outlined, Icons.history, 'History'),
            ],
          ),
        ),
      ),
    );
  }

  Widget _navItem(
      int index, IconData icon, IconData activeIcon, String label) {
    final isSelected = selectedIndex == index;
    return GestureDetector(
      onTap: () => onTap(index),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: EdgeInsets.symmetric(
            horizontal: isSelected ? 18 : 12, vertical: 10),
        decoration: BoxDecoration(
          color: isSelected
              ? accentColor.withOpacity(0.12)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isSelected ? activeIcon : icon,
              color: isSelected ? accentColor : Colors.grey,
              size: 22,
            ),
            if (isSelected) ...[
              const SizedBox(width: 6),
              Text(
                label,
                style: const TextStyle(
                  color: accentColor,
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


class _HomeTab extends StatelessWidget {
  const _HomeTab();

  static const primaryColor = Color(0xFF1E2158);
  static const accentColor = Color(0xFF4B6CF7);
  static const pageBg = Color(0xFFF5F7FF);

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    final String initial = (auth.email ?? 'G')[0].toUpperCase();
    final String greeting = _greeting();

    return Scaffold(
      backgroundColor: pageBg,
      body: Column(
        children: [
          _AppHeader(
            initial: initial,
            greeting: greeting,
            email: auth.email,
            isLoggedIn: auth.isLoggedIn,
            onLogout: () async {
              await context.read<AuthProvider>().logout();
              if (context.mounted) {
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                  (r) => false,
                );
              }
            },
            onLogin: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const LoginScreen()),
            ),
          ),

          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(16, 20, 16, 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Hero banner
                  _HeroBanner(
                    onGetStarted: () {
                      final home = context
                          .findAncestorStateOfType<_SignBridgeHomeState>();
                      home?._onNavTap(1);
                    },
                  ),

                  const SizedBox(height: 24),

                  // Mode selector: SLT + SLP
                  _SectionLabel(label: 'Choose mode'),
                  const SizedBox(height: 12),
                  _ModeGrid(
                    onSLT: () {
                      final home = context
                          .findAncestorStateOfType<_SignBridgeHomeState>();
                      home?._onNavTap(1);
                    },
                    onSLP: () {
                      final home = context
                          .findAncestorStateOfType<_SignBridgeHomeState>();
                      home?._onNavTap(2);
                    },
                  ),

                  const SizedBox(height: 24),

                  // Quick actions
                  _SectionLabel(label: 'Quick actions'),
                  const SizedBox(height: 12),
                  _QuickActions(
                    onHistory: () {
                      final home = context
                          .findAncestorStateOfType<_SignBridgeHomeState>();
                      home?._onNavTap(3);
                    },
                    onSaved: () {
                      Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => const SavedScreen()),
                      );
                    },
                    onSettings: () {
                      Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => const SettingsScreen()),
                      );
                    },
                  ),

                  const SizedBox(height: 24),

                  // Activity stats
                  _SectionLabel(label: 'Your activity'),
                  const SizedBox(height: 12),
                  const _StatsRow(),

                  const SizedBox(height: 24),

                  // Support section
                  _SectionLabel(label: 'Support'),
                  const SizedBox(height: 12),
                  const _SupportCard(),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _greeting() {
    final hour = DateTime.now().hour;
    if (hour < 12) return 'Good morning 👋';
    if (hour < 17) return 'Good afternoon 👋';
    return 'Good evening 👋';
  }
}


class _AppHeader extends StatelessWidget {
  const _AppHeader({
    required this.initial,
    required this.greeting,
    required this.email,
    required this.isLoggedIn,
    required this.onLogout,
    required this.onLogin,
  });

  final String initial;
  final String greeting;
  final String? email;
  final bool isLoggedIn;
  final VoidCallback onLogout;
  final VoidCallback onLogin;

  static const primaryColor = Color(0xFF1E2158);

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: primaryColor,
        borderRadius: BorderRadius.only(
          bottomLeft: Radius.circular(24),
          bottomRight: Radius.circular(24),
        ),
      ),
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Brand row + avatar
              Row(
                children: [
                  // Brand
                  Container(
                    padding: const EdgeInsets.all(7),
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(10),
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
                      fontSize: 20,
                    ),
                  ),
                  const Spacer(),

                  // Avatar / login
                  if (isLoggedIn)
                    PopupMenuButton<String>(
                      onSelected: (v) {
                        if (v == 'logout') onLogout();
                      },
                      itemBuilder: (_) => [
                        PopupMenuItem(
                          value: 'email',
                          enabled: false,
                          child: Text(email ?? '',
                              style: const TextStyle(
                                  fontWeight: FontWeight.w600)),
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
                      child: CircleAvatar(
                        radius: 18,
                        backgroundColor: Colors.white.withOpacity(0.2),
                        child: Text(
                          initial,
                          style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.bold,
                              fontSize: 15),
                        ),
                      ),
                    )
                  else
                    TextButton(
                      onPressed: onLogin,
                      child: const Text('Login',
                          style: TextStyle(color: Colors.white)),
                    ),
                ],
              ),

              const SizedBox(height: 14),

              // Greeting
              Text(
                greeting,
                style: TextStyle(
                    color: Colors.white.withOpacity(0.65), fontSize: 13),
              ),
              const SizedBox(height: 2),
              const Text(
                'Welcome back!',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 22,
                    fontWeight: FontWeight.bold),
              ),
            ],
          ),
        ),
      ),
    );
  }
}


class _HeroBanner extends StatelessWidget {
  const _HeroBanner({required this.onGetStarted});
  final VoidCallback onGetStarted;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF2B2D5D), Color(0xFF4B6CF7)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Stack(
        children: [
          // Decorative circles
          Positioned(
            top: -20, right: -20,
            child: Container(
              width: 100, height: 100,
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.06),
                shape: BoxShape.circle,
              ),
            ),
          ),
          Positioned(
            bottom: -10, right: 50,
            child: Container(
              width: 60, height: 60,
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.04),
                shape: BoxShape.circle,
              ),
            ),
          ),

          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.18),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: const Text(
                  'AI-Powered',
                  style: TextStyle(
                      color: Colors.white,
                      fontSize: 11,
                      letterSpacing: 0.5),
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Sign Language\nBridge System',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  height: 1.3,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Translate signs to text, or convert text back to sign language poses.',
                style: TextStyle(
                    color: Colors.white.withOpacity(0.7),
                    fontSize: 13,
                    height: 1.5),
              ),
              const SizedBox(height: 18),
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.white,
                  foregroundColor: const Color(0xFF2B2D5D),
                  padding: const EdgeInsets.symmetric(
                      horizontal: 20, vertical: 11),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12)),
                  elevation: 0,
                ),
                onPressed: onGetStarted,
                icon: const Icon(Icons.play_arrow_rounded, size: 18),
                label: const Text(
                  'Get Started',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}


class _ModeGrid extends StatelessWidget {
  const _ModeGrid({required this.onSLT, required this.onSLP});
  final VoidCallback onSLT;
  final VoidCallback onSLP;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _ModeCard(
            icon: Icons.camera_alt_rounded,
            iconBg: const Color(0xFFE6FAF3),
            iconColor: const Color(0xFF0F6E56),
            title: 'SLT',
            subtitle: 'Sign → Text',
            description:
                'Point camera at signs and get instant text translation.',
            chipLabel: 'Translate',
            chipBg: const Color(0xFFE6FAF3),
            chipColor: const Color(0xFF0F6E56),
            onTap: onSLT,
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _ModeCard(
            icon: Icons.keyboard_rounded,
            iconBg: const Color(0xFFEEF2FF),
            iconColor: const Color(0xFF4B6CF7),
            title: 'SLP',
            subtitle: 'Text → Sign',
            description:
                'Type text and watch it converted to sign language poses.',
            chipLabel: 'Produce',
            chipBg: const Color(0xFFEEF2FF),
            chipColor: const Color(0xFF4B6CF7),
            onTap: onSLP,
          ),
        ),
      ],
    );
  }
}

class _ModeCard extends StatelessWidget {
  const _ModeCard({
    required this.icon,
    required this.iconBg,
    required this.iconColor,
    required this.title,
    required this.subtitle,
    required this.description,
    required this.chipLabel,
    required this.chipBg,
    required this.chipColor,
    required this.onTap,
  });

  final IconData icon;
  final Color iconBg;
  final Color iconColor;
  final String title;
  final String subtitle;
  final String description;
  final String chipLabel;
  final Color chipBg;
  final Color chipColor;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.05),
              blurRadius: 10,
              offset: const Offset(0, 3),
            ),
          ],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44, height: 44,
              decoration: BoxDecoration(
                color: iconBg,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(icon, color: iconColor, size: 22),
            ),
            const SizedBox(height: 12),
            Text(title,
                style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF1E2158))),
            const SizedBox(height: 2),
            Text(subtitle,
                style: const TextStyle(
                    fontSize: 11, color: Colors.grey)),
            const SizedBox(height: 8),
            Text(description,
                style: const TextStyle(
                    fontSize: 12,
                    color: Colors.black54,
                    height: 1.5)),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: chipBg,
                borderRadius: BorderRadius.circular(20),
              ),
              child: Text(chipLabel,
                  style: TextStyle(
                      color: chipColor,
                      fontSize: 11,
                      fontWeight: FontWeight.w600)),
            ),
          ],
        ),
      ),
    );
  }
}


class _QuickActions extends StatelessWidget {
  const _QuickActions({
    required this.onHistory,
    required this.onSaved,
    required this.onSettings,
  });

  final VoidCallback onHistory;
  final VoidCallback onSaved;
  final VoidCallback onSettings;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _QAButton(
            icon: Icons.history_rounded,
            iconBg: const Color(0xFFFEF3E2),
            iconColor: const Color(0xFFBA7517),
            label: 'History',
            onTap: onHistory,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _QAButton(
            icon: Icons.bookmark_outline_rounded,
            iconBg: const Color(0xFFFBEAF0),
            iconColor: const Color(0xFF993556),
            label: 'Saved',
            onTap: onSaved,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _QAButton(
            icon: Icons.settings_outlined,
            iconBg: const Color(0xFFF1EFE8),
            iconColor: const Color(0xFF5F5E5A),
            label: 'Settings',
            onTap: onSettings,
          ),
        ),
      ],
    );
  }
}

class _QAButton extends StatelessWidget {
  const _QAButton({
    required this.icon,
    required this.iconBg,
    required this.iconColor,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final Color iconBg;
  final Color iconColor;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 14),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.05),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          children: [
            Container(
              width: 36, height: 36,
              decoration: BoxDecoration(
                color: iconBg,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(icon, color: iconColor, size: 18),
            ),
            const SizedBox(height: 8),
            Text(label,
                style: const TextStyle(
                    fontSize: 12, color: Colors.black54)),
          ],
        ),
      ),
    );
  }
}


class _StatsRow extends StatelessWidget {
  const _StatsRow();

  @override
  Widget build(BuildContext context) {
    return Row(
      children: const [
        Expanded(
            child: _StatCard(value: '142', label: 'Translations\ndone')),
        SizedBox(width: 10),
        Expanded(
            child: _StatCard(value: '38', label: 'Signs\nproduced')),
        SizedBox(width: 10),
        Expanded(
            child: _StatCard(value: '7', label: 'Days\nactive')),
      ],
    );
  }
}

class _StatCard extends StatelessWidget {
  const _StatCard({required this.value, required this.label});
  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 12),
      decoration: BoxDecoration(
        color: const Color(0xFFEEF2FF),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value,
              style: const TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF1E2158))),
          const SizedBox(height: 4),
          Text(label,
              style: const TextStyle(
                  fontSize: 11,
                  color: Colors.black54,
                  height: 1.4)),
        ],
      ),
    );
  }
}


class _SupportCard extends StatelessWidget {
  const _SupportCard();

  List<_SupportItem> _buildItems(BuildContext context) {
    return [
      _SupportItem(
        icon: Icons.help_outline_rounded,
        iconBg: const Color(0xFFEEF2FF),
        iconColor: const Color(0xFF4B6CF7),
        title: 'Help & Guide',
        subtitle: 'How to use SLT and SLP modes',
        onTap: () => showAppGuide(context),
      ),
      _SupportItem(
        icon: Icons.mail_outline_rounded,
        iconBg: const Color(0xFFE6FAF3),
        iconColor: const Color(0xFF0F6E56),
        title: 'Contact Us',
        subtitle: 'Reach our support team',
        onTap: () {},
      ),
      _SupportItem(
        icon: Icons.shield_outlined,
        iconBg: const Color(0xFFFEF3E2),
        iconColor: const Color(0xFFBA7517),
        title: 'Privacy & Terms',
        subtitle: 'Legal info and data usage',
        onTap: () {},
      ),
      _SupportItem(
        icon: Icons.info_outline_rounded,
        iconBg: const Color(0xFFF1EFE8),
        iconColor: const Color(0xFF5F5E5A),
        title: 'About SignBridge',
        subtitle: 'Version 1.0.0 · Built with ❤️',
        onTap: () {},
      ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final items = _buildItems(context);
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.05),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Column(
        children: items.asMap().entries.map((entry) {
          final isLast = entry.key == items.length - 1;
          final item = entry.value;
          return Column(
            children: [
              InkWell(
                onTap: item.onTap,
                borderRadius: BorderRadius.vertical(
                  top: entry.key == 0
                      ? const Radius.circular(16)
                      : Radius.zero,
                  bottom: isLast
                      ? const Radius.circular(16)
                      : Radius.zero,
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 14),
                  child: Row(
                    children: [
                      Container(
                        width: 36, height: 36,
                        decoration: BoxDecoration(
                          color: item.iconBg,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Icon(item.icon,
                            color: item.iconColor, size: 18),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(item.title,
                                style: const TextStyle(
                                    fontSize: 14,
                                    fontWeight: FontWeight.w600,
                                    color: Color(0xFF1E2158))),
                            const SizedBox(height: 2),
                            Text(item.subtitle,
                                style: const TextStyle(
                                    fontSize: 12,
                                    color: Colors.black45)),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right_rounded,
                          color: Colors.black26, size: 20),
                    ],
                  ),
                ),
              ),
              if (!isLast)
                const Divider(height: 1, indent: 66, endIndent: 16),
            ],
          );
        }).toList(),
      ),
    );
  }
}

class _SupportItem {
  const _SupportItem({
    required this.icon,
    required this.iconBg,
    required this.iconColor,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  final IconData icon;
  final Color iconBg;
  final Color iconColor;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
}


class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Text(
      label.toUpperCase(),
      style: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w600,
        color: Colors.black45,
        letterSpacing: 0.7,
      ),
    );
  }
}


class _SLPPlaceholder extends StatelessWidget {
  const _SLPPlaceholder();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.keyboard_rounded, size: 48, color: Color(0xFF4B6CF7)),
            SizedBox(height: 16),
            Text('SLP Screen',
                style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF1E2158))),
            SizedBox(height: 8),
            Text('Text → Sign Language Pose',
                style: TextStyle(color: Colors.black45)),
          ],
        ),
      ),
    );
  }
}
