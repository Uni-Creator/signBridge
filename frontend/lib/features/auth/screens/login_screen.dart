import 'package:SignSight/features/auth/screens/forgot_password_screen.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../poviders/auth_provider.dart';
import '../../home/screens/home_screen.dart';
import 'register_screen.dart';
import 'dart:math' as math;

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen>
    with TickerProviderStateMixin {
  final _formKey = GlobalKey<FormState>();
  final _emailCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  bool _obscurePass = true;

  late AnimationController _bgController;
  late AnimationController _cardController;
  late Animation<double> _cardAnimation;

  // Original color palette preserved exactly
  static const Color _primary   = Color(0xFF2B2D5D);
  static const Color _accent    = Color(0xFF4B6CF7);
  static const Color _bg        = Color(0xFFF5F7FF);
  static const Color _surface   = Colors.white;
  static const Color _border    = Color(0xFFE2E6FF);
  static const Color _textMuted = Color(0xFF8A8FAD);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final auth = context.read<AuthProvider>();
      await auth.initialization;
      if (auth.isLoggedIn && mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const SignSightHome()),
        );
      }
    });

    _bgController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 10),
    )..repeat(reverse: true);

    _cardController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 650),
    );
    _cardAnimation = CurvedAnimation(
      parent: _cardController,
      curve: Curves.easeOutCubic,
    );
    _cardController.forward();
  }

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passCtrl.dispose();
    _bgController.dispose();
    _cardController.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;
    final auth = context.read<AuthProvider>();
    final success = await auth.login(_emailCtrl.text.trim(), _passCtrl.text);
    if (!mounted) return;
    if (success) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const SignSightHome()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();

    return Scaffold(
      backgroundColor: _bg,
      body: Stack(
        children: [
          // Subtle animated orbs in the background
          AnimatedBuilder(
            animation: _bgController,
            builder: (_, __) => CustomPaint(
              size: MediaQuery.of(context).size,
              painter: _BgPainter(_bgController.value),
            ),
          ),

          SafeArea(
            child: FadeTransition(
              opacity: _cardAnimation,
              child: SlideTransition(
                position: Tween<Offset>(
                  begin: const Offset(0, 0.05),
                  end: Offset.zero,
                ).animate(_cardAnimation),
                child: SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 24, vertical: 32),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Logo
                      Row(
                        children: [
                          Container(
                            width: 46,
                            height: 46,
                            decoration: BoxDecoration(
                              color: _primary,
                              borderRadius: BorderRadius.circular(13),
                              boxShadow: [
                                BoxShadow(
                                  color: _primary.withOpacity(0.25),
                                  blurRadius: 14,
                                  offset: const Offset(0, 4),
                                ),
                              ],
                            ),
                            child: const Icon(Icons.sign_language,
                                color: Colors.white, size: 24),
                          ),
                          const SizedBox(width: 12),
                          const Text(
                            'SignSight',
                            style: TextStyle(
                              fontSize: 22,
                              fontWeight: FontWeight.w700,
                              color: _primary,
                              letterSpacing: 0.3,
                            ),
                          ),
                        ],
                      ),

                      const SizedBox(height: 44),

                      // Headline
                      const Text(
                        'Welcome back',
                        style: TextStyle(
                          fontSize: 40,
                          fontWeight: FontWeight.w800,
                          color: _primary,
                          height: 1.1,
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'Sign in to continue translation',
                        style: TextStyle(
                            fontSize: 15, color: Colors.grey.shade500),
                      ),

                      const SizedBox(height: 36),

                      // Form card
                      Container(
                        padding: const EdgeInsets.all(24),
                        decoration: BoxDecoration(
                          color: _surface,
                          borderRadius: BorderRadius.circular(24),
                          border: Border.all(color: _border),
                          boxShadow: [
                            BoxShadow(
                              color: _primary.withOpacity(0.06),
                              blurRadius: 32,
                              offset: const Offset(0, 8),
                            ),
                          ],
                        ),
                        child: Form(
                          key: _formKey,
                          child: Column(
                            children: [
                              _buildField(
                                controller: _emailCtrl,
                                label: 'Email',
                                hint: 'you@example.com',
                                icon: Icons.alternate_email_rounded,
                                keyboardType: TextInputType.emailAddress,
                                validator: (v) {
                                  if (v == null || v.isEmpty)
                                    return 'Enter your email';
                                  if (!v.contains('@'))
                                    return 'Invalid email';
                                  return null;
                                },
                              ),
                              const SizedBox(height: 16),
                              _buildField(
                                controller: _passCtrl,
                                label: 'Password',
                                hint: '••••••••',
                                icon: Icons.lock_outline_rounded,
                                obscureText: _obscurePass,
                                suffix: GestureDetector(
                                  onTap: () => setState(
                                      () => _obscurePass = !_obscurePass),
                                  child: Icon(
                                    _obscurePass
                                        ? Icons.visibility_off_rounded
                                        : Icons.visibility_rounded,
                                    color: _textMuted,
                                    size: 20,
                                  ),
                                ),
                                validator: (v) {
                                  if (v == null || v.isEmpty)
                                    return 'Enter your password';
                                  if (v.length < 6)
                                    return 'At least 6 characters';
                                  return null;
                                },
                              ),

                              // Error
                              if (auth.error != null) ...[
                                const SizedBox(height: 14),
                                Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.all(12),
                                  decoration: BoxDecoration(
                                    color: Colors.red.shade50,
                                    borderRadius: BorderRadius.circular(12),
                                    border: Border.all(
                                        color: Colors.red.shade200),
                                  ),
                                  child: Row(
                                    children: [
                                      Icon(Icons.error_outline_rounded,
                                          color: Colors.red.shade600,
                                          size: 16),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: Text(
                                          auth.error!,
                                          style: TextStyle(
                                              color: Colors.red.shade700,
                                              fontSize: 13),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],

                              const SizedBox(height: 24),

                              // Sign in button
                              SizedBox(
                                width: double.infinity,
                                height: 52,
                                child: DecoratedBox(
                                  decoration: BoxDecoration(
                                    gradient: const LinearGradient(
                                      colors: [_primary, _accent],
                                      begin: Alignment.centerLeft,
                                      end: Alignment.centerRight,
                                    ),
                                    borderRadius: BorderRadius.circular(14),
                                    boxShadow: [
                                      BoxShadow(
                                        color: _accent.withOpacity(0.3),
                                        blurRadius: 16,
                                        offset: const Offset(0, 6),
                                      ),
                                    ],
                                  ),
                                  child: ElevatedButton(
                                    style: ElevatedButton.styleFrom(
                                      backgroundColor: Colors.transparent,
                                      shadowColor: Colors.transparent,
                                      foregroundColor: Colors.white,
                                      shape: RoundedRectangleBorder(
                                        borderRadius:
                                            BorderRadius.circular(14),
                                      ),
                                    ),
                                    onPressed:
                                        auth.isLoading ? null : _login,
                                    child: auth.isLoading
                                        ? const SizedBox(
                                            width: 20,
                                            height: 20,
                                            child: CircularProgressIndicator(
                                              color: Colors.white,
                                              strokeWidth: 2,
                                            ),
                                          )
                                        : const Text(
                                            'Sign In',
                                            style: TextStyle(
                                              fontSize: 16,
                                              fontWeight: FontWeight.w600,
                                              letterSpacing: 0.3,
                                            ),
                                          ),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),

                      const SizedBox(height: 20),

                      // Forgot password
                      Center(
                        child: GestureDetector(
                          onTap: () => Navigator.of(context).push(
                            MaterialPageRoute(
                                builder: (_) =>
                                    const ForgotPasswordScreen()),
                          ),
                          child: const Text(
                            'Forgot Password?',
                            style: TextStyle(
                              color: _primary,
                              fontWeight: FontWeight.w600,
                              fontSize: 14,
                            ),
                          ),
                        ),
                      ),

                      const SizedBox(height: 32),

                      // Divider
                      Row(
                        children: [
                          Expanded(
                              child: Divider(
                                  color: Colors.grey.shade200,
                                  thickness: 1)),
                          const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 12),
                            child: Text('or',
                                style: TextStyle(
                                    color: _textMuted, fontSize: 13)),
                          ),
                          Expanded(
                              child: Divider(
                                  color: Colors.grey.shade200,
                                  thickness: 1)),
                        ],
                      ),

                      const SizedBox(height: 24),

                      // Social buttons
                      Row(
                        children: [
                          Expanded(
                            child: _socialBtn(
                              label: 'Google',
                              icon: Icons.g_mobiledata_rounded,
                              onTap: () {},
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _socialBtn(
                              label: 'Apple',
                              icon: Icons.apple_rounded,
                              onTap: () {},
                            ),
                          ),
                        ],
                      ),

                      const SizedBox(height: 32),

                      // Register
                      Center(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Text(
                              "Don't have an account? ",
                              style: TextStyle(
                                  color: Colors.grey.shade600,
                                  fontSize: 14),
                            ),
                            GestureDetector(
                              onTap: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                    builder: (_) =>
                                        const RegisterScreen()),
                              ),
                              child: const Text(
                                'Sign Up',
                                style: TextStyle(
                                  color: _primary,
                                  fontWeight: FontWeight.w700,
                                  fontSize: 14,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),

                      const SizedBox(height: 8),

                      // Guest
                      Center(
                        child: TextButton(
                          onPressed: () =>
                              Navigator.of(context).pushReplacement(
                            MaterialPageRoute(
                                builder: (_) => const SignSightHome()),
                          ),
                          child: Text(
                            'Continue as Guest',
                            style: TextStyle(
                                color: Colors.grey.shade400,
                                fontSize: 13),
                          ),
                        ),
                      ),

                      const SizedBox(height: 16),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildField({
    required TextEditingController controller,
    required String label,
    required String hint,
    required IconData icon,
    bool obscureText = false,
    Widget? suffix,
    TextInputType? keyboardType,
    String? Function(String?)? validator,
  }) {
    return TextFormField(
      controller: controller,
      obscureText: obscureText,
      keyboardType: keyboardType,
      validator: validator,
      style: const TextStyle(fontSize: 15, color: _primary),
      cursorColor: _accent,
      decoration: InputDecoration(
        labelText: label,
        labelStyle: const TextStyle(color: _textMuted, fontSize: 13),
        hintText: hint,
        hintStyle: TextStyle(color: Colors.grey.shade300, fontSize: 14),
        prefixIcon: Icon(icon, color: _primary, size: 19),
        suffixIcon: suffix != null
            ? Padding(
                padding: const EdgeInsets.only(right: 12),
                child: suffix,
              )
            : null,
        filled: true,
        fillColor: const Color(0xFFF8F9FF),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: Colors.grey.shade200),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: Colors.grey.shade200),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _accent, width: 1.8),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Colors.red),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Colors.red, width: 1.5),
        ),
        errorStyle:
            const TextStyle(color: Colors.red, fontSize: 12),
        contentPadding: const EdgeInsets.symmetric(
            horizontal: 16, vertical: 14),
      ),
    );
  }

  Widget _socialBtn({
    required String label,
    required IconData icon,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        height: 48,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: _border),
          boxShadow: [
            BoxShadow(
              color: _primary.withOpacity(0.04),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: _primary, size: 24),
            const SizedBox(width: 8),
            Text(
              label,
              style: const TextStyle(
                color: _primary,
                fontSize: 14,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// Subtle animated background orbs using original palette
class _BgPainter extends CustomPainter {
  final double t;
  _BgPainter(this.t);

  @override
  void paint(Canvas canvas, Size size) {
    final orbs = [
      _Orb(
        cx: size.width * 0.85,
        cy: size.height * 0.08 + math.sin(t * math.pi * 2) * 30,
        r: 160,
        color: const Color(0xFF4B6CF7).withOpacity(0.07),
      ),
      _Orb(
        cx: size.width * 0.05,
        cy: size.height * 0.35 + math.cos(t * math.pi * 2) * 25,
        r: 130,
        color: const Color(0xFF2B2D5D).withOpacity(0.05),
      ),
      _Orb(
        cx: size.width * 0.5,
        cy: size.height * 0.85 + math.sin(t * math.pi) * 20,
        r: 140,
        color: const Color(0xFF4B6CF7).withOpacity(0.06),
      ),
    ];

    for (final orb in orbs) {
      final paint = Paint()
        ..shader = RadialGradient(
          colors: [orb.color, orb.color.withOpacity(0)],
        ).createShader(Rect.fromCircle(
            center: Offset(orb.cx, orb.cy), radius: orb.r))
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 30);
      canvas.drawCircle(Offset(orb.cx, orb.cy), orb.r, paint);
    }
  }

  @override
  bool shouldRepaint(_BgPainter old) => old.t != t;
}

class _Orb {
  final double cx, cy, r;
  final Color color;
  const _Orb(
      {required this.cx,
      required this.cy,
      required this.r,
      required this.color});
}