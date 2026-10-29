// The one client of the PC's remote API (core/remote.py): HTTPS pinned to the certificate learned at pairing on the LAN, ngrok's
// own certificate on the public address, the device credential on every call, the LAN address tried before the public one.
// Every failure is typed so the UI can say what happened in words: unreachable, refused, revoked, rate-limited, a PC error.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import 'credentials.dart';
import 'models.dart';

const kRequestTimeout = Duration(seconds: 12);
const kAppVersion = '0.1.0';

class ApiException implements Exception {
  final int status; // 0 = no connection
  final String reason; // a short word or sentence from the PC, or ours
  const ApiException(this.status, this.reason);
  bool get unreachable => status == 0;
  bool get unauthorized => status == 401;
  @override
  String toString() => status == 0 ? reason : '$status: $reason';
}

/// A client without a pairing yet: only pairing itself. It accepts the PC's self-signed certificate once (the pairing code is the
/// protection for that first use) and returns the fingerprint it saw, which the app then pins.
class PairingClient {
  static Future<Pairing> pair({required String address, required String code, required String deviceName}) async {
    String? seen;
    final io = HttpClient()
      ..connectionTimeout = kRequestTimeout
      ..badCertificateCallback = (cert, host, port) {
        seen = _fingerprint(cert);
        return true;
      };
    final client = IOClient(io);
    try {
      final r = await client
          .post(Uri.parse('${_clean(address)}/remote/pair'),
              headers: {'content-type': 'application/json'},
              body: jsonEncode({
                'code': code.trim(),
                'name': deviceName,
                'platform': Platform.operatingSystem,
                'app_version': kAppVersion,
              }))
          .timeout(kRequestTimeout);
      final j = _decode(r);
      final p = Pairing.fromJson(j);
      // The PC states its fingerprint in the answer; what we saw on the wire must agree, else someone sat in between.
      if (seen != null && p.fingerprint.isNotEmpty && seen != p.fingerprint) {
        throw const ApiException(0, 'The certificate on the wire is not the one the PC named. Not pairing.');
      }
      return p;
    } on SocketException catch (e) {
      throw ApiException(0, 'The PC is unreachable at $address (${e.osError?.message ?? e.message}).');
    } on TimeoutException {
      throw ApiException(0, 'No answer from $address within ${kRequestTimeout.inSeconds} s.');
    } on HandshakeException catch (e) {
      throw ApiException(0, 'TLS failed: ${e.message}');
    } finally {
      client.close();
    }
  }
}

class RemoteApi {
  RemoteApi(this.creds) {
    final io = HttpClient()
      ..connectionTimeout = kRequestTimeout
      ..badCertificateCallback = (cert, host, port) => _fingerprint(cert) == creds.fingerprint;
    _client = IOClient(io);
  }

  Credentials creds;
  late final IOClient _client;
  String? _good; // the address that answered last

  void close() => _client.close();

  Map<String, String> get _headers => {'authorization': 'Bearer ${creds.bearer}', 'content-type': 'application/json'};

  /// Try the LAN address, then the public one. Only connection failures move on to the next address; an answer, any status,
  /// is the PC's and is returned as it is.
  Future<Map<String, dynamic>> _call(String method, String path, {Object? body}) async {
    final order = [if (_good != null) _good!, ...creds.addresses.where((a) => a != _good)];
    if (order.isEmpty) throw const ApiException(0, 'No address for this PC. Pair again.');
    ApiException? last;
    for (final base in order) {
      try {
        final uri = Uri.parse('${_clean(base)}$path');
        final req = http.Request(method, uri)..headers.addAll(_headers);
        if (body != null) req.body = jsonEncode(body);
        final r = await http.Response.fromStream(await _client.send(req).timeout(kRequestTimeout));
        _good = base;
        return _decode(r);
      } on SocketException catch (e) {
        last = ApiException(0, 'Unreachable at $base (${e.osError?.message ?? e.message})');
      } on TimeoutException {
        last = ApiException(0, 'No answer from $base within ${kRequestTimeout.inSeconds} s');
      } on HandshakeException catch (e) {
        last = ApiException(0, 'TLS refused at $base: ${e.message}');
      } on http.ClientException catch (e) {
        last = ApiException(0, 'Connection failed at $base: ${e.message}');
      }
    }
    throw last ?? const ApiException(0, 'Unreachable');
  }

  Future<Hello> hello() async => Hello.fromJson(await _call('GET', '/remote/hello'));

  /// A prompt for the PC. `clientId` makes a retried send idempotent: the PC answers the same message id, `duplicate: true`.
  Future<({int messageId, String? taskId, bool duplicate})> command(String text, String clientId) async {
    final j = await _call('POST', '/remote/command', body: {'text': text, 'client_id': clientId});
    return (messageId: (j['message_id'] as num?)?.toInt() ?? 0, taskId: j['task_id'] as String?, duplicate: j['duplicate'] == true);
  }

  /// The thread: the newest page, or the page after `after`, or the page before `before` (older history).
  Future<List<Message>> messages({int? after, int? before, int limit = 50}) async {
    final q = [
      'limit=$limit',
      if (after != null) 'after=$after',
      if (before != null) 'before=$before',
    ].join('&');
    final j = await _call('GET', '/remote/messages?$q');
    return (j['messages'] as List? ?? const []).map((m) => Message.fromJson(Map<String, dynamic>.from(m))).toList();
  }

  Future<List<Task>> tasks({int limit = 30}) async {
    final j = await _call('GET', '/remote/tasks?limit=$limit');
    return (j['tasks'] as List? ?? const []).map((t) => Task.fromJson(Map<String, dynamic>.from(t))).toList();
  }

  Future<Task> task(String id) async => Task.fromJson(await _call('GET', '/remote/tasks/$id'));

  Future<void> stop(String id) => _call('POST', '/remote/tasks/$id/stop');

  Future<List<Approval>> approvals() async {
    final j = await _call('GET', '/remote/approvals');
    return (j['pending'] as List? ?? const []).map((a) => Approval.fromJson(Map<String, dynamic>.from(a))).toList();
  }

  Future<void> decide(String approvalId, bool allow) =>
      _call('POST', '/remote/approvals/$approvalId/${allow ? 'allow' : 'deny'}');

  Future<List<AgentRow>> agents() async {
    final j = await _call('GET', '/remote/agents');
    return (j['agents'] as List? ?? const []).map((a) => AgentRow.fromJson(Map<String, dynamic>.from(a))).toList();
  }

  /// A new secret from the PC; the old one stays good for a short while on the PC's side, so a lost answer strands nobody.
  Future<String> rotate() async {
    final j = await _call('POST', '/remote/rotate');
    final s = j['secret'] as String?;
    if (s == null || s.isEmpty) throw const ApiException(500, 'The PC answered rotation without a secret.');
    return s;
  }

  Future<void> forget() => _call('DELETE', '/remote/devices/me');

  /// The live stream: one long request whose body is server-sent events. The caller owns the subscription; `lastEventId`
  /// asks the PC to replay what was missed while the phone was away.
  Future<({Stream<String> lines, void Function() cancel})> events({int? lastEventId}) async {
    final order = [if (_good != null) _good!, ...creds.addresses.where((a) => a != _good)];
    ApiException? last;
    for (final base in order) {
      try {
        final req = http.Request('GET', Uri.parse('${_clean(base)}/remote/events'))
          ..headers.addAll({..._headers, 'accept': 'text/event-stream', if (lastEventId != null) 'last-event-id': '$lastEventId'});
        final r = await _client.send(req).timeout(kRequestTimeout);
        if (r.statusCode != 200) {
          final body = await r.stream.bytesToString();
          throw ApiException(r.statusCode, _reason(r.statusCode, body));
        }
        _good = base;
        final controller = StreamController<String>();
        final sub = r.stream.transform(utf8.decoder).transform(const LineSplitter()).listen(
              controller.add,
              onError: controller.addError,
              onDone: controller.close,
              cancelOnError: true,
            );
        return (lines: controller.stream, cancel: () => sub.cancel());
      } on SocketException catch (e) {
        last = ApiException(0, 'Unreachable at $base (${e.osError?.message ?? e.message})');
      } on TimeoutException {
        last = ApiException(0, 'No answer from $base within ${kRequestTimeout.inSeconds} s');
      } on HandshakeException catch (e) {
        last = ApiException(0, 'TLS refused at $base: ${e.message}');
      } on http.ClientException catch (e) {
        last = ApiException(0, 'Connection failed at $base: ${e.message}');
      }
    }
    throw last ?? const ApiException(0, 'Unreachable');
  }
}

String _clean(String address) {
  var a = address.trim();
  if (!a.startsWith('http://') && !a.startsWith('https://')) a = 'https://$a';
  while (a.endsWith('/')) {
    a = a.substring(0, a.length - 1);
  }
  return a;
}

String _fingerprint(X509Certificate cert) => sha256.convert(cert.der).toString();

Map<String, dynamic> _decode(http.Response r) {
  if (r.statusCode >= 200 && r.statusCode < 300) {
    if (r.body.isEmpty) return const {};
    final j = jsonDecode(r.body);
    return j is Map ? Map<String, dynamic>.from(j) : {'value': j};
  }
  throw ApiException(r.statusCode, _reason(r.statusCode, r.body));
}

String _reason(int status, String body) {
  try {
    final j = jsonDecode(body);
    if (j is Map && j['detail'] != null) return j['detail'].toString();
    if (j is Map && j['reason'] != null) return j['reason'].toString();
  } catch (_) {}
  switch (status) {
    case 401:
      return 'Not authorized';
    case 403:
      return 'Refused';
    case 404:
      return 'Not found';
    case 409:
      return 'Conflict';
    case 429:
      return 'Too many requests';
    default:
      return body.isEmpty ? 'HTTP $status' : body.substring(0, body.length.clamp(0, 200));
  }
}
