"""
File browser backend for the Hub: list / download / upload / mkdir / rename /
move / delete under the mounted Media/LightShow/Boombox partitions (Music
lives as a subfolder of Media -- see run/auto.www). Every path is confined
under FS_BASE (no traversal). The car-written partitions are mounted rw by
teslausb's autofs, so no root remount is needed here.

LightShow and Boombox are their own dedicated partitions/LUNs, not
subfolders of Media: Tesla's firmware only recognizes one of the two when
they share a partition (confirmed via marcone/teslausb#832 and
teslamotors/light-show#111), so the earlier Music+LightShow+Boombox merge
broke both features in the car and had to be partially undone. Music has
no such restriction and stays merged into Media.
"""
import os, shutil, subprocess, fcntl, time

FS_BASE = "/var/www/html/fs"   # teslausb mounts Media/LightShow/Boombox here (see run/auto.www)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
# These are indirect autofs mounts: they only appear once accessed, so a
# listdir of FS_BASE shows nothing. Probe the known names instead.
KNOWN_ROOTS = ("Media", "LightShow", "Boombox")


def _safe(rel):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    full = os.path.normpath(os.path.join(FS_BASE, rel))
    base = os.path.normpath(FS_BASE)
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("path outside root")
    return full


def roots():
    out = []
    for d in KNOWN_ROOTS:
        try:
            if os.path.isdir(os.path.join(FS_BASE, d)):   # accessing triggers autofs
                out.append(d)
        except OSError:
            pass
    return out


def listdir(rel):
    if not rel:   # top level -> the (autofs) partitions, which don't self-list
        return [{"name": d, "dir": True, "size": 0, "image": False} for d in roots()]
    full = _safe(rel)
    entries = []
    try:
        for nm in sorted(os.listdir(full), key=str.lower):
            if nm.startswith("."):
                continue
            p = os.path.join(full, nm)
            isdir = os.path.isdir(p)
            try:
                sz = 0 if isdir else os.path.getsize(p)
            except OSError:
                sz = 0
            entries.append({"name": nm, "dir": isdir, "size": sz,
                            "image": (not isdir) and nm.lower().endswith(IMAGE_EXT),
                            "audio": (not isdir) and nm.lower().endswith(AUDIO_EXT)})
    except FileNotFoundError:
        return None
    return entries


def resolve(rel):
    full = _safe(rel)
    return full if os.path.isfile(full) else None


def mkdir(rel):
    full = _safe(rel)
    os.makedirs(full, exist_ok=True)


def delete(rel):
    full = _safe(rel)
    if os.path.isdir(full):
        shutil.rmtree(full)
    elif os.path.exists(full):
        os.remove(full)


def rename(rel, newname):
    full = _safe(rel)
    if "/" in newname or newname in ("", ".", ".."):
        raise ValueError("bad name")
    os.rename(full, os.path.join(os.path.dirname(full), newname))


def move(rel, destdir):
    full = _safe(rel)
    dest = _safe(destdir)
    if not os.path.isdir(dest):
        raise ValueError("dest not a dir")
    shutil.move(full, os.path.join(dest, os.path.basename(full)))


# archiveloop's archive_lock_and_run holds this flock for its whole archive
# pass (fsck/snapshot/copy, with the gadget disconnected in between). Taking
# the same lock keeps a Hub-side write from re-attaching the drives mid-pass.
ARCHIVE_LOCK = "/tmp/teslausb_archive.lock"
GADGET_UDC = "/sys/kernel/config/usb_gadget/teslausb/UDC"


def _gadget_bound():
    try:
        with open(GADGET_UDC) as f:
            return bool(f.read().strip())
    except OSError:
        return False


def with_drives_detached(fn, lock_wait=60):
    """Run fn() with the USB drives detached from the car, then re-attach.

    The backing images are exported live as raw USB mass-storage LUNs, and
    the car caches the FAT directory it already read over USB -- it won't
    notice a file changed underneath it until the drive is unplugged and
    replugged. Every place that edits a backing image therefore brackets the
    write with disable_gadget.sh/enable_gadget.sh, as archiveloop does for
    its own steps. os.sync() before re-attaching, or the car can read the
    image while the new bytes still sit in the Pi's page cache. Drives that
    weren't attached to begin with (archiveloop between phases) are left
    detached; archiveloop re-attaches them itself."""
    fd = os.open(ARCHIVE_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.time() + lock_wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise RuntimeError("Archivierung läuft gerade – bitte in ein paar Minuten erneut versuchen")
                time.sleep(2)
        was_bound = _gadget_bound()
        if was_bound:
            subprocess.run(["/root/bin/disable_gadget.sh"], capture_output=True, timeout=60)
        try:
            result = fn()
        finally:
            os.sync()
            if was_bound:
                r = subprocess.run(["/root/bin/enable_gadget.sh"], capture_output=True, text=True, timeout=120)
        if was_bound and r.returncode != 0:
            raise RuntimeError("Geschrieben, aber die USB-Laufwerke ließen sich nicht wieder verbinden: "
                               + ((r.stdout or "") + (r.stderr or "")).strip()[-200:])
        return result
    finally:
        os.close(fd)


LOCKCHIME_MAX_BYTES = 1024 * 1024  # Tesla requires LockChime.wav <= 1 MB


def set_lockchime(rel):
    """Copy a chime file onto Boombox/LockChime.wav -- the exact file the
    car plays on lock/unlock -- overwriting it. Source must live under
    Boombox/ and already meet Tesla's own requirements for that file
    (.wav, <=1MB), since the copy just becomes the new LockChime.wav
    verbatim. boombox_disk.bin is exported live to the car as lun.3, hence
    with_drives_detached()."""
    rel_norm = (rel or "").replace("\\", "/").lstrip("/")
    if not rel_norm.startswith("Boombox/"):
        raise ValueError("Quelle muss im Boombox-Ordner liegen")
    if not rel_norm.lower().endswith(".wav"):
        raise ValueError("nur .wav-Dateien sind als LockChime zulässig")
    full = _safe(rel)
    if not os.path.isfile(full):
        raise ValueError("Datei nicht gefunden")
    if os.path.getsize(full) > LOCKCHIME_MAX_BYTES:
        raise ValueError("Datei zu groß (max. 1 MB für LockChime.wav)")
    dest = _safe("Boombox/LockChime.wav")
    tmp = dest + ".tmp"

    def write():
        shutil.copyfile(full, tmp)
        os.replace(tmp, dest)
    with_drives_detached(write)


def save_upload(destrel, filename, fileobj):
    dest = _safe(destrel)
    os.makedirs(dest, exist_ok=True)
    safe_name = os.path.basename(filename).replace("/", "_") or "upload.bin"
    out = os.path.join(dest, safe_name)
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(fileobj, f, length=1024 * 1024)
    os.replace(tmp, out)
    return safe_name
