"""
Encrypted secret vault for the decrypt-viewer.

Design goal: pulling the USB stick must be worthless. Therefore the stick never
stores FEKs or the Tesla token in clear text, and never stores the master key in
clear text.

- A random 32-byte master key (MK) encrypts `vault.enc` (AES-256-GCM). The
  vault payload is JSON: { "keys": {clip_id: b64-FEK}, "token": {...} }.
- MK itself is wrapped with a key derived from the user passphrase
  (scrypt) and stored as `vault.wrap`. => passphrase unlock, offline.
- Optionally MK is ALSO placed on the NAS (`vault.mk`, handled by server.py) so
  that, when the NAS is reachable, the viewer can auto-unlock without a
  passphrase. MK is never written to the stick in the clear.

MK and the decrypted payload live ONLY in this process's RAM while unlocked.

File layouts (all binary, versioned magic):
  vault.wrap : b"TVW1" | salt(16) | nonce(12) | tag(16) | ct(=MK, 32)
  vault.enc  : b"TVE1" | nonce(12) | tag(16) | ct(payload json)
  sidecar    : b"TVK1" | nonce(12) | tag(16) | ct(plaintext)   (per-clip key file)
"""
import os, json, threading, base64
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt
from Crypto.Random import get_random_bytes

_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_MK_LEN = 32

_lock = threading.RLock()


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, state_dir: str):
        self.dir = state_dir
        self.enc_path = os.path.join(state_dir, "vault.enc")
        self.wrap_path = os.path.join(state_dir, "vault.wrap")
        self._mk = None      # bytes | None
        self._data = None    # dict | None  {"keys":{}, "token":{}}

    # ---- crypto helpers ----------------------------------------------------
    @staticmethod
    def _gcm_enc(key: bytes, plaintext: bytes) -> bytes:
        nonce = get_random_bytes(12)
        c = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ct, tag = c.encrypt_and_digest(plaintext)
        return nonce + tag + ct

    @staticmethod
    def _gcm_dec(key: bytes, blob: bytes) -> bytes:
        nonce, tag, ct = blob[:12], blob[12:28], blob[28:]
        c = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return c.decrypt_and_verify(ct, tag)   # raises ValueError on wrong key

    def _kek(self, passphrase: str, salt: bytes) -> bytes:
        return scrypt(passphrase.encode("utf-8"), salt, _MK_LEN,
                      N=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)

    # ---- state -------------------------------------------------------------
    def has_vault(self) -> bool:
        return os.path.exists(self.wrap_path) and os.path.exists(self.enc_path)

    def is_unlocked(self) -> bool:
        return self._mk is not None and self._data is not None

    def lock(self):
        with _lock:
            self._mk = None
            self._data = None

    def factory_reset(self):
        """Forgot-password recovery: irreversibly delete the vault (wrapped
        master key + encrypted payload). There is no way to recover the old
        contents without the passphrase anyway -- this just removes the
        now-permanently-inaccessible files so /api/setup can create a fresh
        vault. Already-archived encrypted clips on the NAS are untouched;
        their keys would need to be re-fetched from Tesla after reset."""
        with _lock:
            self._mk = None
            self._data = None
            for p in (self.wrap_path, self.enc_path):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass

    def _write_enc(self):
        os.makedirs(self.dir, exist_ok=True)
        payload = json.dumps(self._data, separators=(",", ":")).encode("utf-8")
        blob = b"TVE1" + self._gcm_enc(self._mk, payload)
        tmp = self.enc_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.enc_path)

    def _write_wrap(self, passphrase: str):
        os.makedirs(self.dir, exist_ok=True)
        salt = get_random_bytes(16)
        kek = self._kek(passphrase, salt)
        blob = b"TVW1" + salt + self._gcm_enc(kek, self._mk)
        tmp = self.wrap_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.wrap_path)

    # ---- lifecycle ---------------------------------------------------------
    def create(self, passphrase: str, import_keys: dict = None, import_token: dict = None):
        if not passphrase:
            raise VaultError("empty passphrase")
        with _lock:
            self._mk = get_random_bytes(_MK_LEN)
            self._data = {"keys": dict(import_keys or {}), "token": dict(import_token or {})}
            self._write_enc()
            self._write_wrap(passphrase)

    def unlock_with_pass(self, passphrase: str) -> bool:
        with _lock:
            try:
                with open(self.wrap_path, "rb") as f:
                    w = f.read()
                if w[:4] != b"TVW1":
                    raise VaultError("bad wrap magic")
                salt = w[4:20]
                kek = self._kek(passphrase, salt)
                mk = self._gcm_dec(kek, w[20:])
            except ValueError:
                return False   # wrong passphrase (GCM verify failed)
            return self._load_with_mk(mk)

    def unlock_with_mk(self, mk: bytes) -> bool:
        with _lock:
            return self._load_with_mk(mk)

    def _load_with_mk(self, mk: bytes) -> bool:
        try:
            with open(self.enc_path, "rb") as f:
                e = f.read()
            if e[:4] != b"TVE1":
                raise VaultError("bad enc magic")
            data = json.loads(self._gcm_dec(mk, e[4:]).decode("utf-8"))
        except ValueError:
            return False
        self._mk = mk
        self._data = data if isinstance(data, dict) else {"keys": {}, "token": {}}
        self._data.setdefault("keys", {})
        self._data.setdefault("token", {})
        return True

    def change_pass(self, old: str, new: str) -> bool:
        with _lock:
            if not self.is_unlocked():
                if not self.unlock_with_pass(old):
                    return False
            if not new:
                raise VaultError("empty new passphrase")
            self._write_wrap(new)
            return True

    def save(self):
        with _lock:
            if not self.is_unlocked():
                raise VaultError("locked")
            self._write_enc()

    # ---- accessors (require unlocked) --------------------------------------
    def _require(self):
        if not self.is_unlocked():
            raise VaultError("locked")

    def get_mk(self) -> bytes:
        self._require()
        return self._mk

    def keys(self) -> dict:
        self._require()
        return dict(self._data["keys"])

    def merge_keys(self, new_keys: dict) -> int:
        """Add new FEKs (never overwrite/delete existing). Returns count added."""
        with _lock:
            self._require()
            n = 0
            for cid, key in (new_keys or {}).items():
                if cid and key and cid not in self._data["keys"]:
                    self._data["keys"][cid] = key
                    n += 1
            if n:
                self._write_enc()
            return n

    def get_token(self) -> dict:
        self._require()
        return dict(self._data.get("token") or {})

    def set_token(self, tok: dict):
        with _lock:
            self._require()
            self._data["token"] = dict(tok or {})
            self._write_enc()

    # ---- per-clip key sidecar (encrypted with MK) --------------------------
    def seal(self, plaintext: bytes) -> bytes:
        """Encrypt arbitrary bytes with the MK (for the NAS key sidecars)."""
        self._require()
        return b"TVK1" + self._gcm_enc(self._mk, plaintext)

    def unseal(self, blob: bytes) -> bytes:
        self._require()
        if blob[:4] != b"TVK1":
            raise VaultError("bad sidecar magic")
        return self._gcm_dec(self._mk, blob[4:])
