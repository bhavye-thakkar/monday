// The PC's tasks: the ones running now first, then the recent ones, each a card; a tap opens the task with every step. The list
// is what the PC answered plus what its stream changed since; pull to reload asks the PC again.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../core/session.dart';
import '../theme.dart';
import 'task_card.dart';

class TasksPage extends StatelessWidget {
  const TasksPage({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final live = s.liveTasks;
    final recent = s.recentTasks.where((t) => !t.live).toList();
    final t = Theme.of(context).textTheme;
    return RefreshIndicator(
      color: Tokens.orange,
      onRefresh: s.retryNow,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(Tokens.gutter, 12, Tokens.gutter, 24),
        children: [
          if (live.isEmpty && recent.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 60),
              child: Column(children: [
                const Icon(Icons.task_alt_outlined, size: 40, color: Tokens.textOff),
                const SizedBox(height: 12),
                Text('No tasks', style: t.titleMedium),
                const SizedBox(height: 4),
                Text(s.online ? 'The PC has not run a task yet.' : 'The list loads when the PC answers.', style: t.bodySmall),
              ]),
            ),
          if (live.isNotEmpty) ...[
            Text('RUNNING NOW · ${live.length}', style: t.labelSmall),
            const SizedBox(height: 8),
            for (final task in live) Padding(padding: const EdgeInsets.only(bottom: 10), child: TaskCard(task: task, onOpen: () => _open(context, task))),
            const SizedBox(height: 14),
          ],
          if (recent.isNotEmpty) ...[
            Text('RECENT', style: t.labelSmall),
            const SizedBox(height: 8),
            for (final task in recent) Padding(padding: const EdgeInsets.only(bottom: 10), child: TaskCard(task: task, onOpen: () => _open(context, task))),
          ],
        ],
      ),
    );
  }

  void _open(BuildContext context, Task task) {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => TaskScreen(taskId: task.id)));
  }
}

class TaskScreen extends StatefulWidget {
  const TaskScreen({super.key, required this.taskId});
  final String taskId;
  @override
  State<TaskScreen> createState() => _TaskScreenState();
}

class _TaskScreenState extends State<TaskScreen> {
  @override
  void initState() {
    super.initState();
    context.read<Session>().loadTask(widget.taskId); // the full row with every step, not the list's summary
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final task = s.tasks[widget.taskId];
    return Scaffold(
      appBar: AppBar(title: Text(widget.taskId)),
      body: task == null
          ? const Center(child: Text('Not known to the PC', style: TextStyle(color: Tokens.textMuted)))
          : ListView(
              padding: const EdgeInsets.all(Tokens.gutter),
              children: [
                Text(task.prompt, style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 6),
                Text(
                  [
                    if (task.source.isNotEmpty) 'from ${task.source}',
                    if (task.started != null) 'started ${_at(task.started!)}',
                    if (task.ended != null) 'ended ${_at(task.ended!)}',
                  ].join(' · '),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 14),
                TaskCard(task: task),
              ],
            ),
    );
  }

  String _at(double ts) {
    final d = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    return '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}:${d.second.toString().padLeft(2, '0')}';
  }
}
