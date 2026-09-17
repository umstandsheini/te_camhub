"""
Viewer engine for the TeslaCam Hub. Scans BROWSE_ROOT (/mutable/TeslaCam, see
server.py) -- make_snapshot.sh's persistent symlink farm, which accumulates
clips across every snapshot generation instead of exposing only the latest
one, and is already flat (no EncryptedClips/ segment: make_snapshot.sh links
straight to RecentClips|SavedClips|SentryClips/...) whether the source clip is
current-firmware-encrypted or, on older firmware, plain. Because scan_dir has
no EncryptedClips/ prefix to strip, "is this sr encrypted" is decided purely
by content (_is_encrypted), never by path shape -- ENC_PREFIX/_is_enc_sr below
exist only to match key IDs the vault already stores without that prefix.

Per camera .mp4:
  - plain   : not eCryptfs           -> stream directly
  - ready   : encrypted + in RAM cache
  - key     : encrypted + FEK in vault -> decrypt on demand
  - locked  : encrypted + no FEK yet

Decryption is ON DEMAND into a tmpfs cache (OUT_DIR, e.g. /dev/shm) — never onto
the stick. Keys come from the vault (RAM-only). ffmpeg makes thumbnails.
"""
import os, glob, json, re, posixpath, hashlib, datetime, math, base64, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from ecryptfs import EcryptfsFile
from keybridge import is_ecryptfs
import keybridge, pipeline

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(.+)\.mp4$", re.I)
ENC_PREFIX = "EncryptedClips"
TRIP_GAP_MIN = 20
# event.json's "camera" field: Tesla's numeric camera enum -> our filename
# suffix. Same mapping Te_FITI's UI uses (CAM_LABELS) to show "Camera: Left"
# etc. -- pillar cameras (4/5) aren't in our 4-camera clip groups, so they
# fall back to front in _thumb_camera() below.
EVENT_CAMERA_NAMES = {0: "front", 1: "back", 2: "left_repeater", 3: "right_repeater"}


# Clip decrypts are RAM-heavy: pipeline.decrypt_and_cache holds the encrypted
# and the decrypted ~36 MB clip at once, plus telemetry parsing. They come
# from HTTP threads (one per thumbnail the browser asks for -- up to six at a
# time), from prepare() and from the background thumbnail pass; unbounded,
# that got the Hub OOM-killed on this 1 GB Pi several times on 2026-09-15.
# One at a time, Hub-wide. Taken inside the worker thread (not by the caller
# of _with_timeout), so a timed-out caller can't let a second decrypt start
# while the first is still running.
_DECRYPT_SLOTS = threading.Semaphore(1)
DECRYPT_TIMEOUT = 180   # includes waiting for the slot behind other decrypts
# A decrypt needs ~2x the clip size in process memory plus the clip size in
# /dev/shm (also RAM): don't start one below this much MemAvailable -- a
# skipped clip is better than the OOM killer taking the whole Hub down.
DECRYPT_MIN_AVAIL_MB = 180


def _mem_available_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _decrypt_one(src, dst, fek, make_room=None):
    with _DECRYPT_SLOTS:
        if os.path.exists(dst):   # done meanwhile by whoever held the slot
            return True
        if make_room:
            # Evict older cached clips BEFORE writing the new one; capping
            # only afterwards let /dev/shm run past its limit mid-clip.
            make_room(os.path.getsize(src))
        avail = _mem_available_mb()
        if avail is not None and avail < DECRYPT_MIN_AVAIL_MB:
            raise MemoryError(f"nur {avail} MB RAM frei – Entschlüsseln übersprungen")
        return pipeline.decrypt_and_cache(src, dst, fek)


class Viewer:
    def __init__(self, scan_dir, out_dir, vault, extra_roots=None,
                 tmpfs_cap=200 * 1024 * 1024, derived=None):
        self.scan_dir = scan_dir
        self.out_dir = out_dir
        self.vault = vault
        self.derived = derived   # derived.Derived: sealed thumbnails/telemetry on the SSD, or None
        self.extra_roots = extra_roots or []
        self.tmpfs_cap = tmpfs_cap
        # sr -> is_encrypted. A clip's content at a given sr never changes
        # (make_snapshot.sh always links a given filename to the same
        # originally-captured clip), so this is valid forever once read --
        # persisted to disk so a Hub restart doesn't have to re-open every
        # clip under scan_dir again just to rebuild it. That matters here:
        # scan_dir accumulates every clip ever captured (see module
        # docstring), so on a device with months of history this is
        # thousands of files, and re-probing all of them from a cold cache
        # is slow enough (real disk I/O per file) to make /api/clips hang
        # for minutes after every restart.
        self._enc_cache_path = os.path.join(os.path.dirname(os.path.normpath(scan_dir)), ".enc_cache.json")
        self._enc = self._load_enc_cache()
        self._meta = {}
        self._lcache = {"t": 0.0, "data": None}
        self._guard = threading.Lock()
        self._prep_locks = {}
        self._thumb_fail = {}                # cid -> {"count": int, "at": float}
        self._kidx = (0, {})                 # (key count, basename -> FEK), see _by_basename
        os.makedirs(os.path.join(out_dir, ".thumbs"), exist_ok=True)

    def _load_enc_cache(self):
        try:
            with open(self._enc_cache_path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_enc_cache(self):
        try:
            tmp = self._enc_cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._enc, f)
            os.replace(tmp, self._enc_cache_path)
        except Exception:
            pass

    # ---- keys from the vault ------------------------------------------------
    def _keys(self):
        try:
            return self.vault.keys() if self.vault.is_unlocked() else {}
        except Exception:
            return {}

    # ---- path helpers -------------------------------------------------------
    def _cache(self, sr): return os.path.normpath(os.path.join(self.out_dir, sr))
    def _src(self, sr):   return os.path.normpath(os.path.join(self.scan_dir, sr))
    def _is_enc_sr(self, sr): return sr == ENC_PREFIX or sr.startswith(ENC_PREFIX + "/")
    def _enc_id(self, sr):    return sr[len(ENC_PREFIX) + 1:] if self._is_enc_sr(sr) else sr

    def _is_encrypted(self, sr):
        if sr in self._enc:
            return self._enc[sr]
        try:
            with open(self._src(sr), "rb") as f:
                res = is_ecryptfs(f.read(28))
        except Exception:
            # Don't cache -- this is now a persistent, forever cache (see
            # __init__), so a transient read error (file briefly mid-write,
            # mount hiccup) must not get permanently baked in as "plain".
            return False
        self._enc[sr] = res
        return res

    def _by_basename(self, keys):
        """Clip basename -> FEK (b64). The vault's key IDs come in two
        layouts: relative to the latest snapshot's EncryptedClips/
        (key_fetch_loop via keybridge.clip_id -- flat RecentClips/<file>)
        or relative to scan_dir (keys fetched when a clip is opened, or for
        older snapshots). scan_dir is make_snapshot.sh's link farm, which
        files the same clip under RecentClips/<date>/ and again under
        SavedClips|SentryClips/<event>/, so only the basename (timestamp +
        camera, unique per clip) matches across both -- same reasoning as
        nassync.push_key_sidecars_local. Without this every clip keyed by
        the background loop showed as locked and never got decrypted. Keys
        are only ever added (Vault.merge_keys), so the key count is enough
        to tell when the index is stale."""
        cached = self._kidx
        if cached[0] != len(keys):
            idx = {}
            for kid, fek in keys.items():
                b = posixpath.basename(kid)
                if TS_RE.search(b):
                    idx.setdefault(b, fek)
            cached = self._kidx = (len(keys), idx)
        return cached[1]

    def _key_b64(self, sr, keys):
        if sr in keys: return keys[sr]
        eid = self._enc_id(sr)
        if eid in keys: return keys[eid]
        # Only timestamped clip names are unique; event.json etc. have to
        # match by full path.
        b = posixpath.basename(sr)
        return self._by_basename(keys).get(b) if TS_RE.search(b) else None

    def _key_for(self, sr, keys):
        fek = self._key_b64(sr, keys)
        return base64.b64decode(fek) if fek else None

    def _read_event_json(self, ejp, ejsr):
        """event.json's content, or None if it can't be read right now.
        Returns (data, resolved): resolved=False means "encrypted, no key
        yet -- try again next scan"; True means the result (success OR a
        genuine parse/decrypt failure) is final and safe to cache. Current
        firmware wraps event.json in eCryptfs exactly like the clips, with
        its own key that has to be fetched from Tesla like any other file
        (see keybridge.MEDIA_EXT)."""
        try:
            with open(ejp, "rb") as f:
                head = f.read(28)
        except OSError:
            return None, True
        if not is_ecryptfs(head):
            try:
                return json.load(open(ejp, encoding="utf-8")), True
            except Exception:
                return None, True
        fek = self._key_for(ejsr, self._keys())
        if not fek:
            return None, False
        try:
            plain = pipeline.decrypt_bytes(ejp, fek)
            return json.loads(plain), True
        except Exception:
            return None, True  # wrong/stale key or corrupt file -- don't retry forever

    # ---- state --------------------------------------------------------------
    def _cam_state(self, sr, keys):
        if self._is_encrypted(sr):
            if os.path.exists(self._cache(sr)):
                return {"state": "ready", "url": "media/" + sr}
            if self._key_b64(sr, keys):
                return {"state": "key"}
            return {"state": "locked"}
        return {"state": "plain", "url": "media/" + sr}

    def _telsr(self, folder, ts):
        return (folder + "/" if folder else "") + f"{ts}-front.telemetry.json"

    def _has_tel(self, telsr):
        """Telemetry available: in the RAM cache, or sealed on the SSD
        (derived.py) -- the latter survives locks and reboots."""
        return os.path.exists(self._cache(telsr)) or bool(self.derived and self.derived.has("tel", telsr))

    def _persist(self, kind, key, path):
        """Keep a sealed copy of a RAM-cache file (no-op if one exists)."""
        if self.derived:
            try:
                self.derived.put_file(kind, key, path)
            except OSError:
                pass

    def seal_ram_telemetry(self):
        """Seal front-camera telemetry that so far only exists in the RAM
        cache. _persist() can't seal while the vault is locked, and the
        background loops compute clip meta (and cache it) regardless of the
        vault -- right after a Hub restart that left 107 of 116 telemetry
        files RAM-only (2026-09-15). Called every key_fetch_loop pass while
        unlocked; the files are tiny. Returns how many got sealed."""
        if not (self.derived and self.vault.is_unlocked()):
            return 0
        n = 0
        base = os.path.normpath(self.out_dir)
        for root, _dirs, names in os.walk(base):
            for nm in names:
                if nm.endswith("-front.telemetry.json"):
                    p = os.path.join(root, nm)
                    try:
                        if self.derived.put_file("tel", os.path.relpath(p, base).replace("\\", "/"), p):
                            n += 1
                    except OSError:
                        pass
        return n

    def _scan(self, keys=None):
        if keys is None:
            keys = self._keys()
        enc_before = len(self._enc)
        clips = {}
        for path in glob.glob(os.path.join(self.scan_dir, "**", "*.mp4"), recursive=True):
            m = TS_RE.search(os.path.basename(path))
            if not m:
                continue
            ts, cam = m.group(1), m.group(2).lower()
            sr = os.path.relpath(path, self.scan_dir).replace("\\", "/")
            folder = posixpath.dirname(sr)
            ck = folder + "|" + ts
            c = clips.setdefault(ck, {"id": ck, "folder": folder, "timestamp": ts,
                                      "cameras": {}, "telemetry": None})
            c["cameras"][cam] = self._cam_state(sr, keys)
            if cam == "front" and self._has_tel(self._telsr(folder, ts)):
                c["telemetry"] = "media/" + self._telsr(folder, ts)
        if len(self._enc) != enc_before:
            self._save_enc_cache()
        out = [self._finalize(c) for c in clips.values()]
        self._mark_trigger_segments(out)
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out

    def _mark_trigger_segments(self, clips):
        """Flag the one segment of each event folder that actually contains
        the trigger moment, so the UI can make it stand out instead of
        showing every ~1-minute segment of the event as equally important.

        Tesla writes one event.json per folder but records the rolling
        buffer as separate ~1-minute segments, so every segment in the
        folder inherits the same event/reason with no indication of which
        one the door handle was actually pulled in. The trigger belongs to
        the last segment that starts at or before the event timestamp --
        robust to segments being 60 or 61s apart and to a short final
        segment. Ported from Te_FITI's _mark_trigger_segments()."""
        by_folder = {}
        for c in clips:
            if c.get("has_event") and c.get("event_ts"):
                by_folder.setdefault(c["folder"], []).append(c)
        for group in by_folder.values():
            try:
                ev_dt = datetime.datetime.strptime(group[0]["event_ts"][:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                continue
            best, best_off = None, None
            for c in group:
                try:
                    start = datetime.datetime.strptime(c["timestamp"], "%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    continue
                off = (ev_dt - start).total_seconds()
                if off < 0:
                    continue  # segment starts after the trigger
                if best_off is None or off < best_off:
                    best, best_off = c, off
            if best is not None and best_off is not None and best_off <= 3600:
                best["is_trigger"] = True
                best["event_at"] = round(best_off, 1)

    def _finalize(self, c):
        sts = [cm["state"] for cm in c["cameras"].values()]
        c["needs_prepare"] = "key" in sts
        c["has_locked"] = "locked" in sts
        c["playable"] = any(s in ("plain", "ready") for s in sts)
        c["encrypted"] = any(s in ("ready", "key", "locked") for s in sts)
        enc_sts = [s for s in sts if s in ("ready", "key", "locked")]
        c["cams_encrypted"] = len(enc_sts)
        c["cams_keyed"] = sum(1 for s in enc_sts if s in ("ready", "key"))
        cached = self._meta.get(c["id"])
        if cached is None or cached.get("_pending"):
            cached = self._compute_meta(c)
            self._meta[c["id"]] = cached
        c.update({k: v for k, v in cached.items() if k != "_pending"})
        return c

    def _compute_meta(self, c):
        telsr = self._telsr(c["folder"], c["timestamp"])
        telp = self._cache(telsr)
        ht, gps, track, reason = False, None, [], None
        raw, tel_pending = None, False
        try:
            if os.path.isfile(telp):
                with open(telp, "rb") as f:
                    raw = f.read()
                self._persist("tel", telsr, telp)   # RAM-only telemetry from before derived.py existed
            elif self.derived and self.derived.has("tel", telsr):
                # Straight from the sealed copy, without putting it into the
                # RAM cache: hundreds of clips' telemetry would pile up in /dev/shm.
                raw = self.derived.get("tel", telsr)
                tel_pending = raw is None   # vault locked: retry later instead of caching "no telemetry"
            if raw:
                tel = json.loads(raw)
                ht = tel.get("frame_count", 0) > 0
                pts = [[f["lat"], f["lon"]] for f in tel.get("frames", []) if f.get("lat") and f.get("lon")]
                if pts:
                    gps = {"center_lat": sum(p[0] for p in pts) / len(pts),
                           "center_lon": sum(p[1] for p in pts) / len(pts)}
                    track = pts[::max(1, len(pts) // 40)]
        except Exception:
            pass
        ejp = os.path.join(self.scan_dir, c["folder"], "event.json")
        ejsr = (c["folder"] + "/" if c["folder"] else "") + "event.json"
        he = os.path.isfile(ejp)
        event_ts = None
        event_camera = None
        pending = False
        if he:
            ev, resolved = self._read_event_json(ejp, ejsr)
            pending = not resolved
            if ev:
                reason = ev.get("reason") or None
                event_ts = ev.get("timestamp") or None
                event_camera = ev.get("camera")
                if not gps:
                    lat = float(ev.get("est_lat") or ev.get("lat") or 0)
                    lon = float(ev.get("est_lon") or ev.get("lon") or 0)
                    if lat and lon:
                        gps = {"center_lat": lat, "center_lon": lon}
        return {"has_tel": ht, "has_event": he, "gps_bounds": gps,
                "has_data": ht or he, "reason": reason, "event_ts": event_ts,
                "event_camera": event_camera,
                "_track": track, "_pending": pending or tel_pending}

    def event_data(self, cid):
        """event.json for a clip's folder, incl. the seek offset (seconds
        into the clip) computed from the event timestamp vs. the clip's
        start timestamp -- lets the player jump straight to the trigger."""
        folder, ts = cid.rsplit("|", 1) if "|" in cid else ("", cid)
        ejp = os.path.join(self.scan_dir, folder, "event.json")
        if not os.path.isfile(ejp):
            return None
        ejsr = (folder + "/" if folder else "") + "event.json"
        ev, _resolved = self._read_event_json(ejp, ejsr)
        if not ev:
            return None
        result = {}
        et = ev.get("timestamp", "")
        if et:
            try:
                cs = datetime.datetime.strptime(ts, "%Y-%m-%d_%H-%M-%S")
                evt = datetime.datetime.strptime(et[:19], "%Y-%m-%dT%H:%M:%S")
                off = (evt - cs).total_seconds()
                if 0 <= off <= 3600:
                    result["seek"] = off
            except Exception:
                pass
        lat = float(ev.get("est_lat") or ev.get("lat") or 0)
        lon = float(ev.get("est_lon") or ev.get("lon") or 0)
        if lat and lon:
            result["lat"] = lat
            result["lon"] = lon
        for k in ("reason", "city", "street"):
            if ev.get(k):
                result[k] = ev[k]
        if ev.get("camera") is not None:
            result["camera"] = ev["camera"]
        return result or None

    def clips(self, ttl=10):
        now = time.time()
        with self._guard:
            if self._lcache["data"] is None or now - self._lcache["t"] >= ttl:
                self._lcache["data"] = self._scan()
                self._lcache["t"] = now
            return self._lcache["data"]

    def invalidate(self):
        self._lcache["t"] = 0.0

    def counts(self):
        cl = self.clips()
        cams = [cm for c in cl for cm in c["cameras"].values()]
        return {"clips": len(cl),
                "encrypted": sum(1 for cm in cams if cm["state"] in ("ready", "key", "locked")),
                "plain": sum(1 for cm in cams if cm["state"] == "plain"),
                "ready": sum(1 for cm in cams if cm["state"] == "ready"),
                "locked": sum(1 for cm in cams if cm["state"] == "locked")}

    # ---- decrypt on demand --------------------------------------------------
    def _clip_lock(self, cid):
        with self._guard:
            l = self._prep_locks.get(cid)
            if l is None:
                l = threading.Lock(); self._prep_locks[cid] = l
            return l

    def _clip_cams(self, cid):
        folder, ts = cid.rsplit("|", 1) if "|" in cid else ("", cid)
        cams = {}
        for path in glob.glob(os.path.join(self.scan_dir, folder, f"{ts}-*.mp4")):
            m = TS_RE.search(os.path.basename(path))
            if m:
                cam = m.group(2).lower()
                cams[cam] = (folder + "/" if folder else "") + f"{ts}-{cam}.mp4"
        return folder, ts, cams

    def _with_timeout(self, fn, *args, timeout=45):
        """pipeline.decrypt_and_cache()/telemetry_for_plain() both do a
        plain, unbounded open().read() -- if the clip's backing storage (an
        older snapshot generation whose loop mount has gone slow/bad) is
        stuck, that blocks forever with no way to time out, unlike
        make_thumbnail()'s ffmpeg call. Run it in a throwaway thread and
        bound how long we wait, so one bad clip can't freeze the whole
        prepare()/bulk_prepare() loop. Python can't force-kill a thread, so
        a genuinely stuck read leaks that one thread -- acceptable to let
        the caller move on rather than hang indefinitely."""
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(fn, *args)
        try:
            result = fut.result(timeout=timeout)
        except _FutureTimeout:
            ex.shutdown(wait=False)
            raise TimeoutError(f"{fn.__name__} timed out after {timeout}s: {args[0]}")
        ex.shutdown(wait=False)
        return result

    def prepare(self, cid):
        keys = self._keys()
        folder, ts, cams = self._clip_cams(cid)
        if not cams:
            return {"ok": False, "error": "clip not found"}
        errs = []
        def do(item):
            cam, sr = item
            try:
                if self._is_encrypted(sr):
                    if not os.path.exists(self._cache(sr)):
                        fek = self._key_for(sr, keys)
                        if fek:
                            self._with_timeout(_decrypt_one, self._src(sr), self._cache(sr), fek,
                                               self._make_room, timeout=DECRYPT_TIMEOUT)
                elif cam == "front":
                    telp = os.path.splitext(self._cache(sr))[0] + ".telemetry.json"
                    self._with_timeout(pipeline.telemetry_for_plain, self._src(sr), telp)
            except Exception as e:
                errs.append(f"{cam}: {e}")
        # One camera at a time: each decrypt holds the encrypted and the
        # decrypted clip in RAM at once (~2 x 36 MB plus telemetry parsing),
        # and all four in parallel is what got the Hub OOM-killed on this
        # 1 GB Pi (2026-09-15).
        with self._clip_lock(cid):
            for item in cams.items():
                do(item)
        telsr = self._telsr(folder, ts)
        self._persist("tel", telsr, self._cache(telsr))   # sealed copy survives lock/reboot (derived.py)
        self._cap_tmpfs()
        self._meta.pop(cid, None)
        self.invalidate()
        keys2 = self._keys()
        cameras = {cam: self._cam_state(sr, keys2) for cam, sr in cams.items()}
        return {"ok": not errs, "errors": errs, "cameras": cameras}

    def clip_paths(self, cid):
        """Absolute source path per camera for a clip (for on-demand key fetch)."""
        _folder, _ts, cams = self._clip_cams(cid)
        return {cam: self._src(sr) for cam, sr in cams.items()}

    def clip_rel_paths(self, cid):
        """scan_dir-relative path per camera for a clip -- the same string
        keybridge.clip_id() would compute, and the convention the vault's
        key IDs already use, so callers matching this clip's cameras
        against the key store can use these directly instead of
        recomputing a relpath against some other base dir."""
        _folder, _ts, cams = self._clip_cams(cid)
        return dict(cams)

    def bulk_targets(self):
        """Clips that still need work: locked/keyed cameras to decrypt, or a
        plain front camera whose telemetry hasn't been extracted yet."""
        out = []
        for c in self.clips():
            front = c["cameras"].get("front", {})
            if c["needs_prepare"] or (front.get("state") == "plain" and not c.get("has_tel")):
                out.append(c["id"])
        return out

    def bulk_prepare(self, on_progress=None, targets=None):
        """Decrypt + extract metadata (telemetry/thumbnail) for every clip
        that needs it. Calls on_progress(done, total, cid) after each clip.
        Callers that already have a fresh bulk_targets() result (e.g. to
        report a total before the first clip finishes) can pass it in via
        targets= to skip scanning clips() a second time."""
        if targets is None:
            targets = self.bulk_targets()
        errors = []
        for i, cid in enumerate(targets, 1):
            res = self.prepare(cid)
            if not res.get("ok"):
                errors.extend(res.get("errors", []))
            try:
                self.make_thumb(cid)
            except Exception:
                pass
            # What a bulk pass keeps is thumbnails + telemetry; the decrypted
            # clips don't fit the RAM-backed cache anyway, so drop them now
            # instead of churning through the cap (opening a clip decrypts
            # it again on demand).
            for sr in self._clip_cams(cid)[2].values():
                try:
                    os.remove(self._cache(sr))
                except OSError:
                    pass
            if on_progress:
                on_progress(i, len(targets), cid)
        return {"ok": not errors, "total": len(targets), "errors": errors}

    def resolve_media(self, sr):
        sr = posixpath.normpath(sr).lstrip("/")
        cp = self._cache(sr)
        if cp.startswith(os.path.normpath(self.out_dir)) and os.path.isfile(cp):
            return cp
        # Telemetry the player asks for: restore just this one file from its
        # sealed copy (a few KB) instead of decrypting the clip again.
        if sr.endswith("-front.telemetry.json") and cp.startswith(os.path.normpath(self.out_dir) + os.sep) \
                and self.derived and self.derived.restore("tel", sr, cp):
            return cp
        if not self._is_encrypted(sr):
            sp = self._src(sr)
            if sp.startswith(os.path.normpath(self.scan_dir)) and os.path.isfile(sp):
                return sp
        return None

    def make_thumb(self, cid):
        folder, ts, cams = self._clip_cams(cid)
        if not cams and "|" in cid:
            folder, ts = cid.rsplit("|", 1)
        cam = self._thumb_camera(cid, cams)
        target = (folder + "/" if folder else "") + f"{ts}-{cam}.mp4"
        if not self._is_encrypted(target):
            tp = os.path.join(self.scan_dir, folder, "thumb.png")
            if os.path.isfile(tp):
                return tp
        cache = os.path.join(self.out_dir, ".thumbs",
                             hashlib.sha1(cid.encode()).hexdigest()[:20] + ".jpg")
        if os.path.isfile(cache):
            self._persist("thumb", cid, cache)   # RAM-only thumbnail from before derived.py existed
            return cache
        # Sealed copy from an earlier session (derived.py): a few KB to
        # unseal instead of decrypting a whole ~36 MB clip.
        if self.derived and self.derived.restore("thumb", cid, cache):
            return cache
        # Some source clips just never produce a frame (corrupt segment, a
        # snapshot generation whose backing loop mount has gone slow/bad) --
        # ffmpeg then burns its full 60s timeout on every attempt. Without a
        # backoff, ensure_thumbnails() (called every 60s) retries the same
        # doomed clip forever, one more stuck thread each time, which is what
        # drove the Pi into sustained overload. Give up after a few tries,
        # and don't retry a fresh failure for a while either.
        fail = self._thumb_fail.get(cid)
        if fail and (fail["count"] >= 3 or time.time() - fail["at"] < 1800):
            return None
        keys = self._keys()
        with self._clip_lock(cid):
            if os.path.isfile(cache):
                return cache
            if self._is_encrypted(target):
                cp = self._cache(target)
                if not os.path.isfile(cp):
                    fek = self._key_for(target, keys)
                    if not fek:
                        return None
                    try:
                        self._with_timeout(_decrypt_one, self._src(target), cp, fek, self._make_room,
                                           timeout=DECRYPT_TIMEOUT)
                    except Exception:
                        self._note_thumb_fail(cid)
                        return None
                    self._cap_tmpfs()
                src = cp
            else:
                src = self._src(target)
                if not os.path.isfile(src):
                    return None
            if pipeline.make_thumbnail(src, cache, seek=self._thumb_seek(cid)):
                self._thumb_fail.pop(cid, None)
                self._persist("thumb", cid, cache)
                telsr = self._telsr(folder, ts)
                self._persist("tel", telsr, self._cache(telsr))   # written by the front-camera decrypt
                return cache
            self._note_thumb_fail(cid)
            return None

    def _note_thumb_fail(self, cid):
        f = self._thumb_fail.setdefault(cid, {"count": 0, "at": 0.0})
        f["count"] += 1
        f["at"] = time.time()

    def _trigger_info(self, cid):
        """(is_trigger, event_at, event_camera) for cid, read off the
        already-computed clips() list (10s cache) rather than recomputed
        here, so seek/camera choice always agrees with the 🎯 badge shown
        for the same clip in the UI."""
        for c in self.clips():
            if c["id"] == cid:
                return c.get("is_trigger"), c.get("event_at"), c.get("event_camera")
        return None, None, None

    def _thumb_seek(self, cid, default=1.0):
        """Where to grab the thumbnail frame from. If this clip is the exact
        segment the event actually happened in (is_trigger -- the event
        timestamp falls inside it, not just inside the same event folder),
        seek to that moment so the thumbnail shows the event itself instead
        of an arbitrary early frame."""
        is_trigger, event_at, _cam = self._trigger_info(cid)
        if is_trigger and event_at is not None:
            return max(0.0, float(event_at))
        return default

    def _thumb_camera(self, cid, cams):
        """Which camera to grab the thumbnail frame from: the one Tesla
        recorded as the actual trigger source (event.json's "camera"
        field), if this clip is the trigger segment and that camera is
        actually present in this clip -- otherwise front, as before."""
        is_trigger, _at, event_camera = self._trigger_info(cid)
        if is_trigger and event_camera is not None:
            try:
                name = EVENT_CAMERA_NAMES.get(int(event_camera))
            except (TypeError, ValueError):
                name = None
            if name and name in cams:
                return name
        return "front"

    def ensure_thumbnails(self, limit=3):   # each encrypted thumbnail decrypts a whole ~36 MB clip first
        """Best-effort background thumbnail generation, called periodically
        (see key_fetch_loop in server.py) so thumbnails exist before anyone
        opens the Viewer -- covers every clip whose front camera is already
        playable or has a key waiting (make_thumb() only decrypts that one
        camera for a single frame, not a full prepare()). Capped per call
        so a large backlog can't block the calling loop for long; whatever
        doesn't fit catches up on the next pass 60s later."""
        made = 0
        for c in self.clips():
            cache = os.path.join(self.out_dir, ".thumbs",
                                 hashlib.sha1(c["id"].encode()).hexdigest()[:20] + ".jpg")
            if os.path.isfile(cache):
                continue
            # A sealed copy (derived.py) is a few KB to restore -- the limit
            # below is only there to bound real decrypts.
            if self.derived and self.derived.restore("thumb", c["id"], cache):
                continue
            if made >= limit:
                continue
            front = c["cameras"].get("front", {})
            if front.get("state") not in ("plain", "ready", "key"):
                continue
            try:
                if self.make_thumb(c["id"]):
                    made += 1
            except Exception:
                pass
        return made

    def _make_room(self, need):
        """Evict cached clips until `need` more bytes fit under tmpfs_cap
        (called before each decrypt, see _decrypt_one)."""
        self._cap_tmpfs(reserve=need)

    def _cap_tmpfs(self, reserve=0):
        limit = max(0, self.tmpfs_cap - reserve)
        try:
            files = []
            for root, _, names in os.walk(self.out_dir):
                for nm in names:
                    if nm.endswith(".mp4"):
                        fp = os.path.join(root, nm)
                        try:
                            st = os.stat(fp); files.append((st.st_atime, st.st_size, fp))
                        except OSError:
                            pass
            total = sum(f[1] for f in files)
            if total <= limit:
                return
            for _at, sz, fp in sorted(files):
                if total <= limit:
                    break
                try:
                    os.remove(fp); total -= sz
                except OSError:
                    pass
        except Exception:
            pass

    def clear_cache(self):
        for root, _, names in os.walk(self.out_dir):
            for nm in names:
                if nm.endswith((".mp4", ".jpg", ".png", ".telemetry.json")):
                    try:
                        os.remove(os.path.join(root, nm))
                    except OSError:
                        pass

    # ---- trips / gps --------------------------------------------------------
    def all_gps(self):
        pts = []
        for c in self.clips():
            gb = c.get("gps_bounds")
            if gb:
                pts.append([gb["center_lat"], gb["center_lon"], c["id"]])
        return pts

    def trips(self, gap_min=TRIP_GAP_MIN):
        cl = sorted(self.clips(), key=lambda c: c["timestamp"])
        trips, group, prev = [], [], None
        def flush(g):
            if not g:
                return
            route = []
            for c in g:
                route += self._meta.get(c["id"], {}).get("_track") or (
                    [[c["gps_bounds"]["center_lat"], c["gps_bounds"]["center_lon"]]] if c.get("gps_bounds") else [])
            dist = sum(_haversine(*route[i], *route[i + 1]) for i in range(len(route) - 1))
            trips.append({"start": g[0]["timestamp"], "end": g[-1]["timestamp"],
                          "clip_ids": [c["id"] for c in g], "clip_count": len(g),
                          "distance_km": round(dist, 2), "route": route})
        for c in cl:
            dt = datetime.datetime.strptime(c["timestamp"], "%Y-%m-%d_%H-%M-%S")
            if group and prev and (dt - prev).total_seconds() > gap_min * 60:
                flush(group); group = []
            group.append(c); prev = dt
        flush(group)
        trips.sort(key=lambda t: t["start"], reverse=True)
        return trips


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
