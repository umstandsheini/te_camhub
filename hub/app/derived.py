"""
Encrypted, persistent copies of what the Viewer derives from a decrypted
clip -- thumbnails and front-camera telemetry -- so they survive a vault
lock or a reboot.

The RAM cache (/dev/shm, see viewer.py) is wiped on every lock (incl. the
180-min auto-lock) and on every boot, and rebuilding one thumbnail means
decrypting a whole ~36 MB clip again. On the 1 GB Pi, redoing that for every
clip after each login is what ran into the OOM killer on 2026-09-15.

Sealed with the vault's master key (Vault.seal, AES-256-GCM -- the same
scheme as the key sidecars), so the SSD still holds nothing readable without
the passphrase. Telemetry is zlib-compressed before sealing (JSON with ~36
frames per second of driving). File names are a hash of kind + clip id: no
timestamps or paths on disk.
"""
import os, time, zlib, shutil, hashlib

KINDS = ("thumb", "tel")
MAX_AGE_DAYS = 120   # clips that old are long gone from the stick (snapshots rotate)


class Derived:
    def __init__(self, root, vault):
        self.root = root
        self.vault = vault

    def _path(self, kind, key):
        h = hashlib.sha256((kind + ":" + key).encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.root, kind, h + ".tvk")

    def has(self, kind, key):
        """Cheap existence check; works with the vault locked."""
        return os.path.isfile(self._path(kind, key))

    def put(self, kind, key, data):
        """Seal and store; returns False if the vault is locked."""
        if not self.vault.is_unlocked():
            return False
        try:
            blob = self.vault.seal(zlib.compress(bytes(data), 6) if kind == "tel" else bytes(data))
        except Exception:
            return False
        p = self._path(kind, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, p)
        return True

    def put_file(self, kind, key, path):
        """Store a file from the RAM cache unless a sealed copy exists already."""
        if self.has(kind, key) or not os.path.isfile(path):
            return False
        with open(path, "rb") as f:
            return self.put(kind, key, f.read())

    def get(self, kind, key):
        """Unsealed bytes, or None (missing, vault locked, or sealed under an
        older master key after a factory reset)."""
        p = self._path(kind, key)
        if not self.vault.is_unlocked() or not os.path.isfile(p):
            return None
        try:
            with open(p, "rb") as f:
                data = self.vault.unseal(f.read())
            return zlib.decompress(data) if kind == "tel" else data
        except Exception:
            return None

    def restore(self, kind, key, dst):
        """Unseal into dst (a RAM cache file); True on success."""
        data = self.get(kind, key)
        if data is None:
            return False
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        tmp = dst + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        return True

    def prune(self, max_age_days=MAX_AGE_DAYS):
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for kind in KINDS:
            d = os.path.join(self.root, kind)
            for name in (os.listdir(d) if os.path.isdir(d) else []):
                p = os.path.join(d, name)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        return removed

    def drop(self):
        """Factory reset: the master key is gone, so these are unreadable anyway."""
        shutil.rmtree(self.root, ignore_errors=True)
