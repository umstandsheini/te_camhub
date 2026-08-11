"""
NAS sync for the Hub -- two deliberately different procedures:

  1. Camera clips (EncryptedClips): ONE-WAY, stick -> NAS only. This is
     teslausb's own archiveloop/archive-clips.sh, not this module -- it
     moves clips off the stick permanently (frees space) and never pulls
     anything back down. refresh_status()/push_key_sidecars() below only
     *observe* that process (coverage %, key sidecars); they don't drive it.
  2. Music/LightShow/Boombox: TWO-WAY, via sync_media(). Pulls NAS-side
     changes down to the stick, then pushes stick-side changes up, so e.g.
     a song added directly on the NAS appears on the stick too, and vice
     versa. Neither direction deletes -- removing a file on one side leaves
     it untouched on the other.

ARCHIVE_SERVER/SHARE_NAME already point directly at the same TeslaCam/
EncryptedClips folder that teslausb's own archiveloop writes to, so local and
remote relative paths line up 1:1 -- no basename/size guessing needed, unlike
the old decrypt-viewer/retention.sh approach.

If a separate, unrelated tool (e.g. the "Te_FITI" Home Assistant add-on) is
also pointed at this NAS share with delete_originals enabled, it deletes each
encrypted original right after successfully decrypting it (to save space --
the FEK stays in its own keystore, so nothing is actually lost) and moves
permanently-undecryptable ones (no wrapped-key block) into a sibling
broken/<clips-folder>/ tree instead of leaving them in place. Naively that
makes refresh_status() think those clips were never archived at all, even
though they're safely accounted for one level up the share tree -- see the
DECRYPTED/BROKEN handling in refresh_status() below.

Camera-clip-observing jobs, both driven by nas_sync_loop() in server.py:
  - refresh_status(): mounts read-only, compares local vs. remote clip
    (folder+timestamp) groups -> cached coverage percentage.
  - push_key_sidecars(): for every local clip whose FEK is already in the
    vault AND whose video is confirmed archived, writes a small encrypted
    per-video key file (<video>.mp4.key.json) next to it on the NAS, plus a
    one-time README explaining what the files are for. The sidecar is
    self-describing (names the exact video it belongs to) and is useless
    without the vault passphrase (FEK is sealed with the vault's MK).
  - push_raw_keys(): OPT-IN, off by default. Writes the *unsealed* FEK
    (<video>.mp4.rawkey.json) so a separate, trusted system with NAS access
    can decrypt clips without ever knowing the vault passphrase. Gated by
    a NAS-pairing check (see _ensure_nas_pairing): a random token is minted
    on first use, stored both locally and in a file on the NAS share root;
    every subsequent run refuses to write raw keys unless the two match.
    This stops raw keys from ever landing on a NAS that got swapped/
    misconfigured/pointed elsewhere after the fact -- the whole point of
    offering this weaker mode is that it should only ever talk to the one
    NAS it was explicitly paired with.
  - push_key_sidecars_local(): local counterpart of push_key_sidecars(),
    for the Pi's own [TeslaCam] Samba export instead of a remote NAS --
    called every 60s from key_fetch_loop() in server.py, not
    nas_sync_loop(), since it needs no NAS configured at all.
"""
import os, re, json, time, base64, secrets, datetime, subprocess, tempfile, threading
import hubconf
import blackbox
import diag

PAIRING_FILE = "HUB-NAS-KOPPLUNG.json"

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(.+)\.mp4$", re.I)
README_NAME = "SCHLUESSEL-INFO.txt"
README_TEXT = (
    "Diese *.mp4.key.json Dateien enthalten den Entschluesselungs-Schluessel\n"
    "(FEK) fuer die gleichnamige Videodatei im selben Ordner, verschluesselt\n"
    "mit dem Tresor-Passwort der TeslaCam Hub App. Ohne dieses Passwort sind\n"
    "sie nutzlos. 'IMG_0001-front.mp4.key.json' gehoert zu 'IMG_0001-front.mp4'.\n"
)

_guard = threading.Lock()
_op_lock = threading.Lock()   # serializes mount operations (loop vs. manual refresh)
_cache = {"t": 0.0, "total": 0, "on_nas": 0, "percent": 0, "ok": None, "error": None, "clips": {}}
_media_cache = {"t": 0.0, "ok": None, "error": None, "copied": 0}
_trips_cache = {"t": 0.0, "ok": None, "error": None, "uploaded": 0}

TRIPS_FOLDER = "Fahrten"  # sits at the archive share's root, alongside TeslaCam/decrypted/broken
# Music merged into the Media partition and stayed there; LightShow and
# Boombox got split back onto their own dedicated partitions/LUNs after
# discovering Tesla's firmware only recognizes one of the two when they
# share a partition (see files.py's module docstring for sources). Maps
# each root to its local mount path relative to FS_BASE.
MEDIA_ROOTS = {"Music": "Media/Music", "LightShow": "LightShow", "Boombox": "Boombox"}
FS_BASE = "/var/www/html/fs"
LOCAL_TESLACAM = "/mutable/TeslaCam"   # served read-only over SMB, see setup/pi/configure-samba.sh


def _mount(mnt, rw, share=None):
    server = hubconf.getval("ARCHIVE_SERVER")
    share = share if share is not None else hubconf.getval("SHARE_NAME")
    user = hubconf.getval("SHARE_USER")
    password = hubconf.getval("SHARE_PASSWORD")
    vers = hubconf.getval("CIFS_VERSION") or "3.0"
    if not server or not share:
        raise RuntimeError("NAS nicht konfiguriert")
    os.makedirs(mnt, exist_ok=True)
    creds = tempfile.NamedTemporaryFile("w", delete=False)
    creds.write("username=%s\npassword=%s\n" % (user, password)); creds.close()
    os.chmod(creds.name, 0o600)
    opts = "credentials=%s,vers=%s,iocharset=utf8,%s" % (creds.name, vers, "rw" if rw else "ro")
    try:
        r = subprocess.run(["mount", "-t", "cifs", "//%s/%s" % (server, share), mnt,
                            "-o", opts], capture_output=True, text=True, timeout=25)
    finally:
        try: os.remove(creds.name)
        except OSError: pass
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "Mount fehlgeschlagen").splitlines()[-1][:200])


def _walk_basenames(root):
    """{filename -> True} for every .mp4 under root, or empty if root doesn't
    exist -- used for the DECRYPTED/BROKEN fallback trees, whose internal
    layout (flat vs. nested-by-date vs. nested-by-event) doesn't line up with
    EncryptedClips' own, so exact relpath matching isn't reliable there and
    basenames (each camera+timestamp is already unique) are used instead."""
    out = set()
    for root_, _, names in os.walk(root):
        for nm in names:
            if nm.endswith(".mp4"):
                out.add(nm)
    return out


def _umount(mnt):
    subprocess.run(["umount", mnt], capture_output=True)


def _clip_groups(files):
    """{"folder|timestamp": {cam: relpath}} from a list of relpaths (relpaths
    are relative to the EncryptedClips folder). The clip id is prefixed with
    'EncryptedClips' to match Viewer's ids (which are relative to the TeslaCam
    root one level up), so the frontend can look up nas-sync status by c.id."""
    groups = {}
    for rel in files:
        m = TS_RE.search(os.path.basename(rel))
        if not m:
            continue
        ts, cam = m.group(1), m.group(2).lower()
        sub = os.path.dirname(rel)
        folder = "EncryptedClips/" + sub if sub else "EncryptedClips"
        groups.setdefault(folder + "|" + ts, {})[cam] = rel
    return groups


def _on_nas(rel, ts, remote):
    """True if rel (or teslausb's alternate RecentClips/<date>/ layout for
    the same file) is present in the remote set. teslausb's own archiver
    nests RecentClips under a per-day subfolder on the NAS
    (RecentClips/2026-07-08/2026-07-08_17-35-19-front.mp4) while the local
    snapshot tree keeps them flat (RecentClips/2026-07-08_17-35-19-front.mp4)
    -- without this, every RecentClips entry looks permanently un-synced
    even though it's actually already archived."""
    if rel in remote:
        return True
    if rel.startswith("RecentClips/") and "/" not in rel[len("RecentClips/"):]:
        alt = f"RecentClips/{ts[:10]}/{rel[len('RecentClips/'):]}"
        return alt in remote
    return False


def refresh_status(scan_dir):
    """Recompute local-vs-NAS clip coverage, per clip. scan_dir = .../TeslaCam"""
    src = os.path.join(scan_dir, "EncryptedClips")
    local = []
    for root, _, names in os.walk(src):
        for nm in names:
            if nm.endswith(".mp4"):
                local.append(os.path.relpath(os.path.join(root, nm), src).replace("\\", "/"))
    local_groups = _clip_groups(local)
    total = len(local_groups)
    on_nas = 0
    clip_status = {}
    err = None
    mnt = "/tmp/hub_nas_status"
    if total:
        with _op_lock:
            try:
                # Mount the share root (not SHARE_NAME's full .../EncryptedClips
                # path) so the DECRYPTED/BROKEN fallback trees, which are
                # siblings of it, are reachable too.
                full = (hubconf.getval("SHARE_NAME") or "").strip("/")
                share_root, _, clips_subpath = full.partition("/")
                _mount(mnt, rw=False, share=share_root)
                try:
                    enc_dir = os.path.join(mnt, clips_subpath) if clips_subpath else mnt
                    remote = set()
                    for root, _, names in os.walk(enc_dir):
                        for nm in names:
                            if nm.endswith(".mp4"):
                                remote.add(os.path.relpath(os.path.join(root, nm), enc_dir).replace("\\", "/"))
                    # Te_FITI (if present) deletes each original right after a
                    # successful decrypt and moves undecryptable ones aside --
                    # see module docstring. A clip missing from EncryptedClips
                    # is still "on the NAS" if it shows up in either tree.
                    fallback = _walk_basenames(os.path.join(mnt, "decrypted"))
                    fallback |= _walk_basenames(os.path.join(mnt, "broken", os.path.basename(clips_subpath) or "EncryptedClips"))
                    for ck, cams in local_groups.items():
                        ts = ck.rsplit("|", 1)[-1]
                        synced = all(_on_nas(rel, ts, remote) or os.path.basename(rel) in fallback
                                     for rel in cams.values())
                        clip_status[ck] = synced
                        if synced:
                            on_nas += 1
                finally:
                    _umount(mnt)
            except Exception as e:
                err = str(e)
    with _guard:
        _cache.update(t=time.time(), total=total, on_nas=on_nas,
                       percent=(round(on_nas * 100 / total) if total else 100),
                       ok=(err is None), error=err, clips=clip_status)
    return dict(_cache)


_ARCHIVE_START_RE = re.compile(r"Archiving (\d+) file\(s\).*?starting at")
_ARCHIVE_OK_RE = re.compile(r"Archiving completed successfully\. Archived (\d+) files?")
_ARCHIVE_ERR_RE = re.compile(r"Error during archiving\. Archived (\d+) files?")


def archive_progress():
    """Best-effort live progress of teslausb's own native camera-clip
    archiving run (see module docstring -- a completely separate mechanism
    from refresh_status()'s NAS coverage check above, which only samples
    once every 10 minutes and says nothing about an in-flight transfer).
    Parsed from archiveloop.log (to find the most recent "Archiving N
    file(s)... starting at" line and how it ended) plus archive-clips.sh's
    own rsync --log-file (one line per file actually transferred so far),
    so a large backlog -- e.g. after a network interruption delayed the
    normal per-minute runs -- shows up as real progress instead of the UI
    just reporting the coverage-check error while the transfer is quietly
    still working.

    A run can also end with "Error during archiving" rather than
    "completed successfully" -- archive-clips.sh's own connectionmonitor
    kills rsync outright if the NAS's SMB port stops answering for ~30s
    straight (see run/cifs_archive/archive-clips.sh), which happens on a
    flaky network even while the transfer itself was still making
    progress. That's reported as error=True with however many files did
    make it through before the kill, not silently as "still running"."""
    tail = diag.tail_log("archiveloop", lines=400)
    lines = tail.splitlines() if tail else []
    total = None
    start_idx = None
    for i in range(len(lines) - 1, -1, -1):
        m = _ARCHIVE_START_RE.search(lines[i])
        if m:
            total = int(m.group(1))
            start_idx = i
            break
    if start_idx is None:
        return {"running": False, "total": None, "done": None, "error": False}
    for line in lines[start_idx + 1:]:
        m = _ARCHIVE_OK_RE.search(line)
        if m:
            return {"running": False, "total": total, "done": int(m.group(1)), "error": False}
        m = _ARCHIVE_ERR_RE.search(line)
        if m:
            return {"running": False, "total": total, "done": int(m.group(1)), "error": True}
    done = 0
    try:
        with open("/tmp/archive-rsync-cmd.log", encoding="utf-8", errors="replace") as f:
            done = sum(1 for _ in f)
    except OSError:
        pass
    return {"running": True, "total": total, "done": min(done, total), "error": False}


def status():
    with _guard:
        return dict(_cache)


def media_status():
    with _guard:
        return dict(_media_cache)


def _media_mount(mnt, rw):
    """Mount only the CIFS *share* itself (the first path segment of
    SYNC_MEDIA_PATH) -- that's the one thing that must already exist on the
    NAS. Any subpath after it (and the Music/LightShow/Boombox folders) are
    created automatically once mounted, so a typo'd or not-yet-existing
    subfolder never breaks the mount itself."""
    server = hubconf.getval("ARCHIVE_SERVER")
    raw = (hubconf.getval("SYNC_MEDIA_PATH") or "").strip().strip("/")
    user = hubconf.getval("SHARE_USER")
    password = hubconf.getval("SHARE_PASSWORD")
    vers = hubconf.getval("CIFS_VERSION") or "3.0"
    if not server or not raw:
        raise RuntimeError("Sync-Pfad nicht konfiguriert (unter Einstellungen eintragen und speichern)")
    share, _, subpath = raw.partition("/")
    os.makedirs(mnt, exist_ok=True)
    creds = tempfile.NamedTemporaryFile("w", delete=False)
    creds.write("username=%s\npassword=%s\n" % (user, password)); creds.close()
    os.chmod(creds.name, 0o600)
    opts = "credentials=%s,vers=%s,iocharset=utf8,%s" % (creds.name, vers, "rw" if rw else "ro")
    try:
        r = subprocess.run(["mount", "-t", "cifs", "//%s/%s" % (server, share), mnt,
                            "-o", opts], capture_output=True, text=True, timeout=25)
    finally:
        try: os.remove(creds.name)
        except OSError: pass
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "Mount fehlgeschlagen").splitlines()[-1][:200])
    return subpath


def _rsync(src, dst, errors, label):
    r = subprocess.run(
        ["rsync", "-rt", "--no-perms", "--no-owner", "--no-group", src + "/", dst + "/"],
        capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        errors.append(f"{label}: {(r.stderr or '').splitlines()[-1][:200] if r.stderr else 'rsync-Fehler'}")
        return False
    return True


def sync_media():
    """Two-way sync of Music/LightShow/Boombox between the stick (already
    locally mounted rw by teslausb's own autofs under FS_BASE) and a
    configurable NAS path, auto-creating the subpath and one subfolder per
    partition there. Unlike the camera-clip archive (one-way, stick -> NAS
    only, teslausb's own mechanism), this pulls NAS-side changes down to the
    stick first, then pushes stick-side changes up -- so e.g. music added
    directly on the NAS shows up on the stick too. Neither direction ever
    deletes: files removed on one side simply stay on the other."""
    mnt = "/tmp/hub_nas_media"
    _op_lock.acquire()
    try:
        subpath = _media_mount(mnt, rw=True)
    except Exception as e:
        with _guard:
            _media_cache.update(t=time.time(), ok=False, error=str(e))
        _op_lock.release()
        return {"ok": False, "error": str(e)}
    copied, errors = 0, []
    try:
        base = os.path.join(mnt, subpath) if subpath else mnt
        os.makedirs(base, exist_ok=True)
        for root_name, local_rel in MEDIA_ROOTS.items():
            local_dir = os.path.join(FS_BASE, local_rel)
            if not os.path.isdir(local_dir):   # accessing triggers autofs
                continue
            dest_dir = os.path.join(base, root_name)
            os.makedirs(dest_dir, exist_ok=True)
            ok_pull = _rsync(dest_dir, local_dir, errors, root_name + " (NAS->Stick)")
            ok_push = _rsync(local_dir, dest_dir, errors, root_name + " (Stick->NAS)")
            if ok_pull and ok_push:
                copied += 1
    finally:
        _umount(mnt)
        _op_lock.release()
    with _guard:
        _media_cache.update(t=time.time(), ok=not errors, error="; ".join(errors) or None, copied=copied)
    return {"ok": not errors, "copied": copied, "errors": errors}


def trips_status():
    with _guard:
        return dict(_trips_cache)


def sync_trips(active_trip_id=None):
    """One-way push of every finished blackbox trip's GPX export to
    TRIPS_FOLDER at the archive share's root (reuses ARCHIVE_SERVER/
    SHARE_NAME -- no separate path to configure). Skips active_trip_id (the
    trip still being recorded, if any -- its GPX would be incomplete) and
    any trip whose GPX already exists on the NAS: finished trips never
    change, so an existing remote file is always up to date and re-uploading
    it would be wasted work."""
    trips = [t["trip_id"] for t in blackbox.list_trips() if t["trip_id"] != active_trip_id]
    if not trips:
        with _guard:
            _trips_cache.update(t=time.time(), ok=True, error=None, uploaded=0)
        return {"ok": True, "uploaded": 0}
    mnt = "/tmp/hub_nas_trips"
    full = (hubconf.getval("SHARE_NAME") or "").strip("/")
    share_root, _, _ = full.partition("/")
    _op_lock.acquire()
    try:
        _mount(mnt, rw=True, share=share_root)
    except Exception as e:
        with _guard:
            _trips_cache.update(t=time.time(), ok=False, error=str(e))
        _op_lock.release()
        return {"ok": False, "error": str(e)}
    uploaded, errors = 0, []
    try:
        dest_dir = os.path.join(mnt, TRIPS_FOLDER)
        os.makedirs(dest_dir, exist_ok=True)
        for trip_id in trips:
            dest = os.path.join(dest_dir, trip_id + ".gpx")
            if os.path.isfile(dest):
                continue
            try:
                gpx = blackbox.to_gpx(trip_id)
                tmp = dest + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(gpx)
                os.replace(tmp, dest)
                uploaded += 1
            except Exception as e:
                errors.append(f"{trip_id}: {e}")
    finally:
        _umount(mnt)
        _op_lock.release()
    with _guard:
        _trips_cache.update(t=time.time(), ok=not errors, error="; ".join(errors) or None, uploaded=uploaded)
    return {"ok": not errors, "uploaded": uploaded, "errors": errors}


def push_key_sidecars(scan_dir, vault):
    """Write a sealed per-video key sidecar on the NAS for every local clip
    whose FEK is already in the vault and whose video is already archived."""
    if not vault.is_unlocked():
        return {"ok": False, "error": "vault locked"}
    keys = vault.keys()
    if not keys:
        return {"ok": True, "written": 0}
    src = os.path.join(scan_dir, "EncryptedClips")
    mnt = "/tmp/hub_nas_keys"
    _op_lock.acquire()
    try:
        _mount(mnt, rw=True)
    except Exception as e:
        _op_lock.release()
        return {"ok": False, "error": str(e)}
    written, errors = 0, []
    try:
        readme = os.path.join(mnt, README_NAME)
        if not os.path.isfile(readme):
            try:
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(README_TEXT)
            except Exception:
                pass
        for cid, fek_b64 in keys.items():
            rel = cid.lstrip("/")
            if not os.path.isfile(os.path.join(src, rel)):
                continue          # key for a clip no longer on the stick
            remote_mp4 = os.path.join(mnt, rel)
            if not os.path.isfile(remote_mp4):
                continue          # video not archived yet -- nothing to attach to
            sidecar = remote_mp4 + ".key.json"
            if os.path.isfile(sidecar):
                continue
            try:
                sealed = vault.seal(base64.b64decode(fek_b64))
                payload = json.dumps({
                    "video": os.path.basename(rel),
                    "for_file": rel,
                    "algo": "AES-256-GCM (TeslaCam Hub vault)",
                    "created": datetime.datetime.utcnow().isoformat() + "Z",
                    "key_sealed_b64": base64.b64encode(sealed).decode("ascii"),
                }, indent=2)
                tmp = sidecar + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, sidecar)
                written += 1
            except Exception as e:
                errors.append(f"{rel}: {e}")
    finally:
        _umount(mnt)
        _op_lock.release()
    return {"ok": not errors, "written": written, "errors": errors}


def push_key_sidecars_local(vault):
    """Same idea as push_key_sidecars() above, but for the Pi's own local
    Samba export (LOCAL_TESLACAM, the [TeslaCam] share from
    setup/pi/configure-samba.sh) instead of a remote NAS -- so a plain SMB
    client (Windows Explorer, etc. connecting straight to the Pi, no NAS
    needed) sees the sealed key file right next to each video.

    Matches by basename rather than by the vault's EncryptedClips-relative
    id: teslausb's own make_snapshot.sh (see run/make_snapshot.sh) links
    each clip into LOCAL_TESLACAM via symlinks in potentially *multiple*
    places (RecentClips/<date>, and again into SavedClips/<event> or
    SentryClips/<event> for flagged clips) rather than mirroring
    EncryptedClips' own relative paths 1:1 -- basename matching (each
    clip+camera timestamp is unique) is the only layout-independent way to
    find every location a given video's symlink actually appears in, and
    writes a sidecar in each one."""
    if not vault.is_unlocked():
        return {"ok": False, "error": "vault locked"}
    keys = vault.keys()
    if not keys:
        return {"ok": True, "written": 0}
    if not os.path.isdir(LOCAL_TESLACAM):
        return {"ok": True, "written": 0}
    by_basename = {}
    for cid, fek_b64 in keys.items():
        by_basename.setdefault(os.path.basename(cid), (cid, fek_b64))

    readme = os.path.join(LOCAL_TESLACAM, README_NAME)
    if not os.path.isfile(readme):
        try:
            with open(readme, "w", encoding="utf-8") as f:
                f.write(README_TEXT)
        except Exception:
            pass

    written, errors = 0, []
    for root, _dirs, names in os.walk(LOCAL_TESLACAM):
        for nm in names:
            if not nm.endswith(".mp4"):
                continue
            match = by_basename.get(nm)
            if not match:
                continue
            sidecar = os.path.join(root, nm) + ".key.json"
            if os.path.isfile(sidecar):
                continue
            cid, fek_b64 = match
            try:
                sealed = vault.seal(base64.b64decode(fek_b64))
                payload = json.dumps({
                    "video": nm,
                    "for_file": cid,
                    "algo": "AES-256-GCM (TeslaCam Hub vault)",
                    "created": datetime.datetime.utcnow().isoformat() + "Z",
                    "key_sealed_b64": base64.b64encode(sealed).decode("ascii"),
                }, indent=2)
                tmp = sidecar + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, sidecar)
                written += 1
            except Exception as e:
                errors.append(f"{nm}: {e}")
    return {"ok": not errors, "written": written, "errors": errors}


def _pairing_path(state_dir):
    return os.path.join(state_dir, "nas_pairing_token.txt")


def pairing_status(state_dir):
    """Local view only (no NAS mount) -- whether this Hub has ever minted a
    pairing token. Doesn't confirm the NAS side still matches; that's only
    checked at the moment of an actual raw-key push."""
    p = _pairing_path(state_dir)
    if os.path.isfile(p):
        return {"paired": True, "token_prefix": open(p).read().strip()[:8]}
    return {"paired": False}


def reset_pairing(state_dir):
    """Forget the local pairing token. The next raw-key push will mint a
    fresh one and (re-)write it to whatever NAS is mounted at that time --
    use this deliberately when switching to a different/replacement NAS."""
    try:
        os.remove(_pairing_path(state_dir))
    except FileNotFoundError:
        pass
    return {"ok": True}


def _ensure_nas_pairing(mnt, state_dir):
    """Verify (or establish, on first use) that the currently-mounted NAS is
    the one this Hub instance is paired with, via a small token file at the
    share root. Returns True only if the NAS is confirmed to be the paired
    one (or is being paired for the first time); False means refuse to
    write raw keys."""
    local_path = _pairing_path(state_dir)
    local_tok = open(local_path).read().strip() if os.path.isfile(local_path) else None
    nas_file = os.path.join(mnt, PAIRING_FILE)

    remote_tok = None
    if os.path.isfile(nas_file):
        try:
            remote_tok = json.load(open(nas_file, encoding="utf-8")).get("hub_pairing_token")
        except Exception:
            remote_tok = None

    if local_tok and remote_tok:
        return local_tok == remote_tok
    if local_tok and not remote_tok:
        # We're paired locally but the NAS has no (or an unreadable) token
        # file -- could be a different/reset NAS. Refuse rather than assume.
        return False
    if remote_tok and not local_tok:
        # NAS already carries a token but this Hub's state dir has none
        # (e.g. freshly restored state) -- adopt it rather than overwrite.
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(remote_tok)
        return True
    # Neither side has a token yet: first-ever use, mint and write both.
    new_tok = secrets.token_hex(32)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(new_tok)
    with open(nas_file, "w", encoding="utf-8") as f:
        json.dump({"hub_pairing_token": new_tok,
                    "created": datetime.datetime.utcnow().isoformat() + "Z",
                    "note": "Kopplungs-Nachweis zwischen diesem TeslaCam Hub und diesem NAS. "
                            "Nicht löschen/verändern, sonst verweigert der Hub weitere "
                            "Roh-Schlüssel-Übertragungen zu diesem Share."}, f, indent=2)
    return True


def push_raw_keys(scan_dir, vault, state_dir):
    """OPT-IN: write the unsealed FEK next to each archived+keyed clip on
    the NAS, as <video>.mp4.rawkey.json, so a separate trusted system can
    decrypt clips using only NAS access -- no vault passphrase needed.
    Refuses outright if the NAS-pairing check fails."""
    if not vault.is_unlocked():
        return {"ok": False, "error": "vault locked"}
    keys = vault.keys()
    if not keys:
        return {"ok": True, "written": 0}
    src = os.path.join(scan_dir, "EncryptedClips")
    mnt = "/tmp/hub_nas_rawkeys"
    _op_lock.acquire()
    try:
        _mount(mnt, rw=True)
    except Exception as e:
        _op_lock.release()
        return {"ok": False, "error": str(e)}
    written, errors = 0, []
    try:
        if not _ensure_nas_pairing(mnt, state_dir):
            return {"ok": False, "written": 0,
                    "error": "NAS-Kopplung ungültig -- Prüfdatei fehlt oder stimmt nicht überein. "
                             "Keine Rohschlüssel übertragen (falsches/vertauschtes NAS?). "
                             "Falls das NAS bewusst gewechselt wurde: Kopplung zurücksetzen und erneut versuchen."}
        for cid, fek_b64 in keys.items():
            rel = cid.lstrip("/")
            if not os.path.isfile(os.path.join(src, rel)):
                continue
            remote_mp4 = os.path.join(mnt, rel)
            if not os.path.isfile(remote_mp4):
                continue
            sidecar = remote_mp4 + ".rawkey.json"
            if os.path.isfile(sidecar):
                continue
            try:
                payload = json.dumps({
                    "video": os.path.basename(rel),
                    "for_file": rel,
                    "algo": "AES-128-CBC (Tesla eCryptfs FEK, unsealed)",
                    "fek_b64": fek_b64,
                    "created": datetime.datetime.utcnow().isoformat() + "Z",
                }, indent=2)
                tmp = sidecar + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, sidecar)
                written += 1
            except Exception as e:
                errors.append(f"{rel}: {e}")
    finally:
        _umount(mnt)
        _op_lock.release()
    return {"ok": not errors, "written": written, "errors": errors}
