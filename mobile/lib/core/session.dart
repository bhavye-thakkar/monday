// The phone's live view over one PC: the pairing, the connection state machine, the messages, tasks, approvals and agents as the
// PC reports them, and the event stream that keeps them current. One object, one source of truth for every screen.
//
// Connection words (what the owner sees): unpaired · connecting · connected · reconnecting · disconnected · authentication required.
// A send that finds the PC unreachable stays pending with its client id and is retried on reconnect; the PC treats a repeated
// client id as the same message, so nothing runs twice.
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';

import 'api.dart';
import 'credentials.dart';
import 'models.dart';

enum Connection { unpaired, connecting, connected, reconnecting, disconnected, authRequired }

extension ConnectionWords on Connection {
  String get word => switch (this) {
        Connection.unpaired => 'Not paired',
        Connection.connecting => 'Connecting',
        Connection.connected => 'Connected',
        Connection.reconnecting => 'Reconnecting',
        Connection.disconnected => 'Disconnected',
        Connection.authRequired => 'Authentication required',
      };
  String get key => switch (this) {
        Connection.connected => 'connected',
        Connection.connecting => 'connecting',
        Connection.reconnecting => 'reconnecting',
        Connection.disconnected => 'disconnected',
        Connection.authRequired => 'authentication required',
        Connection.unpaired => '',
      };
}

class PendingSend {
  final String clientId;
  final String text;
  final int tempId;
  String? error;
  PendingSend(this.clientId, this.text, this.tempId);
}

const _backoff = [1, 2, 4, 8, 15];
const _streamSilence = Duration(seconds: 45); // the PC pings every 15 s; three missed pings is a dead stream

class Session extends ChangeNotifier {
  Session({CredentialStore? store}) : _store = store ?? CredentialStore();

  final CredentialStore _store;
  Credentials? creds;
  RemoteApi? _api;

  Connection state = Connection.unpaired;
  String? lastError; // the last connection or PC error, in words
  Hello? hello;
  final List<Message> messages = [];
  final Map<String, Task> tasks = {};
  final List<Approval> approvals = [];
  List<AgentRow> agents = [];
  final List<PendingSend> pending = [];
  int? lastEventId;
  int _tempId = -1;
  int _tries = 0;
  bool _closing = false;
  void Function()? _cancelStream;
  Timer? _retry;
  Timer? _silence;
  final _rng = Random.secure();

  RemoteApi? get api => _api;
  bool get paired => creds != null;
  bool get online => state == Connection.connected;
  List<Task> get liveTasks => tasks.values.where((t) => t.live).toList()..sort((a, b) => (b.started ?? 0).compareTo(a.started ?? 0));
  List<Task> get recentTasks => tasks.values.toList()..sort((a, b) => (b.started ?? 0).compareTo(a.started ?? 0));

  // -- lifecycle ------------------------------------------------------------------------------------------------------
  Future<void> start() async {
    creds = await _store.load();
    if (creds == null) {
      _set(Connection.unpaired);
      return;
    }
    _api = RemoteApi(creds!);
    await connect();
  }

  @override
  void dispose() {
    _closing = true;
    _retry?.cancel();
    _silence?.cancel();
    _cancelStream?.call();
    _api?.close();
    super.dispose();
  }

  void _set(Connection s, [String? error]) {
    state = s;
    if (error != null) lastError = error;
    notifyListeners();
  }

  Future<void> connect() async {
    if (_api == null) return;
    _retry?.cancel();
    _set(_tries == 0 ? Connection.connecting : Connection.reconnecting);
    try {
      hello = await _api!.hello();
      final loaded = await Future.wait([
        _api!.messages(limit: 50),
        _api!.tasks(),
        _api!.approvals(),
        _api!.agents(),
      ]);
      _mergeMessages(loaded[0] as List<Message>);
      for (final t in loaded[1] as List<Task>) {
        tasks[t.id] = t;
      }
      approvals
        ..clear()
        ..addAll(loaded[2] as List<Approval>);
      agents = loaded[3] as List<AgentRow>;
      _tries = 0;
      lastError = null;
      _set(Connection.connected);
      await _openStream();
      await _flushPending();
    } on ApiException catch (e) {
      if (e.unauthorized) {
        _set(Connection.authRequired, e.reason);
        return; // no retry: the credential is revoked or unknown; the owner pairs again
      }
      _scheduleRetry(e.reason);
    } catch (e) {
      _scheduleRetry('$e');
    }
  }

  void _scheduleRetry(String why) {
    if (_closing) return;
    final wait = _backoff[min(_tries, _backoff.length - 1)];
    _tries += 1;
    _set(_tries >= 3 ? Connection.disconnected : Connection.reconnecting, why);
    _retry?.cancel();
    _retry = Timer(Duration(seconds: wait), connect);
  }

  /// Called by the UI when the app comes back to the foreground or the owner taps Retry.
  Future<void> retryNow() async {
    _tries = 0;
    await connect();
  }

  // -- the event stream -----------------------------------------------------------------------------------------------
  Future<void> _openStream() async {
    _cancelStream?.call();
    final s = await _api!.events(lastEventId: lastEventId);
    _cancelStream = s.cancel;
    _armSilence();
    String? type;
    int? id;
    final data = StringBuffer();
    s.lines.listen((line) {
      _armSilence();
      if (line.isEmpty) {
        if (data.isNotEmpty) _onEvent(type, id, data.toString());
        type = null;
        id = null;
        data.clear();
        return;
      }
      if (line.startsWith(':')) return; // a ping
      final colon = line.indexOf(':');
      final field = colon < 0 ? line : line.substring(0, colon);
      var value = colon < 0 ? '' : line.substring(colon + 1);
      if (value.startsWith(' ')) value = value.substring(1);
      switch (field) {
        case 'event':
          type = value;
        case 'id':
          id = int.tryParse(value);
        case 'data':
          if (data.isNotEmpty) data.write('\n');
          data.write(value);
      }
    }, onError: (Object e) => _streamDropped('$e'), onDone: () => _streamDropped('The PC closed the stream.'));
  }

  void _armSilence() {
    _silence?.cancel();
    _silence = Timer(_streamSilence, () => _streamDropped('No sign of the PC for ${_streamSilence.inSeconds} s.'));
  }

  void _streamDropped(String why) {
    if (_closing) return;
    _silence?.cancel();
    _cancelStream?.call();
    _cancelStream = null;
    if (state == Connection.connected) _scheduleRetry(why);
  }

  void _onEvent(String? type, int? id, String raw) {
    Map<String, dynamic> j;
    try {
      final d = jsonDecode(raw);
      j = d is Map ? Map<String, dynamic>.from(d) : {'data': d};
    } catch (_) {
      return;
    }
    if (id != null) lastEventId = id;
    final ev = RemoteEvent.fromJson({...j, if (type != null && j['type'] == null) 'type': type});
    apply(ev);
  }

  /// One event of the PC's bus, relayed: every change comes from here or from a reload, never from a guess.
  void apply(RemoteEvent ev) {
    final d = ev.data;
    switch (ev.type) {
      case 'MESSAGE_CREATED':
        final m = Message.fromJson(d);
        final replaces = (d['replaces'] as num?)?.toInt(); // the streamed placeholder this stored reply stands for
        if (replaces != null) messages.removeWhere((x) => x.id == replaces);
        _upsertMessage(m);
        _settlePending(d['client_id'] as String?, m.id);
      case 'MESSAGE_DELTA':
        final mid = (d['message_id'] as num?)?.toInt();
        final text = d['text'] as String?;
        final delta = d['delta'] as String?;
        if (mid == null) return;
        var m = messages.cast<Message?>().firstWhere((x) => x!.id == mid, orElse: () => null);
        if (m == null) {
          m = Message(id: mid, role: 'assistant', text: '', ts: ev.ts, taskId: ev.taskId, streaming: true);
          _upsertMessage(m);
        }
        if (text != null) {
          m.text = text;
        } else if (delta != null) {
          m.text += delta;
        }
        m.streaming = d['done'] != true;
      case 'TASK_CREATED':
      case 'TASK_STARTED':
      case 'TASK_PROGRESS':
      case 'TASK_STEP':
      case 'TASK_COMPLETED':
      case 'TASK_FAILED':
      case 'TASK_CANCELLED':
      case 'TASK_WAITING':
        if (d['id'] != null) tasks[d['id'].toString()] = Task.fromJson(d);
      case 'PERMISSION_REQUIRED':
        final a = Approval.fromJson(d);
        approvals.removeWhere((x) => x.id == a.id);
        approvals.add(a);
      case 'PERMISSION_GRANTED':
      case 'PERMISSION_DENIED':
      case 'PERMISSION_EXPIRED':
        approvals.removeWhere((x) => x.id == d['id']?.toString());
      case 'AGENTS':
        agents = ((d['agents'] as List?) ?? const []).map((a) => AgentRow.fromJson(Map<String, dynamic>.from(a))).toList();
      case 'CORE_STATE':
        if (hello != null && d['state'] != null) {
          hello = Hello(
            pc: hello!.pc,
            state: d['state'].toString(),
            tasksRunning: (d['tasks_running'] as num?)?.toInt() ?? hello!.tasksRunning,
            canApprove: hello!.canApprove,
            canControl: hello!.canControl,
            serverTime: ev.ts,
          );
        }
      case 'DEVICE_REVOKED':
        _cancelStream?.call();
        _set(Connection.authRequired, 'This phone was revoked on the PC.');
        return;
      default:
        return; // an event the phone does not show
    }
    notifyListeners();
  }

  void _upsertMessage(Message m) {
    final i = messages.indexWhere((x) => x.id == m.id);
    if (i >= 0) {
      messages[i] = m;
    } else {
      messages.add(m);
      messages.sort((a, b) => a.id.compareTo(b.id));
    }
  }

  void _mergeMessages(List<Message> loaded) {
    for (final m in loaded) {
      final i = messages.indexWhere((x) => x.id == m.id);
      if (i >= 0) {
        if (!messages[i].streaming) messages[i] = m;
      } else {
        messages.add(m);
      }
    }
    messages.sort((a, b) => a.id.compareTo(b.id));
  }

  /// Older history, on demand (the thread never caps itself; it loads back in pages).
  Future<bool> loadOlder() async {
    if (_api == null) return false;
    final oldest = messages.where((m) => m.id > 0).map((m) => m.id).fold<int?>(null, (a, b) => a == null ? b : min(a, b));
    if (oldest == null) return false;
    final older = await _api!.messages(before: oldest, limit: 50);
    if (older.isEmpty) return false;
    _mergeMessages(older);
    notifyListeners();
    return true;
  }

  // -- sending --------------------------------------------------------------------------------------------------------
  String _clientId() => List.generate(16, (_) => _rng.nextInt(256).toRadixString(16).padLeft(2, '0')).join();

  Future<void> send(String text) async {
    final t = text.trim();
    if (t.isEmpty || _api == null) return;
    final p = PendingSend(_clientId(), t, _tempId--);
    pending.add(p);
    messages.add(Message(id: p.tempId, role: 'user', text: t, ts: DateTime.now().millisecondsSinceEpoch / 1000, source: 'phone'));
    notifyListeners();
    await _deliver(p);
  }

  Future<void> _deliver(PendingSend p) async {
    try {
      final r = await _api!.command(p.text, p.clientId);
      _settlePending(p.clientId, r.messageId);
    } on ApiException catch (e) {
      p.error = e.unreachable ? 'Not sent: the PC is unreachable. It will be sent when the PC is back.' : 'The PC refused it: ${e.reason}';
      if (!e.unreachable) pending.remove(p); // a refusal is final; the owner can send again
      notifyListeners();
      if (e.unauthorized) _set(Connection.authRequired, e.reason);
    }
  }

  void _settlePending(String? clientId, int messageId) {
    if (clientId == null) return;
    final i = pending.indexWhere((p) => p.clientId == clientId);
    if (i < 0) return;
    final p = pending.removeAt(i);
    final mi = messages.indexWhere((m) => m.id == p.tempId);
    if (mi >= 0) {
      final old = messages[mi];
      if (messageId > 0 && messages.any((m) => m.id == messageId)) {
        messages.removeAt(mi); // the PC's own copy already arrived over the stream
      } else {
        messages[mi] = Message(id: messageId > 0 ? messageId : old.id, role: 'user', text: old.text, ts: old.ts, source: 'phone');
        messages.sort((a, b) => a.id.compareTo(b.id));
      }
    }
    notifyListeners();
  }

  Future<void> _flushPending() async {
    for (final p in List.of(pending)) {
      await _deliver(p);
    }
  }

  String? pendingError(int messageId) => pending.cast<PendingSend?>().firstWhere((p) => p!.tempId == messageId, orElse: () => null)?.error;

  // -- actions on the PC ----------------------------------------------------------------------------------------------
  Future<String?> stop(String taskId) => _guard(() => _api!.stop(taskId));

  Future<String?> decide(Approval a, bool allow) async {
    final err = await _guard(() => _api!.decide(a.id, allow));
    if (err == null) approvals.removeWhere((x) => x.id == a.id);
    notifyListeners();
    return err;
  }

  Future<void> refreshAgents() async {
    if (_api == null) return;
    try {
      agents = await _api!.agents();
      notifyListeners();
    } on ApiException {
      // the stream will say; the last list stays
    }
  }

  Future<Task?> loadTask(String id) async {
    if (_api == null) return null;
    try {
      final t = await _api!.task(id);
      tasks[t.id] = t;
      notifyListeners();
      return t;
    } on ApiException {
      return tasks[id];
    }
  }

  Future<String?> rotate() async {
    if (_api == null || creds == null) return 'Not paired.';
    try {
      final s = await _api!.rotate();
      creds = creds!.withSecret(s);
      await _store.save(creds!);
      _api!.creds = creds!;
      notifyListeners();
      return null;
    } on ApiException catch (e) {
      return e.reason;
    }
  }

  Future<String?> _guard(Future<void> Function() f) async {
    if (_api == null) return 'Not paired.';
    try {
      await f();
      return null;
    } on ApiException catch (e) {
      if (e.unauthorized) _set(Connection.authRequired, e.reason);
      return e.reason;
    }
  }

  // -- pairing --------------------------------------------------------------------------------------------------------
  Future<String?> pair({required String address, required String code, required String deviceName}) async {
    try {
      final p = await PairingClient.pair(address: address, code: code, deviceName: deviceName);
      // The address the owner typed is the one that demonstrably reached the PC (on an emulator it is the host's loopback alias,
      // which the PC cannot know), so it stays the LAN address; the PC's public address (ngrok) is the fallback.
      creds = Credentials.fromPairing(p).withAddresses(lan: address.trim());
      await _store.save(creds!);
      _api?.close();
      _api = RemoteApi(creds!);
      _tries = 0;
      await connect();
      return null;
    } on ApiException catch (e) {
      return e.reason;
    }
  }

  /// Forget this PC: tell it (so the credential dies there too), then clear the secure storage either way.
  Future<void> forget() async {
    try {
      await _api?.forget();
    } catch (_) {}
    _cancelStream?.call();
    _retry?.cancel();
    _silence?.cancel();
    _api?.close();
    _api = null;
    creds = null;
    hello = null;
    messages.clear();
    tasks.clear();
    approvals.clear();
    agents = [];
    pending.clear();
    lastEventId = null;
    await _store.clear();
    _set(Connection.unpaired);
  }
}
