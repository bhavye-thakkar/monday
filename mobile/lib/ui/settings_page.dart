// This phone's pairing: the PC it is paired with, how it is reached, what it may do, the credential's rotation, and Forget.
// Nothing here shows the secret, ever.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../core/session.dart';
import '../theme.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final t = Theme.of(context).textTheme;
    final c = s.creds;
    final h = s.hello;
    return ListView(
      padding: const EdgeInsets.fromLTRB(Tokens.gutter, 12, Tokens.gutter, 24),
      children: [
        Text('THIS PC', style: t.labelSmall),
        const SizedBox(height: 8),
        Card(
          child: Column(children: [
            _row('Name', c?.pc.name ?? 'N/A'),
            _row('Monday Core', h?.pc.version.isNotEmpty == true ? h!.pc.version : (c?.pc.version.isNotEmpty == true ? c!.pc.version : 'N/A')),
            _row('State', h?.state ?? 'N/A', color: h == null ? null : stateColor(h.state.toLowerCase())),
            _row('Connection', s.state.word, color: stateColor(s.state.key)),
            _row('On the LAN', c?.lanUrl?.isNotEmpty == true ? c!.lanUrl! : 'not set'),
            _row('Across networks', c?.publicUrl?.isNotEmpty == true ? c!.publicUrl! : 'not set up on the PC (ngrok)'),
            _row('Certificate', c?.fingerprint.isNotEmpty == true ? '${c!.fingerprint.substring(0, 16)}… (pinned)' : 'not pinned (public address only)', last: true),
          ]),
        ),
        const SizedBox(height: 18),
        Text('THIS PHONE MAY', style: t.labelSmall),
        const SizedBox(height: 8),
        Card(
          child: Column(children: [
            _row('Send prompts and stop tasks', h == null ? 'N/A' : (h.canControl ? 'yes' : 'no'), color: h == null ? null : (h.canControl ? Tokens.greenText : Tokens.redText)),
            _row('Answer approvals', h == null ? 'N/A' : (h.canApprove ? 'yes' : 'no, the PC\'s window answers'), color: h == null ? null : (h.canApprove ? Tokens.greenText : Tokens.amber), last: true),
          ]),
        ),
        const SizedBox(height: 6),
        Text('These are set on the PC in Settings → Phone, per device.', style: t.bodySmall),
        const SizedBox(height: 18),
        Text('CREDENTIAL', style: t.labelSmall),
        const SizedBox(height: 8),
        Card(
          child: Column(children: [
            _row('Device id', c?.deviceId ?? 'N/A'),
            _row('App', kAppVersion, last: true),
          ]),
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          onPressed: s.online ? () => _rotate(context) : null,
          icon: const Icon(Icons.autorenew, size: 18),
          label: const Text('Rotate the credential'),
        ),
        const SizedBox(height: 6),
        Text('A new secret from the PC replaces this one. Do it if you think the phone was copied.', style: t.bodySmall),
        const SizedBox(height: 22),
        OutlinedButton.icon(
          onPressed: () => _forget(context),
          style: OutlinedButton.styleFrom(foregroundColor: Tokens.redText),
          icon: const Icon(Icons.link_off, size: 18),
          label: const Text('Forget this PC'),
        ),
        const SizedBox(height: 6),
        Text('Deletes the credential here and on the PC. Pairing again needs a new code from the PC.', style: t.bodySmall),
      ],
    );
  }

  Widget _row(String label, String value, {Color? color, bool last = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(border: last ? null : const Border(bottom: BorderSide(color: Tokens.divider))),
      child: Row(
        children: [
          Text(label, style: const TextStyle(color: Tokens.textNav, fontSize: 13.5)),
          const SizedBox(width: 12),
          Expanded(child: Text(value, textAlign: TextAlign.right, style: TextStyle(color: color ?? Tokens.text, fontSize: 13.5), overflow: TextOverflow.ellipsis, maxLines: 2)),
        ],
      ),
    );
  }

  Future<void> _rotate(BuildContext context) async {
    final err = await context.read<Session>().rotate();
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(err == null ? 'The credential was rotated' : 'Not rotated: $err')));
    }
  }

  Future<void> _forget(BuildContext context) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: Tokens.card,
        title: const Text('Forget this PC?'),
        content: const Text('The credential is deleted here and revoked on the PC. You pair again with a new code.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Forget')),
        ],
      ),
    );
    if (yes == true && context.mounted) await context.read<Session>().forget();
  }
}
