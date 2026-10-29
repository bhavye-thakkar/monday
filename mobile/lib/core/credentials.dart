// The pairing with one PC, kept in the platform's secure storage (Android Keystore / iOS Keychain): the device id, the secret,
// the PC's identity, its addresses and the pinned certificate fingerprint. Nothing of this goes in plain preferences or logs.
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'models.dart';

class Credentials {
  final String deviceId;
  final String secret;
  final PcInfo pc;
  final String fingerprint;
  final String? lanUrl;
  final String? publicUrl;
  const Credentials({
    required this.deviceId,
    required this.secret,
    required this.pc,
    required this.fingerprint,
    this.lanUrl,
    this.publicUrl,
  });

  factory Credentials.fromPairing(Pairing p) => Credentials(
        deviceId: p.deviceId,
        secret: p.secret,
        pc: p.pc,
        fingerprint: p.fingerprint,
        lanUrl: p.lanUrl,
        publicUrl: p.publicUrl,
      );

  Credentials withSecret(String s) =>
      Credentials(deviceId: deviceId, secret: s, pc: pc, fingerprint: fingerprint, lanUrl: lanUrl, publicUrl: publicUrl);

  Credentials withAddresses({String? lan, String? public}) => Credentials(
      deviceId: deviceId, secret: secret, pc: pc, fingerprint: fingerprint, lanUrl: lan ?? lanUrl, publicUrl: public ?? publicUrl);

  /// The addresses to try, in order: the LAN first (fast, no third party), then the public one through ngrok.
  List<String> get addresses => [if (lanUrl != null && lanUrl!.isNotEmpty) lanUrl!, if (publicUrl != null && publicUrl!.isNotEmpty) publicUrl!];

  String get bearer => '$deviceId.$secret';
}

class CredentialStore {
  CredentialStore([FlutterSecureStorage? storage])
      : _s = storage ??
            const FlutterSecureStorage(
              // Android: the plugin's own ciphers with a Keystore key (v10 default; its encryptedSharedPreferences flag is deprecated)
              iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
            );
  final FlutterSecureStorage _s;

  static const _keys = ('device_id', 'secret', 'pc_id', 'pc_name', 'pc_version', 'fingerprint', 'lan_url', 'public_url');

  Future<Credentials?> load() async {
    final all = await _s.readAll();
    final id = all[_keys.$1], secret = all[_keys.$2];
    if (id == null || id.isEmpty || secret == null || secret.isEmpty) return null;
    return Credentials(
      deviceId: id,
      secret: secret,
      pc: PcInfo(id: all[_keys.$3] ?? '', name: all[_keys.$4] ?? '', version: all[_keys.$5] ?? ''),
      fingerprint: all[_keys.$6] ?? '',
      lanUrl: all[_keys.$7],
      publicUrl: all[_keys.$8],
    );
  }

  Future<void> save(Credentials c) async {
    await _s.write(key: _keys.$1, value: c.deviceId);
    await _s.write(key: _keys.$2, value: c.secret);
    await _s.write(key: _keys.$3, value: c.pc.id);
    await _s.write(key: _keys.$4, value: c.pc.name);
    await _s.write(key: _keys.$5, value: c.pc.version);
    await _s.write(key: _keys.$6, value: c.fingerprint);
    await _s.write(key: _keys.$7, value: c.lanUrl ?? '');
    await _s.write(key: _keys.$8, value: c.publicUrl ?? '');
  }

  Future<void> clear() => _s.deleteAll();
}
