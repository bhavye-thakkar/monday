// A permission request the PC is waiting on (router.permit asked). One card, Allow or Deny, the risk word as the PC gave it.
// Only a phone whose approve capability is on can answer; otherwise the card says it is the PC's window that decides.
import 'package:flutter/material.dart';

import '../core/models.dart';
import '../core/session.dart';
import '../theme.dart';

class ApprovalBanner extends StatelessWidget {
  const ApprovalBanner({super.key, required this.session});
  final Session session;

  @override
  Widget build(BuildContext context) {
    final a = session.approvals.first;
    final more = session.approvals.length - 1;
    final can = session.hello?.canApprove ?? false;
    return Material(
      color: Tokens.amberBg,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Tokens.gutter, 12, Tokens.gutter, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.shield_outlined, color: Tokens.amber, size: 18),
                const SizedBox(width: 8),
                Text('MONDAY ASKS', style: Theme.of(context).textTheme.labelSmall?.copyWith(color: Tokens.amber)),
                const Spacer(),
                _RiskChip(a.risk),
                if (more > 0) ...[const SizedBox(width: 8), Text('+$more more', style: const TextStyle(color: Tokens.amber, fontSize: 12))],
              ],
            ),
            const SizedBox(height: 8),
            Text(a.what, style: const TextStyle(color: Tokens.text, fontSize: 14.5, height: 1.35)),
            if (a.taskId != null) ...[
              const SizedBox(height: 4),
              Text('Task ${a.taskId}${a.agent != null ? ' · ${a.agent}' : ''}', style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 10),
            if (can)
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => _decide(context, a, false),
                      style: OutlinedButton.styleFrom(foregroundColor: Tokens.redText),
                      child: const Text('Deny'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(child: FilledButton(onPressed: () => _decide(context, a, true), child: const Text('Allow'))),
                ],
              )
            else
              Text('Approving from this phone is switched off on the PC. Answer in Monday\'s window.',
                  style: const TextStyle(color: Tokens.amber, fontSize: 13)),
          ],
        ),
      ),
    );
  }

  Future<void> _decide(BuildContext context, Approval a, bool allow) async {
    final err = await session.decide(a, allow);
    if (err != null && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Not answered: $err')));
    }
  }
}

class _RiskChip extends StatelessWidget {
  const _RiskChip(this.risk);
  final String risk;
  @override
  Widget build(BuildContext context) {
    final high = risk.toUpperCase() == 'HIGH';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: high ? Tokens.redBg : Tokens.chip,
        borderRadius: BorderRadius.circular(Tokens.radiusChip),
        border: Border.all(color: high ? Tokens.red : Tokens.chipEdge),
      ),
      child: Text(risk.isEmpty ? 'RISK ?' : risk.toUpperCase(),
          style: TextStyle(fontSize: 11, letterSpacing: 0.6, fontWeight: FontWeight.w700, color: high ? Tokens.redText : Tokens.textNav)),
    );
  }
}
