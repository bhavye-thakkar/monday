// The PC's conversation, the same rows Monday's window shows, newest at the bottom; the composer sends to the PC. A reply streams
// as the PC streams it; a task made by a prompt shows its card under that prompt; a send the PC never received says so and
// goes when the PC is back.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../core/session.dart';
import '../theme.dart';
import 'task_card.dart';

class ChatPage extends StatefulWidget {
  const ChatPage({super.key});
  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final _text = TextEditingController();
  final _scroll = ScrollController();
  bool _loadingOlder = false;
  bool _noOlder = false;
  int _lastCount = 0;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    // Reverse list: the "top" of the thread is maxScrollExtent. Near it, load the page before.
    if (_loadingOlder || _noOlder) return;
    if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 200) _older();
  }

  Future<void> _older() async {
    setState(() => _loadingOlder = true);
    final more = await context.read<Session>().loadOlder();
    if (!mounted) return;
    setState(() {
      _loadingOlder = false;
      _noOlder = !more;
    });
  }

  Future<void> _send() async {
    final t = _text.text;
    if (t.trim().isEmpty) return;
    _text.clear();
    await context.read<Session>().send(t);
    if (_scroll.hasClients) _scroll.animateTo(0, duration: const Duration(milliseconds: 240), curve: Curves.easeOutCubic);
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final msgs = s.messages;
    // A task's card once, under the first row that carries its id (its title row); its reply names the task again, without the card.
    final firstCard = <String, int>{};
    for (final m in msgs) {
      if (m.taskId != null) firstCard.putIfAbsent(m.taskId!, () => m.id);
    }
    if (msgs.length != _lastCount) {
      _lastCount = msgs.length;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients && _scroll.position.pixels < 120) _scroll.jumpTo(0);
      });
    }
    return Column(
      children: [
        Expanded(
          child: msgs.isEmpty
              ? _Empty(session: s)
              : ListView.builder(
                  controller: _scroll,
                  reverse: true,
                  padding: const EdgeInsets.fromLTRB(Tokens.gutter, 12, Tokens.gutter, 12),
                  itemCount: msgs.length + 1,
                  itemBuilder: (context, i) {
                    if (i == msgs.length) {
                      return _loadingOlder
                          ? const Padding(padding: EdgeInsets.all(12), child: Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))))
                          : const SizedBox(height: 8);
                    }
                    final m = msgs[msgs.length - 1 - i];
                    return _Row(message: m, session: s, showCard: m.taskId != null && firstCard[m.taskId!] == m.id);
                  },
                ),
        ),
        _Composer(controller: _text, onSend: _send, enabled: s.paired),
      ],
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.message, required this.session, this.showCard = true});
  final Message message;
  final Session session;
  final bool showCard;

  @override
  Widget build(BuildContext context) {
    final m = message;
    final mine = m.role == 'user';
    final err = m.id < 0 ? session.pendingError(m.id) : null;
    final task = showCard && m.taskId != null ? session.tasks[m.taskId!] : null;
    final t = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(
        crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          ConstrainedBox(
            constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.84),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: mine ? Tokens.chip : Tokens.card,
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(Tokens.radiusCard),
                  topRight: const Radius.circular(Tokens.radiusCard),
                  bottomLeft: Radius.circular(mine ? Tokens.radiusCard : 4),
                  bottomRight: Radius.circular(mine ? 4 : Tokens.radiusCard),
                ),
                border: Border.all(color: mine ? Tokens.chipEdge : Tokens.edge),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (m.role == 'system') Text('MONDAY · SYSTEM', style: t.labelSmall),
                  SelectableText(m.text.isEmpty && m.streaming ? '…' : m.text, style: t.bodyMedium),
                  if (m.streaming)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Row(mainAxisSize: MainAxisSize.min, children: [
                        const SizedBox(width: 10, height: 10, child: CircularProgressIndicator(strokeWidth: 1.5)),
                        const SizedBox(width: 6),
                        Text('streaming', style: t.bodySmall),
                      ]),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 3),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (err != null) ...[
                const Icon(Icons.schedule, size: 12, color: Tokens.amber),
                const SizedBox(width: 4),
                Flexible(child: Text(err, style: t.bodySmall?.copyWith(color: Tokens.amber))),
              ] else ...[
                Text(_when(m.ts), style: t.bodySmall),
                if (!mine && m.model != null) Text(' · ${m.model}', style: t.bodySmall),
                if (mine && m.source.startsWith('phone')) Text(' · from this phone', style: t.bodySmall),
              ],
            ],
          ),
          if (task != null) Padding(padding: const EdgeInsets.only(top: 8), child: TaskCard(task: task, compact: true)),
        ],
      ),
    );
  }

  String _when(double ts) {
    if (ts <= 0) return '';
    final d = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    final now = DateTime.now();
    final hm = '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    if (d.year == now.year && d.month == now.month && d.day == now.day) return hm;
    return '${d.day}/${d.month} $hm';
  }
}

class _Composer extends StatelessWidget {
  const _Composer({required this.controller, required this.onSend, required this.enabled});
  final TextEditingController controller;
  final VoidCallback onSend;
  final bool enabled;
  @override
  Widget build(BuildContext context) {
    return Material(
      color: Tokens.rail,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(Tokens.gutter, 8, 8, 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  enabled: enabled,
                  minLines: 1,
                  maxLines: 5,
                  textInputAction: TextInputAction.send,
                  textCapitalization: TextCapitalization.sentences,
                  onSubmitted: (_) => onSend(),
                  decoration: const InputDecoration(hintText: 'Tell Monday what to do on the PC'),
                ),
              ),
              const SizedBox(width: 6),
              IconButton.filled(
                tooltip: 'Send to the PC',
                onPressed: enabled ? onSend : null,
                style: IconButton.styleFrom(backgroundColor: Tokens.orange, foregroundColor: Tokens.onOrange, minimumSize: const Size(44, 44)),
                icon: const Icon(Icons.arrow_upward),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.session});
  final Session session;
  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final online = session.online;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(online ? Icons.desktop_windows_outlined : Icons.desktop_access_disabled_outlined, size: 40, color: Tokens.textOff),
            const SizedBox(height: 14),
            Text(online ? 'Nothing said yet' : 'No conversation loaded', style: t.titleMedium),
            const SizedBox(height: 6),
            Text(
              online
                  ? 'What you send here runs on ${session.creds?.pc.name ?? 'the PC'}, and the PC\'s replies show up here.'
                  : 'The thread loads when the PC answers.',
              textAlign: TextAlign.center,
              style: t.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
