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
import os, shutil, subprocess

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


LOCKCHIME_MAX_BYTES = 1024 * 1024  # Tesla requires LockChime.wav <= 1 MB


def set_lockchime(rel):
    """Copy a chime file onto Boombox/LockChime.wav -- the exact file the
    car plays on lock/unlock -- overwriting it. Source must live under
    Boombox/ and already meet Tesla's own requirements for that file
    (.wav, <=1MB), since the copy just becomes the new LockChime.wav
    verbatim.

    boombox_disk.bin is also exported live as a raw USB mass-storage LUN to
    the car whenever the gadget is connected (see
    /sys/kernel/config/usb_gadget/teslausb/functions/mass_storage.0/lun.3).
    Every other place in this codebase that touches a backing image
    (archiveloop's fsck/trim/snapshot steps) disconnects the gadget first
    for exactly this reason: the car caches the FAT directory it already
    read over USB and won't notice a file changed underneath it until the
    drive is unplugged and replugged, so writing LockChime.wav while
    connected updates the bytes on disk but the car keeps playing the old
    chime. Bracket the copy the same way."""
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
    subprocess.run(["/root/bin/disable_gadget.sh"], capture_output=True)
    try:
        shutil.copyfile(full, tmp)
        os.replace(tmp, dest)
    finally:
        subprocess.run(["/root/bin/enable_gadget.sh"], capture_output=True)


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
