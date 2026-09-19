# Tesla dashcam encryption (firmware 2026.20+): clips, event.json, thumb.png

A practical, reverse-engineered reference for how Tesla encrypts TeslaCam /
Sentry Mode recordings on the USB drive since software **2026.20**, what can be
decrypted off the car and what cannot, and why `event.json` / `thumb.png` behave
differently from the video clips. Written from the teslausb-fork **TeslaCam Hub**
project; findings measured on a real Model Y archive (2026-09).

If you searched for *tesla dashcam encryption*, *decrypt EncryptedClips*,
*event.json encrypted*, *`_CONSOLE`*, *eCryptfs Tesla*, *thumb.png encrypted*,
*dashcam.tesla.com key*, or *why does event.json show no reason* — this page is
for you.

## TL;DR

- Since **2026.20**, dashcam/Sentry clips are stored **encrypted** on the USB
  drive, under `TeslaCam/EncryptedClips/…`. Toggle: *Vehicle → Safety → Encrypt
  Dashcam Recordings* (on by default).
- **Video (`*.mp4`)** uses a **per-clip key wrapped for Tesla's cloud**. You can
  decrypt it locally after fetching the key from your Tesla account
  (`dashcam.tesla.com`, or the API). This is well understood and works.
- **`event.json` and `thumb.png`** use a **different** eCryptfs mode: a
  **passphrase** with the key name **`_CONSOLE`**, held **inside the car's
  console**. That key is **not** in Tesla's cloud, is not the per-clip key, and
  never leaves the vehicle — so these two metadata files are **not decryptable
  off the car**. This is why third-party viewers show encrypted events with no
  trigger reason and no thumbnail.

## The container format (both file types)

Both are eCryptfs-style per-file containers:

| Offset | Meaning |
|---|---|
| `0` | plaintext size, `uint64` big-endian |
| `8` / `12` | magic pair; `u32@8 XOR u32@12 == 0x3C81B7F5` identifies the format |
| `16` | version/flags `0x03000002` |
| `20` | page size `4096` |
| `24` | extent count `2` |
| `4096` (`0x1000`) | **wrapped-key block** — used by clips, empty for metadata |
| `8192` (`0x2000`) | encrypted payload, AES-128-CBC in 4096-byte pages |

Per-page IV: `iv = MD5( MD5(file_key) ++ ASCII(page_number) padded with zeros to
32 bytes )`, page numbers as decimal ASCII (`"0"`, `"1"`, …).

## Video clips — the cloud / public-key path

The wrapped-key block at offset `4096` carries `key_id`, an EC public key, the
VIN, a timestamp, and the wrapped File Encryption Key (FEK). To decrypt:

1. Read that block, POST it to Tesla's decrypt endpoint (`/api/1/decrypt/batch`)
   with account authentication; only metadata is sent, not the video bytes.
2. Tesla returns the base-64 AES-128 FEK.
3. Decrypt the payload locally with the page-IV scheme above.

This is exactly what `dashcam.tesla.com` and open-source tools such as
[`XGxF3/tesla-dashcam-decrypt`](https://github.com/XGxF3/tesla-dashcam-decrypt)
do, and what this project's `hub/app/ecryptfs.py` implements.

## `event.json` / `thumb.png` — the passphrase / `_CONSOLE` path

These carry an **all-zero** wrapped-key block at offset `4096` (not a bug —
they don't use the cloud path). Their key material is in the header as an
eCryptfs **Tag 3 packet** (passphrase-based):

- salt `0011223344556677`
- 65536 MD5 iterations, AES-128
- followed by a **Tag 11 literal packet naming the key: `_CONSOLE`**

Measured over 221 metadata files on one car: **one** salt, but **213 distinct
wrapped file-keys** — i.e. a random per-file key, all wrapped with the **same**
passphrase. That single passphrase is a **fixed in-console key** (`_CONSOLE`).

What it is **not** (all tested and ruled out): the per-clip cloud FEK; the VIN
or VIN-derived strings; the app/vault password; ~60 common candidate strings —
all failed against the header's passphrase signature and against known-plaintext
(`event.json` starts with `{`, `thumb.png` with the PNG magic). Conclusion: the
metadata files are **not decryptable off the vehicle**. The only realistic way
to read `reason`/`thumbnail` externally would be a future community extraction
of the fixed `_CONSOLE` key from firmware.

`event.json` (when plaintext, i.e. pre-2026.20) looks like:

```json
{ "timestamp": "2024-11-08T08:08:49", "city": "Mauritz",
  "est_lat": "51.9513", "est_lon": "7.63656",
  "reason": "user_interaction_dashcam_launcher_action_tapped", "camera": "0" }
```

`reason` values seen in the wild include `user_interaction_honk`,
`sentry_aware_object_detection`, and `sentry_aware_accel_<g-value>`; `camera`
`"0"` is the front camera.

## Related pitfall: mid-write snapshots of clips (not the metadata)

Independent of the above: a snapshot taken **while the car is still writing a
clip's key block** captures an mp4 with a valid header but an **all-zero
wrapped-key block at offset 4096**, which is then archived and can never be
decrypted. This fork guards against it in `run/make_snapshot.sh`
(`is_finalized_clip`) by only linking a clip once its key block is present.

## What the TeslaCam Hub does about it

`hub/app/nassync.py` (`mirror_event_files`):

- **Pre-encryption clips**: copies the plaintext `event.json`/`thumb.png` into
  the decrypted tree a viewer indexes.
- **Encrypted clips**: does **not** copy the undecryptable `_CONSOLE` container
  (a viewer would read it as broken JSON). Instead writes a clearly-marked
  **reconstructed `event.json`** (`"reconstructed": true`) with the timestamp
  from the folder name and an approximate location from the Hub's own decrypted
  telemetry; `reason` is honestly `unknown`. No fake thumbnail is invented.

## Should you leave car-side encryption on?

See the trade-off table in the project [README](../README.md#video-verschlüsselung-im-auto-an-oder-aus).
Short version: **on** protects the footage on the drive (key never on the
device) but loses external `event.json`/`thumb.png` and needs a Tesla account
token; **off** gives plain MP4 with full native event metadata but readable
footage on the stick.

---

*Measured 2026-09 on a Tesla Model Y, firmware ~2026.26, with a Raspberry Pi
teslausb setup archiving to a NAS. Corrections welcome via issues.*
