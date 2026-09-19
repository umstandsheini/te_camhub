# TeslaCam Hub

> **Self-hosted Tesla dashcam & Sentry Mode hub for the Raspberry Pi** — a
> [teslausb](https://github.com/marcone/teslausb) fork with a single web UI:
> decrypts Tesla **2026.20+ encrypted dashcam clips** (`EncryptedClips`),
> synced multi-camera viewer with telemetry HUD and GPS map, NAS archiving,
> an encrypted key vault, plus Home Assistant (MQTT) and BLE vehicle access.
>
> 📄 **Findings — how Tesla encrypts dashcam clips (and why `event.json` /
> `thumb.png` can't be decrypted off the car):**
> [doc/tesla-dashcam-encryption.md](doc/tesla-dashcam-encryption.md).
> Keywords: tesla dashcam encryption, decrypt EncryptedClips, event.json
> encrypted, eCryptfs `_CONSOLE`, dashcam.tesla.com key, sentry mode.

A fork of [teslausb](https://github.com/marcone/teslausb) that replaces the old
web interface (nginx + cgi-bin + iframe) with a single Python service (the
"Hub"): HTTPS + login, a video viewer with on-demand decryption, a file browser,
NAS sync, diagnostics/settings, and optional BLE / Home Assistant integration —
all from one UI. The teslausb core (USB gadget, snapshots, archiving) is
unchanged; the Hub only configures and drives it.

## Companion viewer: Te_FITI

TeslaCam Hub and **[Te_FITI](https://github.com/umstandsheini/Te_FITI)** are the
two halves of one pipeline — the **capture/archive side in the car** and the
**viewer/decryptor side at home** — and work best together:

| | **TeslaCam Hub** (this project) | **[Te_FITI](https://github.com/umstandsheini/Te_FITI)** |
|---|---|---|
| Runs on | Raspberry Pi **in the car** | Home Assistant add-on, **on the home/NAS side** |
| Role | emulate the USB drive, snapshot & **archive** clips to the NAS, fetch per-clip **decryption keys** from Tesla, write reconstructed `event.json` | **read** the archived clips from the NAS, **decrypt** them, **view** them |
| Highlights | USB gadget, NAS sync, encrypted vault, BLE / Home Assistant, its own built-in viewer | six-camera player, telemetry HUD, GPS map, trips, event browser |

**How they connect:** the Hub archives `TeslaCam/EncryptedClips/…` to the NAS and,
optionally, places each clip's key next to it there — either sealed
(`*.key.json`) or, if you enable it, as a plain raw key (`*.rawkey.json`,
`NAS_RAW_KEYS`). Te_FITI reads the same NAS share, decrypts the clips (with its own
one-time Tesla login, or with those raw keys) and plays them back. The Hub also
writes a stand-in `event.json` into the decrypted tree so Te_FITI can show
encrypted events despite the unreadable `_CONSOLE` metadata (see
[doc/tesla-dashcam-encryption.md](doc/tesla-dashcam-encryption.md)). You can run
either alone — the Hub has a viewer, Te_FITI can read any teslausb NAS — but
together they cover the whole path from the car to the couch.

## ⚠ Built for private personal use only

This project was created **exclusively for my own use**, tailored to my own
hardware, my own network and my own workflow. It is **publicly visible, but not
intended as a finished product for others**.

Anyone may take the code, modify it and use it for themselves — but:

- **No warranty.** Neither that anything works, nor that it is secure, correct
  or fit for any particular purpose.
- **No support.** I don't answer questions, don't fix other people's bugs, and
  take no responsibility for damage, data loss or any other consequences of use
  — including anything to do with remote vehicle access (BLE keys).
- **Entirely at your own risk.** Whoever runs this software does so completely
  at their own responsibility, especially regarding vehicle access and the
  credentials/keys stored on the stick.

## ⚠ Very early stage of development

The project is **fresh, under active development and unaudited**. It may contain
security holes, bugs, half-finished features and unexpected behavior. Nothing
here has been reviewed by anyone other than me. Before any productive use: read
the code, understand it yourself, test it yourself.

## Required hardware

| Part | Recommendation | Notes |
|---|---|---|
| **Raspberry Pi** | Raspberry Pi 4 Model B, 2 GB RAM or more | Tested with the Pi 4 (1 GB RAM) — enough, but tight. The Pi 4 can draw power through its USB-C port and present itself to the car as a USB drive at the same time. Other models are untested; the Pi 5 and the Pi Zero 2 W (512 MB RAM) do not fit. |
| **Storage** | USB 3 SSD with UASP, 128 GB or more | Tested: Netac Z Slim 250 GB. The Pi 4 boots directly from USB (no microSD card inserted). Alternatively a "High Endurance" microSD from 64 GB — the dashcam writes constantly, ordinary cards wear out fast. |
| **Cable to the car** | USB-A-to-USB-C (or USB-C-to-USB-C), with data lines | Charge-only cables don't work. Connect to the USB port where the dashcam stick normally goes. |
| **Power supply** | Official Raspberry Pi USB-C power supply (15 W) | Only for setup (20–40 minutes) and for desk updates; in the car the car powers the Pi. |
| **Home Wi-Fi** | – | Within range of the parking spot, so clips go to the NAS. |
| Optional | NAS with SMB share, Home Assistant with MQTT, USB Wi-Fi dongle | The NAS receives the recordings; a USB Wi-Fi dongle helps if the built-in Wi-Fi is too weak in the garage. BLE for the car is built into the Pi. |
| For flashing | A PC with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) or balenaEtcher | – |

## Installation

There is no ISO for the Raspberry Pi — the equivalent is a **disk image
(`.img.xz`)** that you write to the storage with a flashing tool the same way. It
contains Raspberry Pi OS Lite (Bookworm, 64-bit) and this project; the actual
setup runs on the Pi itself on first boot.

1. **Get the image:** download `te_camhub-<version>.img.xz` from the
   [releases page](https://github.com/umstandsheini/te_camhub/releases/latest)
   (the `.sha256` file next to it holds the checksum).
2. **Flash it:** open Raspberry Pi Imager → device "Raspberry Pi 4" → OS "Use
   custom" → pick the `.img.xz` → pick the SSD or microSD → when asked about
   **customization choose "No"** (otherwise Imager writes its own first-run
   script that replaces the setup).
3. **Enter credentials** — one of two ways:
   - **Way A, config file:** re-insert the storage after flashing. On the
     `bootfs` volume open `teslausb_setup_variables.conf` in a text editor,
     enter at least `SSID` and `WIFIPASS`; NAS, drive sizes and device name are
     optional. Save, eject.
   - **Way B, setup hotspot:** change nothing. After the first boot (step 4) the
     Pi opens the Wi-Fi **`TeslaCam-Setup`** (password `teslacam-setup`) after
     2–3 minutes. Connect a phone — the setup page appears by itself, otherwise
     open `http://192.168.4.1`. Enter Wi-Fi, NAS and device name, save.
4. **Boot:** storage into the Pi, start the Pi **on the power supply** (not in
   the car yet). Setup takes 20–40 minutes with several reboots: create
   partitions and USB drives, load packages, install the Hub. Progress:
   `teslausb-headless-setup.log` on `bootfs`.
5. **Set up the vault:** on the home Wi-Fi open `https://teslausb.local` (or the
   chosen device name, or the IP address from the router). Accept the
   self-signed certificate warning, set a **vault password** — it is both the
   login and protects keys, tokens and the trip log (see below). Without this
   password encrypted recordings are unreadable.
6. **Into the car:** connect the Pi to the car's USB port with the data cable.
   The car recognizes the drives after about 6 seconds.

**If something stalls:** if the Pi can't find the configured Wi-Fi (typo in the
password), it reopens `TeslaCam-Setup` after a few minutes with the previous
entries. Fast continuous blinking of all LEDs means "setup failed" — the cause
is in the log on `bootfs`; after fixing it, just reboot and setup continues. The
setup hotspot uses a publicly known password and is only active until setup
finishes; to avoid it entirely, use Way A.

**SSH** after installation is key-only: put `SSH_PUBKEY` in the config file (user
`pi`). Without a key, `pi` gets a random password nobody knows — everything is
operated through the Hub UI.

## Updates

The Hub asks GitHub every 6 hours for the latest release (releases of this
repository). If there is a newer version, a hint appears after login, the nav
shows "Update" next to **Diagnostics**, and the **Hub software** card shows the
version and changes. "Check for updates" asks right away.

"Install update" (home Wi-Fi only):

1. **Backup** to `/backingfiles/hub-backups/hub-<old-version>-<time>.tar.gz`: the
   program (`/opt/teslacam-hub`, `/root/te_camhub`, `/root/bin`), systemd units,
   config, Wi-Fi profiles, TLS key and the state folder with vault and trip log
   (without the regenerable cache of encrypted thumbnails). The newest three
   backups are kept. They contain the config's credentials in the clear — just
   like the config itself on the same SSD.
2. **Download** the package `te_camhub-<version>.tar.gz` with its SHA-256
   checksum, verify, unpack; every Python file must compile with the Pi's
   Python.
3. **Install** in a separate systemd unit (`teslacam-hub-update`), because
   `install.sh` restarts the Hub: replace the source tree, run `hub/install.sh`,
   wait until the Hub answers again. Log in again afterward.
4. **Automatic rollback:** if `install.sh` fails or the Hub doesn't answer
   within 4 minutes, the script restores the program and units from the backup
   and restarts the Hub. Log: `/mutable/hub-update.log` and the card on the
   Diagnostics page.

The update swaps the Hub program and the teslausb scripts; the OS updates its own
"Operating-system updates" card. An already-running `archiveloop` uses the new
version from the Pi's next start.

**Restore a backup by hand** (via SSH):

```bash
sudo mount / -o remount,rw
```
```bash
sudo tar -xzf /backingfiles/hub-backups/<file>.tar.gz -C / opt/teslacam-hub root/te_camhub root/bin
```
```bash
sudo systemctl restart teslacam-hub && sudo mount / -o remount,ro
```

Config or vault can be restored individually the same way
(`root/teslausb_setup_variables.conf`, `backingfiles/decrypt-viewer-state`).

## Publishing a new version

A tag of the form `vYYYY.MM.DD` (multiple on one day: `vYYYY.MM.DD.N`) triggers
the workflow `.github/workflows/release.yml`. It builds the package for the
update feature (`te_camhub-<tag>.tar.gz`, with `VERSION` = tag), the install
image (`te_camhub-<tag>.img.xz`) and the checksums, and creates the GitHub
release. From then on all installed Hubs offer the update.

```bash
git tag v2026.09.17 && git push origin v2026.09.17
```

The image can also be built locally (Linux or WSL, as root): `sudo
tools/build-image.sh` takes the committed state (`git archive HEAD`), downloads
the base image once into `.image-cache/`, checks its SHA-256 and writes
`dist/te_camhub-dev-<commit>.img.xz`.

## Acknowledgements / sources

This project builds on the work of others:

- **[marcone/teslausb](https://github.com/marcone/teslausb)** — the base of this
  fork: USB gadget emulation, snapshot/archiving pipeline, network/AP setup, BLE
  basics. Originally born from
  [this Reddit thread](https://www.reddit.com/r/teslamotors/comments/9m9gyk/build_a_smart_usb_drive_for_your_tesla_dash_cam/).
- **Te_FITI** — model for the viewer feature set (synchronized multi-camera
  playback, event seek, GPS map, telemetry HUD) and the starting point for the
  eCryptfs / crypto modules.
- **[yoziru/esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble)** —
  reference for the multi-role BLE key pairing (separate keys per role instead
  of one full-access owner key).
- **[teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)**
  — official Tesla tools (`tesla-control`, `tesla-keygen`) for BLE vehicle
  commands.
- **[MikeBishop/tesla-vehicle-command-arm-binaries](https://github.com/MikeBishop/tesla-vehicle-command-arm-binaries)**
  — prebuilt ARM binaries of the above tools for the Raspberry Pi.

## BLE vehicle access: what the `charging_manager` role really allows

The Hub deliberately pairs only a single, restricted BLE key with the Tesla role
`charging_manager` (not `owner`/`driver` with full access, see
[Acknowledgements](#acknowledgements--sources) → esphome-tesla-ble). Tesla
documents roles only vaguely
(["can authorize commands related to charging"](https://github.com/teslamotors/vehicle-command/blob/main/pkg/protocol/protocol.md#roles))
— so what that means in practice was tested on 2026-07-10 **empirically against a
real vehicle** (`tesla-control` over BLE, result recorded in
[`hub/app/diag.py`](hub/app/diag.py), `BLE_READS`/`BLE_ACTIONS`). The Hub UI shows
this list under **Vehicle (BLE)** once a key is paired.

**Allowed (confirmed by the vehicle):**

- **Reads — unrestricted, all categories:** charge state, locks/doors, climate,
  tire pressure, location, drive state, media (details), charge/precondition
  schedule, software-update status, parental controls, VCSEC base state (works
  even while the vehicle sleeps), list all enrolled keys, ping.
- **Controls:** start/stop charging, set charge limit/amps, cancel charge
  schedule, wake the vehicle (only wakes, doesn't keep awake — see
  [below](#waking-is-not-keeping-awake-measured-2026-09-13)), accessory power
  on/off (per Tesla does not apply to the dashcam/USB data port, confirmed in
  practice).

**Rejected** (refused by the vehicle itself with `INSUFFICIENT_PRIVILEGES` or
`GENERICERROR_UNAUTHORIZED`, not just refused client-side): all media control
commands (volume, track, play/pause, favorites), lock/unlock, windows, climate
on/off, Sentry mode, tonneau, steering-wheel heating — plus charge-port
open/close, honk and flash lights: still accepted on the morning of 2026-07-10,
rejected a few hours later with the same role and the same key (Tesla itself
notes that role permissions can change).

**Deliberately never tested automatically** (the command exists technically, but
an unexpected success would be riskier than the insight is worth): remote start
(`drive`, risk of moving the vehicle), add/remove/rename keys (risk of losing
your own access), start/cancel software update, wipe guest data, open frunk (no
close command), trunk (close not available on all models), seat heating/target
temperature (need parameters that can't be chosen safely), valet/guest mode, set
parental controls, low-power mode, add/remove complex schedules. (The former
detailed list with reasoning no longer exists in the code, see the git history of
[`hub/app/diag.py`](hub/app/diag.py).)

**Security note:** the private key sits unencrypted on the stick
(`/root/.ble/<name>/key_private.pem`). If the stick is lost/stolen: remove the
BLE key immediately in the Tesla app, enable PIN-to-Drive in the vehicle.

### Waking is not keeping awake (measured 2026-09-13)

The car only powers the Hub's USB port while it is awake. Whether a locked,
parked car can be kept awake via BLE `wake` was measured live through the BLE
proxy in the garage
([esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble), the "Wake up"
button — as far as researched, the same wake command as `tesla-control wake`):

| Attempt | Result |
|---|---|
| locked, no commands | sleeps after 5:56 min |
| `wake` every 120 s, 14 min | slept 3×, awake only 1:01 to 3:55 min each time |
| `wake` every 60 s, 8 min | slept 2×, awake only 2:31 and 3:06 min |

- `wake` wakes a sleeping car, but not always on the first try.
- `wake` sent to an **awake** car does not extend its awake time: three times it
  slept 20–41 s after such a command. So a shorter interval doesn't help either.
- This matches upstream:
  [vehicle-command#397](https://github.com/teslamotors/vehicle-command/issues/397)
  reports the same; teslausb therefore uses `charge-port-close` as the BLE nudge
  — and that is exactly what the vehicle rejects for `charging_manager` (see
  above).

**Consequence:** neither "keep the car awake" nor "sync before it sleeps"
currently keeps the car reliably awake, both use `wake`. The decision logic of
the sync hold ([`hub/app/synchold.py`](hub/app/synchold.py)) stays correct, only
the mechanism needs replacing. Proven alternatives: Sentry mode (documented
upstream; needs a key allowed to set Sentry — `charging_manager` isn't) or an
unlocked car (usually holds, but not always).

## Claude assistant: light shows and Boombox sounds on the go

Menu item **Assistant**: a chat where Claude searches the web for light shows and
Boombox sounds, downloads them to the Pi, checks them and — after a click to
confirm — writes them to the USB drives. Meant for the road via the phone
hotspot, when there's no laptop to copy with.

- Runs on the Claude API (`claude-opus-5`, billed per use, separate from a Claude
  subscription). The API key is stored encrypted in the vault, not in the config
  file. If the model refuses a request, Anthropic's default fallback model takes
  over server-side (`fallbacks: "default"`).
- Web search and page fetch run at Anthropic. The Pi only runs the tool loop and
  a few tightly-scoped tools: download into a staging area
  (`/backingfiles/hub-assistant-staging`), check, install, remove. No shell, no
  arbitrary paths.
- Install and remove always wait for "Run" in the browser. Writing happens with
  the drives briefly detached from the car and under archiveloop's archive lock
  (`files.with_drives_detached`).
- Downloads to local or private-network addresses are refused, even after
  redirects — a web page must not be able to send the assistant into the home
  network over WireGuard.
- Validation follows [teslamotors/light-show](https://github.com/teslamotors/light-show):
  FSEQ v2 uncompressed, 48 or 200 channels, at least 15 ms step, at most 4 h;
  audio mp3/wav, 44.1 kHz recommended. Boombox: the car only offers the first
  five files (alphabetical).
- Why not Claude Code directly on the Pi: it needs 4 GB RAM (this Pi 4 has 1 GB)
  and would effectively be a root shell in the browser.

## Operating-system updates (Diagnostics page)

- "Check for updates" is safe at any time: `apt-get update` into throwaway lists
  under `/tmp`, then a simulation; the system partition stays read-only. Also
  shows security updates and an earlier interrupted install (`dpkg --audit`).
- "Install now" only on the home Wi-Fi and not during an archiving run
  (archiveloop's lock): make `/` and `/boot/firmware` writable, `dpkg --configure
  -a`, `apt-get upgrade --with-new-pkgs` (never removes packages, keeps config
  files), `apt-get clean`, back to read-only. Log: `/mutable/os-update.log`.
  While this runs, the Hub refuses every other action that remounts `/` itself.
- **Power:** even at home the Pi runs off the car's USB power, which is cut once
  the car sleeps. An update interrupted midway is the one case that can really
  damage the system — so keep the car awake (Sentry mode) or update on the power
  supply.

## Wi-Fi networks (Settings)

The home Wi-Fi (`SSID`/`WIFIPASS`) always has priority. Below it a sortable list
of further networks (phone hotspots etc.), stored as `WIFI_NETWORKS` in the
config (like the other credentials: never in the clear in the UI, included in the
backup export, deleted on vault reset). Each network becomes a NetworkManager
profile `TESLAUSB_WIFI_<n>` with priority -10, -11, … The former single hotspot
(`HOTSPOT_SSID`) is adopted as the first entry on the first change.

## Encrypted thumbnails and telemetry

Decrypted videos live only in RAM (`/dev/shm`) and vanish when the vault is
locked and on every reboot. Thumbnails and telemetry (GPS, speed, gear) the Hub
additionally keeps **encrypted on the SSD**
(`/backingfiles/decrypt-viewer-state/derived/`): sealed with the vault master key
(AES-256-GCM, the same scheme as the key files next to the clips), telemetry
compressed first, filenames only a hash of the clip id. After login the overview
and map points are then instantly there, without re-decrypting every clip (~36
MB); a removed stick still stays worthless. Telemetry for the map points is read
straight from the copy, not put into RAM; only what the player is currently
playing goes into RAM. Copies older than 120 days are removed, all of them on
vault reset.

## Wi-Fi selection and access point (Settings → Wi-Fi networks)

Two traps, both hit in the car:

- **NetworkManager doesn't switch back on its own.** Priority only decides
  *while* it is picking a network. Once connected to a phone hotspot, the Pi
  stays there even when the home Wi-Fi is back in range (2026-09-18: online
  through a phone in the car, but invisible at home — no access, no NAS sync, no
  Home Assistant data).
- **The Pi's own access point blocks the radio.** Without a USB Wi-Fi dongle the
  access point (`ap0`) and the client (`wlan0`) share one radio; the access point
  pins the client to its channel. A phone hotspot on another channel is then
  barely reachable.

`hub/wifi-watch.sh` (timer, every minute) therefore handles both jobs — it
replaces the earlier separate watchers for AP fallback and home Wi-Fi, which got
in each other's way:

1. **Known networks, best first:** home Wi-Fi, then the list from Settings in its
   order.
2. Connected to the best reachable one → nothing to do, access point off.
3. Connected to a worse one → switch (covers "back home" and "better hotspot in
   range").
4. Not connected at all → first take the access point down (free the radio),
   then scan and connect to the best known network.
5. Three runs in a row with nothing known found → access point on, as a way in
   when nothing else works.

The home Wi-Fi also counts as reachable when the car, per its last known
position, is in the **home zone** (`HOME_LAT`/`HOME_LON`, radius `HOME_RADIUS_M`,
default 150 m) or — as long as no zone is known — when its profile is a
**hidden** network that can never show up in a scan. The Hub learns the zone
itself once it is on the home Wi-Fi and knows a position from the car
(`home_zone_loop` in `server.py`); the position sits unencrypted in
`last_location.json` — it is the parking spot, not the route; the route stays
encrypted.

A failed connection attempt pauses for 10 minutes. By default the access point is
**fallback only** (`AP_FALLBACK_ONLY=true`); `false` keeps it up permanently,
which only makes sense with a USB Wi-Fi dongle (a second radio). The watcher can
be turned off entirely with `HOME_WIFI_PREFER=false`. Log:
`/mutable/home-wifi.log`.

## Event data for the decrypted clips

For each Sentry/Saved event the car writes `event.json` (trigger, time, position)
and `thumb.png` next to the videos. teslausb archives both reliably to
`TeslaCam/EncryptedClips/…` (measured 2026-09-18: 110 of 110 event folders had
both files) — they just never land in the decrypted branch `decrypted/…` that a
viewer like Te_FITI indexes (0 of 548). So nothing is lost, the files simply
don't travel.

**Important — this metadata is itself encrypted, unlike the videos.** The videos
use the cloud path (wrapped key in the header at offset 4096, retrievable from
Tesla with the account token — that's how the Hub decrypts them). `event.json` /
`thumb.png` instead use the eCryptfs **passphrase path** (Tag-3 packet, salt
`0011223344556677`, 65536 MD5 rounds, AES-128) with the key name **`_CONSOLE`** —
an in-console vehicle key. It is not at Tesla, isn't handed out over any
interface, and can't be obtained off the car. So these two files are **not
decryptable**, see [doc/tesla-dashcam-encryption.md](doc/tesla-dashcam-encryption.md)
and the open item below.

`nassync.mirror_event_files()` (runs in the NAS sync) makes the best of it
without needing the key:

- **Unencrypted old clips** (before firmware 2026.20): `event.json` / `thumb.png`
  are plaintext and get copied — never overwrite, never delete.
- **Encrypted clips:** the `_CONSOLE` container is **not** copied (a viewer would
  read it as broken JSON). Instead a clearly-marked **reconstructed
  `event.json`** is written (`"reconstructed": true`) with the timestamp from the
  folder name and, if decrypted telemetry is present, an approximate position.
  The trigger reason is honestly missing (`"unknown (reconstructed)"`).
  `thumb.png` is not invented.
- **Self-healing:** an earlier version accidentally copied the raw containers into
  `decrypted/`; such containers are removed on the next run. Live run 2026-09-19:
  243 containers removed, 125 stand-ins.

**Open item — the `_CONSOLE` key:** as of 2026-09-19 the key is neither public
(internet/GitHub research: all tools decrypt only the videos), nor guessed (~60
candidates incl. VIN and vault password tested against the header signature), nor
interceptable (it is created and stays in the vehicle console, never crosses
USB/BLE/cloud). The only realistic path would be a future community extraction
from firmware — the passphrase path is already implemented in the decryption
module, only the passphrase is missing. Until then the reconstructed `event.json`
stands. Candidate for later: infer the reason from our own telemetry
(acceleration spike → `sentry_aware_accel_…`, folder type → Sentry vs. manual).

## Video encryption in the car: on or off?

Since firmware **2026.20** the car encrypts dashcam/Sentry recordings on the
storage by default. Toggle under **Vehicle → Safety → "Encrypt Dashcam
Recordings"**. The decision is a real trade-off — here the weighing specifically
for this setup (Hub + NAS + external viewer).

| Aspect | Encryption **ON** (default) | Encryption **OFF** |
|---|---|---|
| **Videos if the stick/Pi is stolen** | Unreadable on the SSD — the key is **not** on the Pi, it comes from Tesla. The strongest protection there is here. | Plain MP4 — whoever has the storage sees everything (people, plates, home address…). A privacy risk. |
| **`event.json` / `thumb.png`** | **Also encrypted, but with the console key `_CONSOLE`** — not decryptable off the car (see the section above). Trigger reason and original thumbnail are lost to any external viewer; the Hub only provides a reconstructed `event.json`. | Plaintext — **full trigger reason, original thumbnail, position**. Te_FITI & co. show everything. |
| **Playback / effort** | Clips need key fetch + decryption (Hub or dashcam.tesla.com). Needs a **Tesla account token** and, when fetching new keys, internet. | Clips play immediately, everywhere, without an account, without an online fetch. Simpler chain. |
| **Snapshot risk** | A snapshot mid-write can produce a **permanently undecryptable** clip (empty key block). teslausb guards against it (`make_snapshot.sh`), but it stays a failure source. | Gone — a half-written MP4 is short at worst, never "broken forever". |
| **Pi load (1 GB RAM)** | Decryption costs CPU/RAM; historically an OOM source on the small Pi (mitigated, but real). | No decryption needed. |
| **Long-term dependency** | Old clips keep needing Tesla's key service. If Tesla changes/removes it, they could become unreadable. | No dependency on Tesla. |

**Important for this setup — the encryption only protects the Pi's SSD, not the
whole chain:**

- The **NAS** holds the recordings **decrypted** anyway (`decrypted/`), and with
  raw keys enabled (`NAS_RAW_KEYS`) the plaintext keys next to them too. So the
  "unreadable if stolen" benefit applies to the Pi, **not** to the NAS — there
  the content is open either way.
- With encryption on, the Hub needs the **Tesla account token** (in the vault)
  and must fetch keys online. Without that, new clips stay unreadable.
- The only *content* lost to encryption is the **`event.json`** (trigger reason +
  thumbnail) — everything else can be fully restored with the key.

**Rule of thumb:**

- **Leave encryption ON** if your main scenario is "the Pi/stick is stolen from
  the car" and protecting the video content on the device matters more to you
  than complete event metadata. The stand-in `event.json` softens the loss.
- **Turn encryption OFF** if you value **complete event data** (reason,
  thumbnail), want to keep the flow simple, and theft protection sits elsewhere —
  e.g. the Pi is hidden/secured and the NAS is on a secured home network. Then
  the whole chain works without a Tesla account, without an online fetch and
  without the snapshot pitfall.

Both work with the Hub. With **ON**, key fetch, the stand-in `event.json` and the
encrypted derivatives kick in; with **OFF** everything is plaintext and the
viewer shows native event data. (Sources: [Not a Tesla App](https://www.notateslaapp.com/news/4225/tesla-enables-dashcam-clip-encryption-in-update-202620), [Drive Tesla Canada](https://driveteslacanada.ca/news/tesla-2026-20-dashcam-encryption-parental-controls/).)

## "Is the Hub at the car?" (Vehicle page)

From the USB side the Pi can't tell which car it is plugged into — the gadget
only knows that *some* host mounted the drives. The only thing that identifies
the vehicle is the BLE pairing: `tesla-control` talks to exactly the configured
VIN, authenticated with the key that this vehicle accepted. `presence.py`
therefore sends a short `ping` every 10 minutes and shows the result on the
Vehicle page, in the event log (only on change) and as the HA sensor "At the car
(BLE)".

What it proves: the paired vehicle is within Bluetooth range, i.e. a few meters
away. What it does **not** prove: that the Hub is plugged in — and a sleeping or
distant car simply doesn't answer. A missing answer is therefore not a theft
alarm. Three failures in a row are needed before the state flips to "not
confirmed"; during a drive the BLE reads that run anyway are enough. `ping` does
not wake a sleeping car.

## WireGuard watchdog

With `WG_ALLOWED_IPS=0.0.0.0/0,::/0` (the default for "VPN home") wg-quick routes
**all** internet traffic into the tunnel and sets the peer's DNS server. If the
peer doesn't answer, the Pi is left without name resolution and without internet
— while the home network keeps working, so it doesn't look broken from the
outside. On 2026-09-18 exactly that happened: never a handshake, 177 KB sent, 0
received; the Hub reached neither GitHub nor Tesla, and the dead resolver entry
`192.168.6.1` survived the tunnel itself (resolvconf record `wg0`).

`hub/wg-watch.sh` (timer, every 2 minutes) stops such a tunnel: no handshake for
180 s **and** a full tunnel → stop `wg-quick`, remove the resolvconf record, retry
every 30 minutes. A split tunnel (no default route) is left alone, because a dead
one costs nothing there. `WG_ENABLED=false` turns the watchdog off entirely. Log:
`/mutable/wg-watch.log`.

## GPS trip log (encrypted)

The blackbox records position, heading, odometer and gear every 10 s while
driving — a movement profile. Since 2026-09-15 none of it lands in the clear on
the SSD. Because the vault is almost always locked while driving (every power cut
locks it), the Hub encrypts with a key pair: the public key sits next to the
trips (`blackbox/trips.pub`) and suffices for writing, the private one (RSA-3072)
is in the vault. Each trip file (`*.tbx`) gets its own AES-256 key per Hub start,
each point is appended encrypted individually with AES-256-GCM — a power cut costs
at most the point being written. Trip list, GPX export and the NAS upload
therefore need the unlocked vault; the km figure in the "trip ended" event-log
line comes from RAM. The key pair is created on the first login after the update;
points recorded before that live only in RAM. Older trips in plaintext (`*.jsonl`)
are encrypted on the first login, the plaintext overwritten and deleted (on an
SSD the overwrite is not guaranteed). On vault reset the encrypted trips are
deleted. On the NAS the trips remain as readable GPX files (`Fahrten/`).

## If the Pi is stolen

The SSD itself is not encrypted — the Pi has to boot in the car without input and
reach Wi-Fi and the NAS. So whoever removes it reads in the clear: NAS user and
password, the Wi-Fi passwords (home Wi-Fi, hotspots, own access point), the MQTT
access, the BLE keys for the car, the VIN, and the password hashes of the user
`pi` and of Samba. Worthless without the vault password: clip keys, Tesla account
token, Anthropic key, thumbnails, telemetry from the videos and the GPS trip log;
the videos were already encrypted by the car, decrypted videos live only in RAM.
But with the Wi-Fi and NAS passwords a thief within range of the house gets into
the home network and onto the NAS — where, depending on setup, there are
decrypted videos (`decrypted/`), unencrypted raw keys (`*.rawkey.json`) and the
trips as GPX.

After a theft you should:

1. Change the NAS, Wi-Fi and MQTT passwords.
2. Delete both BLE keys from the car's key list.
3. Change the Tesla password.

## Boot times (Diagnostics page)

The Hub measures every start of the Pi from the persistent journal and logs it to
`boottimes.jsonl` in the state folder (`/backingfiles/decrypt-viewer-state/`) —
also retroactively for every boot the journal still knows, and across journal
rotation. Measured in seconds from power-on: SSD mounted, USB gadget bound,
**drives mounted by the car**, Wi-Fi connected, Hub reachable, systemd done
(kernel/userspace) — plus whether the previous run shut down cleanly or simply
lost power (the normal case in the car). Shown as a table on the Diagnostics
page, one entry per boot in the event log, and the HA sensors "Start: drives
ready" and "Start: Hub ready". Reference after the rebuild on the Netac SSD:
drives after ~6 s, Wi-Fi ~10 s, Hub ~16 s.

## Clips deleted on the NAS (Settings → NAS)

teslausb keeps a list of the files it has already copied to the NAS
(`/mutable/sentry_files_archived`) and never copies any of them again. So whoever
deletes clips on the NAS to save space does not get them re-uploaded — as long as
the clip is still in the local snapshots, its list entry holds. The Hub evaluates
this: a clip that was already transferred and is no longer on the NAS (not under
`decrypted/` or `broken/` either) shows up as "🗑 deleted on NAS" and counts as
done for coverage (overview, HA sensor "NAS archiving"). The switch "Don't
re-upload clips deleted on the NAS" (`NAS_SKIP_DELETED`, default: on) reverses it:
turned off, the Hub takes such files off the list under the archive lock, and the
next archive run transfers them again. If the Hub finds no clip at all on the NAS,
that counts as a wrong path, not "everything deleted" — then nothing is marked and
nothing is re-uploaded.

## Original teslausb

Everything below this line is the original teslausb documentation and concerns
the unchanged core the Hub builds on.

Raspberry Pi and other [SBCs](## "Single Board Computers") can emulate a USB drive, so can act as a drive for your Tesla to write dashcam footage to. Because the SBC has full access to the emulated drive, it can:

- automatically copy the recordings to an archive server when you get home
- hold both dashcam recordings and music files
- automatically repair filesystem corruption produced by the Tesla's current failure to properly dismount the USB drives before cutting power to the USB ports
- retain more than one hour of RecentClips (assuming large enough storage)

If you are interested in having more detailed information about how TeslaUsb works, have a look into the [wiki](https://github.com/marcone/teslausb/wiki).

### Prerequisites

- You park in range of your wireless network, configured with WPA2 PSK access.
- [A Raspberry Pi or other SBC that supports USB OTG](https://github.com/marcone/teslausb/wiki/Hardware).
- A Micro SD card, at least 64 GB in size, and an adapter (if necessary) to connect the card to your computer.
- Cable(s) to connect the SBC to the Tesla.

### Installing

Base setup follows the [prebuilt image](https://github.com/marcone/teslausb/releases) and [one step setup instructions](doc/OneStepSetup.md); the Hub is installed on top via `hub/install.sh`.
