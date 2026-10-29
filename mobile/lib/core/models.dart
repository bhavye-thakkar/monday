// The shapes the PC's remote API answers with (core/remote.py). Every field is what Core reported; nothing here invents a value:
// a missing progress is null, not 0 %, and a missing state is the empty word.

String _s(Object? v) => v == null ? '' : v.toString();
int? _i(Object? v) => v is int ? v : (v is num ? v.toInt() : int.tryParse(_s(v)));
double? _d(Object? v) => v is num ? v.toDouble() : double.tryParse(_s(v));
bool _b(Object? v) => v == true || v == 1 || v == 'true';
Map<String, dynamic> _m(Object? v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<Map<String, dynamic>> _ml(Object? v) => v is List ? v.map(_m).toList() : const [];

class PcInfo {
  final String id;
  final String name;
  final String version;
  const PcInfo({required this.id, required this.name, required this.version});
  factory PcInfo.fromJson(Map<String, dynamic> j) =>
      PcInfo(id: _s(j['id']), name: _s(j['name']), version: _s(j['version']));
}

/// What `GET /remote/hello` says: reachability, Core's state word, how many tasks run, what this device may do.
class Hello {
  final PcInfo pc;
  final String state; // Core's own state word: IDLE, PLANNING, THINKING, EXECUTING, WAITING_FOR_PERMISSION
  final int tasksRunning;
  final bool canApprove;
  final bool canControl;
  final double serverTime;
  const Hello({
    required this.pc,
    required this.state,
    required this.tasksRunning,
    required this.canApprove,
    required this.canControl,
    required this.serverTime,
  });
  factory Hello.fromJson(Map<String, dynamic> j) {
    final caps = _m(j['capabilities']);
    return Hello(
      pc: PcInfo.fromJson(_m(j['pc'])),
      state: _s(j['state']),
      tasksRunning: _i(j['tasks_running']) ?? 0,
      canApprove: _b(caps['approve']),
      canControl: _b(caps['control']),
      serverTime: _d(j['time']) ?? 0,
    );
  }
}

class Message {
  final int id;
  final String role; // user | assistant | system
  String text;
  final double ts;
  final String? taskId;
  final String source; // window, phone:<name>, voice…
  final String? model;
  bool streaming;
  Message({
    required this.id,
    required this.role,
    required this.text,
    required this.ts,
    this.taskId,
    this.source = '',
    this.model,
    this.streaming = false,
  });
  factory Message.fromJson(Map<String, dynamic> j) => Message(
        id: _i(j['id']) ?? 0,
        role: _s(j['role']),
        text: _s(j['text']),
        ts: _d(j['ts']) ?? 0,
        taskId: j['task_id'] == null ? null : _s(j['task_id']),
        source: _s(j['source']),
        model: j['model'] == null ? null : _s(j['model']),
      );
}

class Step {
  final int n;
  final String tool;
  final String action;
  final String target;
  final String status; // running | done | failed | pending
  final String? verified; // true | false | unknown | null
  final double? took;
  const Step({
    required this.n,
    required this.tool,
    required this.action,
    required this.target,
    required this.status,
    this.verified,
    this.took,
  });
  factory Step.fromJson(Map<String, dynamic> j) => Step(
        n: _i(j['n']) ?? 0,
        tool: _s(j['tool']),
        action: _s(j['action']),
        target: _s(j['target']),
        status: _s(j['status']),
        verified: j['verified'] == null ? null : _s(j['verified']),
        took: _d(j['took']),
      );
}

class Task {
  final String id;
  final String prompt;
  String status; // queued | running | waiting | done | failed | cancelled
  final String agent;
  final double? started;
  double? ended;
  String? result;
  String? error;
  List<Step> steps;
  int? progressDone; // only when the PC counted real steps
  int? progressTotal;
  final String source;
  Task({
    required this.id,
    required this.prompt,
    required this.status,
    required this.agent,
    this.started,
    this.ended,
    this.result,
    this.error,
    this.steps = const [],
    this.progressDone,
    this.progressTotal,
    this.source = '',
  });
  factory Task.fromJson(Map<String, dynamic> j) {
    final p = _m(j['progress']);
    return Task(
      id: _s(j['id']),
      prompt: _s(j['prompt']),
      status: _s(j['status']),
      agent: _s(j['agent']),
      started: _d(j['started']),
      ended: _d(j['ended']),
      result: j['result'] == null ? null : _s(j['result']),
      error: j['error'] == null ? null : _s(j['error']),
      steps: _ml(j['steps']).map(Step.fromJson).toList(),
      progressDone: _i(p['done']),
      progressTotal: _i(p['total']),
      source: _s(j['source']),
    );
  }
  bool get live => status == 'queued' || status == 'running' || status == 'waiting';
}

/// A permission request waiting for the owner (router.permit asked): one decision, allow or deny, nothing in between.
class Approval {
  final String id;
  final String? taskId;
  final String what;
  final String risk;
  final double askedAt;
  final String? agent;
  const Approval({required this.id, this.taskId, required this.what, required this.risk, required this.askedAt, this.agent});
  factory Approval.fromJson(Map<String, dynamic> j) => Approval(
        id: _s(j['id']),
        taskId: j['task_id'] == null ? null : _s(j['task_id']),
        what: _s(j['what']),
        risk: _s(j['risk']),
        askedAt: _d(j['asked_at']) ?? 0,
        agent: j['agent'] == null ? null : _s(j['agent']),
      );
}

/// One row of the island's agent list: who is working on what, as Core reports it (never a made-up percentage).
class AgentRow {
  final String id;
  final String name;
  final String kind; // bot | coder | computer | research | watched
  final String? taskId;
  final String state; // idle | working | waiting | done | failed
  final String step;
  final String tool;
  final bool waitingForApproval;
  final int? progressDone;
  final int? progressTotal;
  final String? result;
  final String? error;
  const AgentRow({
    required this.id,
    required this.name,
    required this.kind,
    this.taskId,
    required this.state,
    required this.step,
    required this.tool,
    required this.waitingForApproval,
    this.progressDone,
    this.progressTotal,
    this.result,
    this.error,
  });
  factory AgentRow.fromJson(Map<String, dynamic> j) {
    final p = _m(j['progress']);
    return AgentRow(
      id: _s(j['id']),
      name: _s(j['name']),
      kind: _s(j['kind']),
      taskId: j['task_id'] == null ? null : _s(j['task_id']),
      state: _s(j['state']),
      step: _s(j['step']),
      tool: _s(j['tool']),
      waitingForApproval: _b(j['waiting']),
      progressDone: _i(p['done']),
      progressTotal: _i(p['total']),
      result: j['result'] == null ? null : _s(j['result']),
      error: j['error'] == null ? null : _s(j['error']),
    );
  }
}

/// One event of Core's bus as the remote stream relays it (`GET /remote/events`).
class RemoteEvent {
  final int? id;
  final String type;
  final double ts;
  final String? taskId;
  final Map<String, dynamic> data;
  const RemoteEvent({this.id, required this.type, required this.ts, this.taskId, required this.data});
  factory RemoteEvent.fromJson(Map<String, dynamic> j) => RemoteEvent(
        id: _i(j['id']),
        type: _s(j['type']),
        ts: _d(j['ts']) ?? 0,
        taskId: j['task_id'] == null ? null : _s(j['task_id']),
        data: _m(j['data']),
      );
}

/// What pairing gives the phone, once (`POST /remote/pair`). The secret is written to secure storage and never shown again.
class Pairing {
  final String deviceId;
  final String secret;
  final PcInfo pc;
  final String fingerprint; // SHA-256 of the PC's self-signed certificate, hex
  final String? lanUrl;
  final String? publicUrl;
  const Pairing({
    required this.deviceId,
    required this.secret,
    required this.pc,
    required this.fingerprint,
    this.lanUrl,
    this.publicUrl,
  });
  factory Pairing.fromJson(Map<String, dynamic> j) {
    final a = _m(j['addresses']);
    return Pairing(
      deviceId: _s(j['device_id']),
      secret: _s(j['secret']),
      pc: PcInfo.fromJson(_m(j['pc'])),
      fingerprint: _s(j['fingerprint']).toLowerCase().replaceAll(':', ''),
      lanUrl: a['lan'] == null ? null : _s(a['lan']),
      publicUrl: a['public'] == null ? null : _s(a['public']),
    );
  }
}
