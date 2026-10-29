// The island's agent rows on the phone: who works on what, the step and tool as the PC reports them, waiting for an approval,
// done or failed. A count of steps only when the PC counted; otherwise "Working…", never a percentage.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../core/session.dart';
import '../theme.dart';

class AgentsPage extends StatelessWidget {
  const AgentsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final t = Theme.of(context).textTheme;
    final rows = s.agents;
    return RefreshIndicator(
      color: Tokens.orange,
      onRefresh: s.refreshAgents,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(Tokens.gutter, 12, Tokens.gutter, 24),
        children: [
          if (rows.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 60),
              child: Column(children: [
                const Icon(Icons.smart_toy_outlined, size: 40, color: Tokens.textOff),
                const SizedBox(height: 12),
                Text('No agent at work', style: t.titleMedium),
                const SizedBox(height: 4),
                Text(s.online ? 'Agents appear here while the PC runs them.' : 'The list loads when the PC answers.', style: t.bodySmall),
              ]),
            ),
          for (final a in rows) Padding(padding: const EdgeInsets.only(bottom: 10), child: _AgentCard(a)),
        ],
      ),
    );
  }
}

class _AgentCard extends StatelessWidget {
  const _AgentCard(this.a);
  final AgentRow a;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final c = a.waitingForApproval ? Tokens.amber : stateColor(a.state);
    final word = a.waitingForApproval ? 'WAITING FOR YOU' : (a.state.isEmpty ? 'N/A' : a.state.toUpperCase());
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(color: Tokens.chip, borderRadius: BorderRadius.circular(8), border: Border.all(color: Tokens.chipEdge)),
                  alignment: Alignment.center,
                  child: Icon(_icon(a.kind), size: 16, color: Tokens.icon),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(a.name.isEmpty ? a.id : a.name, style: t.titleMedium?.copyWith(fontSize: 15)),
                    Text(a.kind, style: t.bodySmall),
                  ]),
                ),
                Text(word, style: TextStyle(color: c, fontWeight: FontWeight.w700, fontSize: 11.5, letterSpacing: 0.5)),
              ],
            ),
            if (a.taskId != null) ...[const SizedBox(height: 8), Text('Task ${a.taskId}', style: t.bodySmall)],
            if (a.step.isNotEmpty || a.tool.isNotEmpty) ...[
              const SizedBox(height: 8),
              Row(children: [
                if (a.tool.isNotEmpty) _chip(a.tool),
                if (a.tool.isNotEmpty && a.step.isNotEmpty) const SizedBox(width: 8),
                if (a.step.isNotEmpty) Expanded(child: Text(a.step, style: const TextStyle(fontSize: 13, color: Tokens.textNav))),
              ]),
            ],
            if (a.progressTotal != null && a.progressTotal! > 0) ...[
              const SizedBox(height: 10),
              ClipRRect(borderRadius: BorderRadius.circular(3), child: LinearProgressIndicator(value: (a.progressDone ?? 0) / a.progressTotal!, minHeight: 4)),
              const SizedBox(height: 4),
              Text('${a.progressDone ?? 0} of ${a.progressTotal}', style: t.bodySmall),
            ] else if (a.state == 'working') ...[
              const SizedBox(height: 8),
              Text('Working…', style: t.bodySmall?.copyWith(color: Tokens.orange)),
            ],
            if (a.result != null && a.result!.isNotEmpty) ...[const SizedBox(height: 8), Text(a.result!, style: t.bodyMedium, maxLines: 4, overflow: TextOverflow.ellipsis)],
            if (a.error != null && a.error!.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(a.error!, style: const TextStyle(color: Tokens.redText, fontSize: 13)),
            ],
          ],
        ),
      ),
    );
  }

  Widget _chip(String text) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: Tokens.chip, borderRadius: BorderRadius.circular(Tokens.radiusChip), border: Border.all(color: Tokens.chipEdge)),
        child: Text(text, style: const TextStyle(fontSize: 11.5, color: Tokens.textNav, fontWeight: FontWeight.w600)),
      );

  IconData _icon(String kind) => switch (kind) {
        'coder' => Icons.code,
        'computer' => Icons.mouse_outlined,
        'research' => Icons.travel_explore,
        'bot' => Icons.smart_toy_outlined,
        'watched' => Icons.visibility_outlined,
        _ => Icons.memory,
      };
}
