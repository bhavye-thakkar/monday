// Monday's visual world on the phone: the same tokens as the desktop window (core/qtkit.py, measured from the owner's Coucou
// recording, docs/design/REFERENCE.md). Dark only, like the window; every colour maps to a real state, nothing decorates.
import 'package:flutter/material.dart';

abstract final class Tokens {
  static const rail = Color(0xFF000000);
  static const panel = Color(0xFF0E0E11);
  static const panelEdge = Color(0xFF1C1C1F);
  static const card = Color(0xFF111516);
  static const cardUp = Color(0xFF15191A);
  static const edge = Color(0xFF1F2427);
  static const divider = Color(0xFF1B2023);
  static const field = Color(0xFF1D2125);
  static const input = Color(0xFF242428);
  static const fieldEdge = Color(0xFF2B3034);
  static const button = Color(0xFF24282C);
  static const buttonHover = Color(0xFF2E3338);
  static const chip = Color(0xFF232428);
  static const chipEdge = Color(0xFF333438);
  static const text = Color(0xFFF5F5F6);
  static const textNav = Color(0xFFC8CACD);
  static const textMuted = Color(0xFF9A9DA3);
  static const textOff = Color(0xFF5D6166);
  static const icon = Color(0xFFB9BCC2);
  static const orange = Color(0xFFF08C40);
  static const orangeHover = Color(0xFFF59D58);
  static const onOrange = Color(0xFF1B1208);
  static const green = Color(0xFF2CDC60);
  static const greenText = Color(0xFF3ECF6E);
  static const red = Color(0xFFF0445A);
  static const redText = Color(0xFFFF6B7A);
  static const redBg = Color(0xFF341418);
  static const amber = Color(0xFFF0A040);
  static const amberBg = Color(0xFF3A2A14);
  static const blue = Color(0xFF5AA9FF);
  static const off = Color(0xFF3A3A3C);
  static const knob = Color(0xFFFFFFFF);

  static const radiusCard = 14.0;
  static const radiusField = 10.0;
  static const radiusChip = 999.0;
  static const gutter = 16.0;
}

ThemeData mondayTheme() {
  const scheme = ColorScheme(
    brightness: Brightness.dark,
    primary: Tokens.orange,
    onPrimary: Tokens.onOrange,
    secondary: Tokens.green,
    onSecondary: Tokens.onOrange,
    error: Tokens.red,
    onError: Tokens.text,
    surface: Tokens.panel,
    onSurface: Tokens.text,
    surfaceContainerHighest: Tokens.cardUp,
    outline: Tokens.edge,
  );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme, brightness: Brightness.dark);
  final text = base.textTheme.apply(bodyColor: Tokens.text, displayColor: Tokens.text);
  return base.copyWith(
    scaffoldBackgroundColor: Tokens.panel,
    canvasColor: Tokens.panel,
    dividerColor: Tokens.divider,
    textTheme: text.copyWith(
      titleLarge: text.titleLarge?.copyWith(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: -0.3),
      titleMedium: text.titleMedium?.copyWith(fontSize: 16, fontWeight: FontWeight.w600),
      bodyMedium: text.bodyMedium?.copyWith(fontSize: 14.5, height: 1.4),
      bodySmall: text.bodySmall?.copyWith(fontSize: 12.5, color: Tokens.textMuted),
      labelSmall: text.labelSmall?.copyWith(fontSize: 11, letterSpacing: 0.8, color: Tokens.textMuted),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: Tokens.rail,
      foregroundColor: Tokens.text,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(color: Tokens.text, fontSize: 17, fontWeight: FontWeight.w600),
    ),
    cardTheme: CardTheme(
      color: Tokens.card,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(Tokens.radiusCard),
        side: const BorderSide(color: Tokens.edge),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Tokens.input,
      hintStyle: const TextStyle(color: Tokens.textOff),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(Tokens.radiusField),
        borderSide: const BorderSide(color: Tokens.fieldEdge),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(Tokens.radiusField),
        borderSide: const BorderSide(color: Tokens.fieldEdge),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(Tokens.radiusField),
        borderSide: const BorderSide(color: Tokens.orange, width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: Tokens.orange,
        foregroundColor: Tokens.onOrange,
        minimumSize: const Size(44, 44),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Tokens.radiusField)),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: Tokens.text,
        backgroundColor: Tokens.button,
        side: const BorderSide(color: Tokens.chipEdge),
        minimumSize: const Size(44, 44),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Tokens.radiusField)),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: Tokens.textNav, minimumSize: const Size(44, 44)),
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: Tokens.cardUp,
      contentTextStyle: TextStyle(color: Tokens.text),
      behavior: SnackBarBehavior.floating,
    ),
    bottomSheetTheme: const BottomSheetThemeData(backgroundColor: Tokens.card, surfaceTintColor: Colors.transparent),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: Tokens.rail,
      indicatorColor: Tokens.chip,
      iconTheme: WidgetStateProperty.resolveWith(
        (s) => IconThemeData(color: s.contains(WidgetState.selected) ? Tokens.text : Tokens.icon),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (s) => TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: s.contains(WidgetState.selected) ? Tokens.text : Tokens.textNav,
        ),
      ),
    ),
    textSelectionTheme: const TextSelectionThemeData(
      cursorColor: Tokens.orange,
      selectionColor: Color(0x55F08C40),
      selectionHandleColor: Tokens.orange,
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(color: Tokens.orange, linearTrackColor: Tokens.chip),
  );
}

/// The colour of a state word, the window's vocabulary: orange is working, green done, red failed, amber waiting, grey off.
Color stateColor(String state) {
  switch (state) {
    case 'running':
    case 'executing':
    case 'planning':
    case 'thinking':
    case 'working':
      return Tokens.orange;
    case 'done':
    case 'completed':
    case 'succeeded':
    case 'connected':
    case 'online':
    case 'authenticated':
      return Tokens.greenText;
    case 'failed':
    case 'error':
    case 'cancelled':
    case 'disconnected':
    case 'offline':
    case 'revoked':
      return Tokens.redText;
    case 'waiting':
    case 'waiting_for_permission':
    case 'connecting':
    case 'reconnecting':
    case 'authentication required':
    case 'degraded':
      return Tokens.amber;
    default:
      return Tokens.textMuted;
  }
}
