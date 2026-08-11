"""
Scans the encrypted tree and prepares key-fetch requests.

Phase separation:
  - needs KEY     : encrypted file with no entry in the key store
  - needs DECRYPT : has a key but no decrypted output yet

Reading headers (8 KB) over SMB costs ~140 ms/file -> done in parallel. Selecting
which files need a key/decrypt at all is done via stat (no header read).
"""
import os, glob, struct, base64
from concurrent.futures import ThreadPoolExecutor

MAGIC = 0x3C81B7F5
HEADER_SIZE = 8192
MEDIA_EXT = (".mp4", ".json")  # .json: event.json -- eCryptfs-wrapped on current firmware, same as the clips
SCAN_WORKERS = 16


def is_ecryptfs(head: bytes) -> bool:
    if len(head) < 28:
        return False
    m1 = struct.unpack_from(">I", head, 8)[0]
    m2 = struct.unpack_from(">I", head, 12)[0]
    return (m1 ^ m2) == MAGIC


def parse_wrapped_key(head: bytes) -> dict:
    """Wrapped-key section @4096: key_id|65B-EC-PubKey|17B-VIN|u64-ts|44B-wrapped."""
    c = 4096
    key_id = struct.unpack_from(">I", head, c)[0]; c += 4
    public_key = head[c:c + 65]; c += 65
    vin = head[c:c + 17].decode("ascii", "replace"); c += 17
    timestamp = struct.unpack_from(">Q", head, c)[0]; c += 8
    wrapped_key = head[c:c + 44]
    if (vin and vin[0] == "\x00") or public_key[0] != 4:
        raise ValueError("invalid wrapped-key section")
    return {"vin": vin, "key_id": key_id, "timestamp": timestamp,
            "wrapped_key": base64.b64encode(wrapped_key).decode(),
            "public_key": base64.b64encode(public_key).decode()}


def clip_id(src_dir: str, path: str) -> str:
    return os.path.relpath(path, src_dir).replace("\\", "/")


def media_files(src_dir: str) -> list:
    out = []
    for path in glob.glob(os.path.join(src_dir, "**", "*"), recursive=True):
        if os.path.splitext(path)[1].lower() in MEDIA_EXT and os.path.isfile(path):
            out.append(path)
    return out


def files_needing_key(src_dir: str, keys: dict) -> list:
    return [p for p in media_files(src_dir) if clip_id(src_dir, p) not in keys]


def files_needing_decrypt(src_dir: str, out_dir: str, keys: dict) -> list:
    res = []
    for p in media_files(src_dir):
        cid = clip_id(src_dir, p)
        if cid in keys and not os.path.exists(os.path.join(out_dir, cid)):
            res.append(p)
    return res


def counts(src_dir: str, out_dir: str, keys: dict) -> dict:
    files = media_files(src_dir)
    keyed = decrypted = 0
    for p in files:
        cid = clip_id(src_dir, p)
        if cid in keys:
            keyed += 1
        if os.path.exists(os.path.join(out_dir, cid)):
            decrypted += 1
    return {"encrypted": len(files), "keyed": keyed, "decrypted": decrypted,
            "need_keys": len(files) - keyed,
            "need_decrypt": sum(1 for p in files
                                if clip_id(src_dir, p) in keys
                                and not os.path.exists(os.path.join(out_dir, clip_id(src_dir, p))))}


def _read_head(path):
    try:
        with open(path, "rb") as f:
            return path, f.read(HEADER_SIZE)
    except OSError:
        return path, None


def scan_items(src_dir: str, keys: dict, limit: int = 0) -> list:
    """Items (id + wrapped key) for files WITHOUT a key – for Direct API or bookmarklet."""
    pend = files_needing_key(src_dir, keys)
    if limit:
        pend = pend[:limit]
    items = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        for path, head in ex.map(_read_head, pend):
            if not head or not is_ecryptfs(head):
                continue
            try:
                wk = parse_wrapped_key(head)
            except Exception:
                continue
            wk["id"] = clip_id(src_dir, path)
            items.append(wk)
    return items


def normalize_results(payload) -> dict:
    """payload: {"results":[{id,key}]} | [{id,key}] | {id:key} -> {id:key}."""
    if isinstance(payload, dict) and "results" in payload:
        payload = payload["results"]
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if k and v}
    return {r.get("id"): r.get("key") for r in payload if r.get("id") and r.get("key")}
