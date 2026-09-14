import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sign_bridge/main.dart';
import 'package:sign_bridge/features/auth/screens/login_screen.dart';
import 'package:sign_bridge/features/splash/screens/splash_screen.dart';

void main() {
  testWidgets('a signed-out user reaches login after the splash screen',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const MyApp());
    expect(find.byType(SplashScreen), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.byType(LoginScreen), findsOneWidget);
    // Login has a repeating background animation; do not pumpAndSettle.
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
