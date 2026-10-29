// The paired app: a status line that always says whether the PC is reachable, the pending approvals above everything, and four
// pages: Chat, Tasks, Agents, Settings.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../theme.dart';
import 'agents_page.dart';
import 'approvals.dart';
import 'chat_page.dart';
import 'settings_page.dart';
import 'tasks_page.dart';

class Shell extends StatefulWidget {
  const Shell({super.key});
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int _page = 0;

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final pages = const [ChatPage(), TasksPage(), AgentsPage(), SettingsPage()];
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Text(s.creds?.pc.name.isNotEmpty == true ? s.creds!.pc.name : 'Monday'),
            const SizedBox(width: 10),
            StatusDot(state: s.state),
          ],
        ),
        actions: [
          if (s.state != Connection.connected && s.state != Connection.authRequired)
            IconButton(tooltip: 'Retry now', onPressed: s.retryNow, icon: const Icon(Icons.refresh)),
        ],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Container(height: 1, color: Tokens.panelEdge)),
      ),
      body: Column(
        children: [
          ConnectionBanner(session: s),
          if (s.approvals.isNotEmpty) ApprovalBanner(session: s),
          Expanded(child: IndexedStack(index: _page, children: pages)),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _page,
        onDestinationSelected: (i) => setState(() => _page = i),
        destinations: [
          const NavigationDestination(icon: Icon(Icons.chat_bubble_outline), selectedIcon: Icon(Icons.chat_bubble), label: 'Chat'),
          NavigationDestination(
            icon: Badge.count(count: s.liveTasks.length, isLabelVisible: s.liveTasks.isNotEmpty, child: const Icon(Icons.task_alt_outlined)),
            selectedIcon: const Icon(Icons.task_alt),
            label: 'Tasks',
          ),
          const NavigationDestination(icon: Icon(Icons.smart_toy_outlined), selectedIcon: Icon(Icons.smart_toy), label: 'Agents'),
          const NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}

/// The dot and word of the connection: green connected, amber connecting or reconnecting, red disconnected or revoked.
class StatusDot extends StatelessWidget {
  const StatusDot({super.key, required this.state});
  final Connection state;
  @override
  Widget build(BuildContext context) {
    final c = stateColor(state.key);
    return Semantics(
      label: 'PC ${state.word}',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
          const SizedBox(width: 6),
          Text(state.word, style: TextStyle(fontSize: 12.5, color: c, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

/// Shown only when the PC is not simply connected: what is wrong, in the PC's or our own words, and what the owner can do.
class ConnectionBanner extends StatelessWidget {
  const ConnectionBanner({super.key, required this.session});
  final Session session;
  @override
  Widget build(BuildContext context) {
    final s = session;
    if (s.state == Connection.connected) return const SizedBox.shrink();
    final auth = s.state == Connection.authRequired;
    final color = auth || s.state == Connection.disconnected ? Tokens.redText : Tokens.amber;
    final bg = auth || s.state == Connection.disconnected ? Tokens.redBg : Tokens.amberBg;
    final line = switch (s.state) {
      Connection.connecting => 'Reaching ${s.creds?.pc.name ?? 'the PC'}…',
      Connection.reconnecting => 'Reconnecting. ${s.lastError ?? ''}'.trim(),
      Connection.disconnected => 'The PC is unavailable. ${s.lastError ?? ''}'.trim(),
      Connection.authRequired => 'This phone is no longer authorized: ${s.lastError ?? 'the credential was refused'}. Pair again from the PC.',
      _ => '',
    };
    return Material(
      color: bg,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Tokens.gutter, 10, 8, 10),
        child: Row(
          children: [
            Expanded(child: Text(line, style: TextStyle(color: color, fontSize: 13))),
            if (auth)
              TextButton(
                onPressed: () => _forget(context),
                child: const Text('Pair again', style: TextStyle(color: Tokens.text)),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _forget(BuildContext context) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: Tokens.card,
        title: const Text('Forget this PC?'),
        content: const Text('The credential on this phone is deleted. You pair again with a new code from the PC.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Forget')),
        ],
      ),
    );
    if (yes == true && context.mounted) await context.read<Session>().forget();
  }
}
