// Monday companion: a remote client of the owner's PC. It pairs once with a code shown in Monday's window, keeps a revocable
// device credential, and shows the PC's own conversation, tasks, agents and approvals as the PC reports them. It is not an
// assistant of its own: every prompt goes to the PC's Monday Core, which decides (Jev) and executes (Hermes and the tools).
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/session.dart';
import 'theme.dart';
import 'ui/pair_screen.dart';
import 'ui/shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final session = Session();
  session.start();
  runApp(ChangeNotifierProvider.value(value: session, child: const CompanionApp()));
}

class CompanionApp extends StatefulWidget {
  const CompanionApp({super.key});
  @override
  State<CompanionApp> createState() => _CompanionAppState();
}

class _CompanionAppState extends State<CompanionApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    // Back in the foreground: the stream may have died while the phone slept; reconnect and replay what was missed.
    if (s == AppLifecycleState.resumed) {
      final session = context.read<Session>();
      if (session.paired && session.state != Connection.authRequired) session.retryNow();
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Monday',
      theme: mondayTheme(),
      themeMode: ThemeMode.dark,
      debugShowCheckedModeBanner: false,
      home: Consumer<Session>(
        builder: (context, s, _) => s.paired ? const Shell() : const PairScreen(),
      ),
    );
  }
}
