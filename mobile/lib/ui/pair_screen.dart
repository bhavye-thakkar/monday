// Pairing: the owner opens Settings → Phone in Monday's window on the PC, which shows the PC's address and an 8-digit code good
// for two minutes. The phone sends both once and keeps the credential it gets back. Nothing permanent is ever typed or shown.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../theme.dart';

class PairScreen extends StatefulWidget {
  const PairScreen({super.key});
  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  final _address = TextEditingController();
  final _code = TextEditingController();
  final _name = TextEditingController(text: Platform.isIOS ? 'iPhone' : 'Android phone');
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _address.dispose();
    _code.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _pair() async {
    final s = context.read<Session>();
    setState(() {
      _busy = true;
      _error = null;
    });
    final err = await s.pair(address: _address.text, code: _code.text, deviceName: _name.text.trim().isEmpty ? 'Phone' : _name.text.trim());
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = err;
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final codeOk = _code.text.replaceAll(RegExp(r'\D'), '').length == 8;
    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(Tokens.gutter, 40, Tokens.gutter, 24),
          children: [
            Text('Pair with your PC', style: t.titleLarge),
            const SizedBox(height: 8),
            Text(
              'On the PC, open Monday → Settings → Phone and tap "Pair a phone". It shows the address and a code that lasts two minutes.',
              style: t.bodyMedium?.copyWith(color: Tokens.textNav),
            ),
            const SizedBox(height: 28),
            Text('PC ADDRESS', style: t.labelSmall),
            const SizedBox(height: 6),
            TextField(
              controller: _address,
              keyboardType: TextInputType.url,
              autocorrect: false,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(hintText: 'https://192.168.1.20:8790'),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: 18),
            Text('PAIRING CODE', style: t.labelSmall),
            const SizedBox(height: 6),
            TextField(
              controller: _code,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(8)],
              style: const TextStyle(fontSize: 24, letterSpacing: 6, fontFeatures: [FontFeature.tabularFigures()]),
              decoration: const InputDecoration(hintText: '12345678'),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => codeOk && !_busy ? _pair() : null,
            ),
            const SizedBox(height: 18),
            Text('THIS PHONE\'S NAME', style: t.labelSmall),
            const SizedBox(height: 6),
            TextField(controller: _name, textCapitalization: TextCapitalization.words, decoration: const InputDecoration(hintText: 'Phone')),
            const SizedBox(height: 28),
            if (_error != null) ...[
              _Problem(_error!),
              const SizedBox(height: 14),
            ],
            FilledButton(
              onPressed: _busy || !codeOk || _address.text.trim().isEmpty ? null : _pair,
              child: _busy
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Tokens.onOrange))
                  : const Text('Pair'),
            ),
            const SizedBox(height: 24),
            Text(
              'The code is used once. Afterwards this phone keeps its own credential in secure storage, until you revoke it on the PC or forget the PC here.',
              style: t.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _Problem extends StatelessWidget {
  const _Problem(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Tokens.redBg, borderRadius: BorderRadius.circular(Tokens.radiusField)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.error_outline, color: Tokens.redText, size: 18),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(color: Tokens.redText))),
        ],
      ),
    );
  }
}
