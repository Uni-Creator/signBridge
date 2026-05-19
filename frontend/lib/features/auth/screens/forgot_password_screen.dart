import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../poviders/auth_provider.dart';
import 'login_screen.dart';
import 'dart:math' as math;

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen>
    with TickerProviderStateMixin {
  final _formKey = GlobalKey<FormState>();
  final _emailCtrl = TextEditingController();

  late AnimationController _bgController;
  late AnimationController _cardController;
  late Animation<double> _cardAnimation;

  bool _emailSent = false;

  // Original color palette
  static const Color _primary   = Color(0xFF2B2D5D);
  static const Color _accent    = Color(0xFF4B6CF7);
  static const Color _bg        = Color(0xFFF5F7FF);
  static const Color _surface   = Colors.white;
  static const Color _border    = Color(0xFFE2E6FF);
  static const Color _textMuted = Color(0xFF8A8FAD);

  @override
  void initState() {
    super.initState();
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
    _bgController.dispose();
    _cardController.dispose();
    super.dispose();
  }

  Future<void> _sendReset() async {
    if (!_formKey.currentState!.validate()) return;
    final auth = context.read<AuthProvider>();
    final success =
        await auth.forgotPassword(_emailCtrl.text.trim());
    if (!mounted) return;
    if (success) {
      setState(() => _emailSent = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();

    return Scaffold(
      backgroundColor: _bg,
      body: Stack(
        children: [
          // Animated background
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
                      horizontal: 24, vertical: 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Back button
                      GestureDetector(
                        onTap: () => Navigator.pop(context),
                        child: Container(
                          width: 40,
                          height: 40,
                          decoration: BoxDecoration(
                            color: Colors.white,
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: _border),
                            boxShadow: [
                              BoxShadow(
                                color: _primary.withOpacity(0.06),
                                blurRadius: 8,
                                offset: const Offset(0, 2),
                              ),
                            ],
                          ),
                          child: const Icon(
                              Icons.arrow_back_ios_new_rounded,
                              color: _primary,
                              size: 18),
                        ),
                      ),

                      const SizedBox(height: 28),

                      // Icon
                      Container(
                        width: 64,
                        height: 64,
                        decoration: BoxDecoration(
                          color: _primary,
                          borderRadius: BorderRadius.circular(18),
                          boxShadow: [
                            BoxShadow(
                              color: _primary.withOpacity(0.25),
                              blurRadius: 16,
                              offset: const Offset(0, 6),
                            ),
                          ],
                        ),
                        child: const Icon(Icons.lock_reset_rounded,
                            color: Colors.white, size: 30),
                      ),

                      const SizedBox(height: 24),

                      // Headline
                      const Text(
                        'Forgot\nPassword?',
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
                        'Enter your email and we\'ll send\nyou a reset link.',
                        style: TextStyle(
                            fontSize: 15,
                            color: Colors.grey.shade500,
                            height: 1.5),
                      ),

                      const SizedBox(height: 36),

                      // Success state
                      if (_emailSent) ...[
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(20),
                          decoration: BoxDecoration(
                            color: Colors.green.shade50,
                            borderRadius: BorderRadius.circular(16),
                            border:
                                Border.all(color: Colors.green.shade200),
                          ),
                          child: Column(
                            children: [
                              Icon(Icons.mark_email_read_rounded,
                                  color: Colors.green.shade600, size: 40),
                              const SizedBox(height: 12),
                              Text(
                                'Reset link sent!',
                                style: TextStyle(
                                  color: Colors.green.shade700,
                                  fontSize: 17,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                              const SizedBox(height: 6),
                              Text(
                                'Check your inbox at\n${_emailCtrl.text.trim()}',
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  color: Colors.green.shade600,
                                  fontSize: 13,
                                  height: 1.5,
                                ),
                              ),
                            ],
                          ),
                        ),

                        const SizedBox(height: 24),

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
                                  borderRadius: BorderRadius.circular(14),
                                ),
                              ),
                              onPressed: () =>
                                  Navigator.of(context).pushReplacement(
                                MaterialPageRoute(
                                    builder: (_) => const LoginScreen()),
                              ),
                              child: const Text(
                                'Back to Sign In',
                                style: TextStyle(
                                  fontSize: 16,
                                  fontWeight: FontWeight.w600,
                                  letterSpacing: 0.3,
                                ),
                              ),
                            ),
                          ),
                        ),
                      ] else ...[
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
                                TextFormField(
                                  controller: _emailCtrl,
                                  keyboardType: TextInputType.emailAddress,
                                  style: const TextStyle(
                                      fontSize: 15, color: _primary),
                                  cursorColor: _accent,
                                  validator: (v) {
                                    if (v == null || v.isEmpty)
                                      return 'Enter your email';
                                    if (!v.contains('@'))
                                      return 'Invalid email';
                                    return null;
                                  },
                                  decoration: InputDecoration(
                                    labelText: 'Email',
                                    labelStyle: const TextStyle(
                                        color: _textMuted, fontSize: 13),
                                    hintText: 'you@example.com',
                                    hintStyle: TextStyle(
                                        color: Colors.grey.shade300,
                                        fontSize: 14),
                                    prefixIcon: const Icon(
                                        Icons.alternate_email_rounded,
                                        color: _primary,
                                        size: 19),
                                    filled: true,
                                    fillColor: const Color(0xFFF8F9FF),
                                    border: OutlineInputBorder(
                                      borderRadius:
                                          BorderRadius.circular(12),
                                      borderSide: BorderSide(
                                          color: Colors.grey.shade200),
                                    ),
                                    enabledBorder: OutlineInputBorder(
                                      borderRadius:
                                          BorderRadius.circular(12),
                                      borderSide: BorderSide(
                                          color: Colors.grey.shade200),
                                    ),
                                    focusedBorder: OutlineInputBorder(
                                      borderRadius:
                                          BorderRadius.circular(12),
                                      borderSide: const BorderSide(
                                          color: _accent, width: 1.8),
                                    ),
                                    errorBorder: OutlineInputBorder(
                                      borderRadius:
                                          BorderRadius.circular(12),
                                      borderSide: const BorderSide(
                                          color: Colors.red),
                                    ),
                                    focusedErrorBorder: OutlineInputBorder(
                                      borderRadius:
                                          BorderRadius.circular(12),
                                      borderSide: const BorderSide(
                                          color: Colors.red, width: 1.5),
                                    ),
                                    errorStyle: const TextStyle(
                                        color: Colors.red, fontSize: 12),
                                    contentPadding:
                                        const EdgeInsets.symmetric(
                                            horizontal: 16, vertical: 14),
                                  ),
                                ),

                                // Error
                                if (auth.error != null) ...[
                                  const SizedBox(height: 14),
                                  Container(
                                    width: double.infinity,
                                    padding: const EdgeInsets.all(12),
                                    decoration: BoxDecoration(
                                      color: Colors.red.shade50,
                                      borderRadius:
                                          BorderRadius.circular(12),
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

                                // Send reset button
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
                                      borderRadius:
                                          BorderRadius.circular(14),
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
                                          auth.isLoading ? null : _sendReset,
                                      child: auth.isLoading
                                          ? const SizedBox(
                                              width: 20,
                                              height: 20,
                                              child:
                                                  CircularProgressIndicator(
                                                color: Colors.white,
                                                strokeWidth: 2,
                                              ),
                                            )
                                          : const Text(
                                              'Send Reset Link',
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

                        const SizedBox(height: 28),

                        // Back to sign in
                        Center(
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Text(
                                'Remember your password? ',
                                style: TextStyle(
                                    color: Colors.grey.shade600,
                                    fontSize: 14),
                              ),
                              GestureDetector(
                                onTap: () => Navigator.of(context).pop(),
                                child: const Text(
                                  'Sign In',
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
                      ],

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
}

class _BgPainter extends CustomPainter {
  final double t;
  _BgPainter(this.t);

  @override
  void paint(Canvas canvas, Size size) {
    final orbs = [
      _Orb(
        cx: size.width * 0.85,
        cy: size.height * 0.1 + math.sin(t * math.pi * 2) * 30,
        r: 160,
        color: const Color(0xFF4B6CF7).withOpacity(0.07),
      ),
      _Orb(
        cx: size.width * 0.1,
        cy: size.height * 0.5 + math.cos(t * math.pi * 2) * 25,
        r: 130,
        color: const Color(0xFF2B2D5D).withOpacity(0.05),
      ),
      _Orb(
        cx: size.width * 0.5,
        cy: size.height * 0.9 + math.sin(t * math.pi) * 20,
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