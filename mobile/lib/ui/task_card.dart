// One task as the PC reports it: its state word, who runs it, its steps (✓ ● ✗ as the window draws them), its result or error,
// a real count of steps when the PC counted them (never a percentage it did not give), and Stop while it runs.
import 'package:flutter/material.dart' hide Step; // Step here is the PC's plan step (core/models.dart), not Material's stepper's
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../core/session.dart';
import '../theme.dart';

class TaskCard extends StatelessWidget {
  const TaskCard({super.key, required this.task, this.compact = false, this.onOpen});
  final Task task;
  final bool compact;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final c = stateColor(task.status);
    final steps = compact ? task.steps.where((s) => s.status != 'pending').toList() : task.steps;
    final shown = compact && steps.length > 3 ? steps.sublist(steps.length - 3) : steps;
    return Card(
      child: InkWell(
        onTap: onOpen,
        borderRadius: BorderRadius.circular(Tokens.radiusCard),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(width: 8, height: 8, decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
                  const SizedBox(width: 8),
                  Text(_word(task.status), style: TextStyle(color: c, fontWeight: FontWeight.w700, fontSize: 12.5, letterSpacing: 0.4)),
                  const SizedBox(width: 8),
                  Expanded(child: Text(task.id, style: t.bodySmall, overflow: TextOverflow.ellipsis)),
                  if (task.agent.isNotEmpty) Text(task.agent, style: t.bodySmall),
                ],
              ),
              if (!compact) ...[
                const SizedBox(height: 8),
                Text(task.prompt, style: t.bodyMedium, maxLines: 3, overflow: TextOverflow.ellipsis),
              ],
              if (task.progressTotal != null && task.progressTotal! > 0) ...[
                const SizedBox(height: 10),
                ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(value: (task.progressDone ?? 0) / task.progressTotal!, minHeight: 4),
                ),
                const SizedBox(height: 4),
                Text('${task.progressDone ?? 0} of ${task.progressTotal} steps', style: t.bodySmall),
              ] else if (task.live) ...[
                const SizedBox(height: 8),
                Text('Working…', style: t.bodySmall?.copyWith(color: Tokens.orange)),
              ],
              if (shown.isNotEmpty) ...[
                const SizedBox(height: 10),
                for (final s in shown) _StepLine(step: s),
              ],
              if (task.result != null && task.result!.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(task.result!, style: t.bodyMedium, maxLines: compact ? 4 : null, overflow: compact ? TextOverflow.ellipsis : null),
              ],
              if (task.error != null && task.error!.isNotEmpty) ...[
                const SizedBox(height: 10),
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(color: Tokens.redBg, borderRadius: BorderRadius.circular(Tokens.radiusField)),
                  child: Text(task.error!, style: const TextStyle(color: Tokens.redText, fontSize: 13)),
                ),
              ],
              if (task.live) ...[
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerRight,
                  child: OutlinedButton.icon(
                    onPressed: () => _stop(context),
                    style: OutlinedButton.styleFrom(foregroundColor: Tokens.redText, minimumSize: const Size(44, 36)),
                    icon: const Icon(Icons.stop_rounded, size: 18),
                    label: const Text('Stop'),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _stop(BuildContext context) async {
    final err = await context.read<Session>().stop(task.id);
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(err == null ? 'Stop sent to the PC' : 'Not stopped: $err')));
    }
  }

  static String _word(String status) => switch (status) {
        'queued' => 'QUEUED',
        'running' => 'RUNNING',
        'waiting' => 'WAITING',
        'done' || 'completed' => 'DONE',
        'failed' => 'FAILED',
        'cancelled' => 'STOPPED',
        '' => 'N/A',
        _ => status.toUpperCase(),
      };
}

class _StepLine extends StatelessWidget {
  const _StepLine({required this.step});
  final Step step;
  @override
  Widget build(BuildContext context) {
    final (glyph, color) = switch (step.status) {
      'done' => (step.verified == 'false' ? '!' : '✓', step.verified == 'false' ? Tokens.amber : Tokens.greenText),
      'running' => ('●', Tokens.orange),
      'failed' => ('✕', Tokens.redText),
      _ => ('○', Tokens.textOff),
    };
    final words = [step.tool, step.action, step.target].where((w) => w.isNotEmpty).join(' › ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 16, child: Text(glyph, style: TextStyle(color: color, fontWeight: FontWeight.w700))),
          Expanded(child: Text(words.isEmpty ? 'step ${step.n}' : words, style: const TextStyle(fontSize: 13, color: Tokens.textNav))),
          if (step.took != null) Text('${step.took!.toStringAsFixed(1)} s', style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}
