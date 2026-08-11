# TeslaCam Hub

Ein Fork von [teslausb](https://github.com/marcone/teslausb), der die alte Weboberfläche
(nginx + cgi-bin + iframe) durch einen einzigen Python-Dienst ("Hub") ersetzt: HTTPS +
Login, Video-Viewer mit On-Demand-Entschlüsselung, Datei-Browser, NAS-Sync,
Diagnose/Einstellungen und optionale BLE-/Home-Assistant-Integration — alles aus einer
Oberfläche. Der teslausb-Kern (USB-Gadget, Snapshots, Archivierung) bleibt unverändert;
der Hub konfiguriert und steuert ihn nur.

## ⚠ Nur für den privaten Eigenbedarf gebaut

Dieses Projekt ist **ausschließlich für meinen eigenen Gebrauch** entstanden, zugeschnitten
auf meine eigene Hardware, mein eigenes Netzwerk und meinen eigenen Workflow. Es ist
**öffentlich einsehbar, aber nicht als fertiges Produkt für Dritte gedacht**.

Jeder darf sich den Code nehmen, verändern und für sich selbst nutzen — aber:

- **Keine Garantie.** Weder dafür, dass irgendetwas funktioniert, noch dafür, dass es
  sicher, korrekt oder für einen bestimmten Zweck geeignet ist.
- **Kein Support.** Ich beantworte keine Anfragen, behebe keine fremden Bugs und
  übernehme keine Verantwortung für Schäden, Datenverlust oder sonstige Folgen der
  Nutzung — inklusive alles, was mit Fahrzeug-Fernzugriff (BLE-Schlüssel) zu tun hat.
- **Volles Eigenrisiko.** Wer diese Software einsetzt, tut das komplett auf eigene
  Verantwortung, insbesondere im Umgang mit Fahrzeugzugriff und den auf dem Stick
  gespeicherten Zugangsdaten/Schlüsseln.

## ⚠ Sehr früher Entwicklungsstand

Das Projekt ist **frisch, in aktiver Entwicklung und nicht auditiert**. Es kann
Sicherheitslücken, Bugs, halbfertige Funktionen und unerwartetes Verhalten enthalten.
Nichts hier wurde von jemand anderem als mir selbst geprüft. Vor jeglichem produktiven
Einsatz: Code lesen, selbst verstehen, selbst testen.

## Danksagungen / Quellen

Dieses Projekt baut auf der Arbeit anderer auf:

- **[marcone/teslausb](https://github.com/marcone/teslausb)** — die Basis dieses Forks:
  USB-Gadget-Emulation, Snapshot-/Archivierungs-Pipeline, Netzwerk-/AP-Setup, BLE-Grundlagen.
  Ursprünglich entstanden aus [diesem Reddit-Thread](https://www.reddit.com/r/teslamotors/comments/9m9gyk/build_a_smart_usb_drive_for_your_tesla_dash_cam/).
- **Te_FITI** — Vorbild für Viewer-Funktionsumfang (synchronisierte Mehrkamera-Wiedergabe,
  Event-Seek, GPS-Karte, Telemetrie-HUD) und Ausgangsbasis für die eCryptfs-/Krypto-Module.
- **[yoziru/esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble)** — Referenz
  für die Mehrrollen-BLE-Schlüsselkopplung (getrennte Schlüssel pro Rolle statt einem
  Owner-Vollzugriffsschlüssel).
- **[teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)** —
  offizielle Tesla-Werkzeuge (`tesla-control`, `tesla-keygen`) für BLE-Fahrzeugbefehle.
- **[MikeBishop/tesla-vehicle-command-arm-binaries](https://github.com/MikeBishop/tesla-vehicle-command-arm-binaries)** —
  vorgebaute ARM-Binaries der obigen Tools für den Raspberry Pi.

## BLE-Fahrzeugzugriff: was die Rolle `charging_manager` wirklich darf

Der Hub koppelt bewusst nur einen einzelnen, eingeschränkten BLE-Schlüssel mit der
Tesla-Rolle `charging_manager` (nicht `owner`/`driver` mit Vollzugriff, siehe
[Danksagungen](#danksagungen--quellen) → esphome-tesla-ble). Tesla dokumentiert Rollen nur
grob (["kann Befehle autorisieren, die das Laden betreffen"](https://github.com/teslamotors/vehicle-command/blob/main/pkg/protocol/protocol.md#roles)) —
was das konkret bedeutet, wurde deshalb am 2026-07-10 **empirisch gegen ein echtes
Fahrzeug** getestet (`tesla-control` über BLE, Ergebnis fest hinterlegt in
[`hub/app/diag.py`](hub/app/diag.py), `BLE_KNOWN_RESULTS`). Die Hub-Oberfläche zeigt diese
Liste im Menü **Fahrzeug (BLE)** an, sobald ein Schlüssel gekoppelt ist.

**Erlaubt (vom Fahrzeug bestätigt):**

- **Lesen — uneingeschränkt, alle Kategorien:** Ladezustand, Verriegelung/Türen, Klima,
  Reifendruck, Standort, Fahrzustand, Medien(-Details), Lade-/Vorklimatisierungs-Zeitplan,
  Software-Update-Status, Kindersicherung, VCSEC-Basiszustand (funktioniert auch bei
  schlafendem Fahrzeug), alle eingetragenen Schlüssel auflisten, Ping.
- **Steuern:** Ladeport öffnen/schließen, Laden starten/stoppen, Ladegrenze/-strom setzen,
  Lade-Zeitplan abbrechen, Fahrzeug aufwecken, Hupen, Lichter blinken lassen,
  Zubehör-Stromversorgung an/aus.

**Abgelehnt** (vom Fahrzeug selbst mit `INSUFFICIENT_PRIVILEGES` bzw.
`GENERICERROR_UNAUTHORIZED` zurückgewiesen, nicht nur client-seitig verweigert):
alle Medien-Steuerbefehle (Lautstärke, Titel, Play/Pause, Favoriten), Ver-/Entriegeln,
Fenster, Klima ein/aus, Sentry-Modus, Tonneau, Lenkradheizung.

**Bewusst nie automatisch getestet** (Befehl existiert technisch, aber ein unerwarteter
Erfolg wäre riskanter als die Erkenntnis wert): Fernstart (`drive`, Risiko einer
Fahrzeugbewegung), Schlüssel hinzufügen/entfernen/umbenennen (Risiko, eigenen Zugriff zu
verlieren), Software-Update starten/abbrechen, Gastdaten löschen, Frunk öffnen (kein
Schließbefehl vorhanden), Kofferraum (Schließen nicht auf allen Modellen verfügbar),
Sitzheizung/Zieltemperatur (brauchen Parameter, die sich nicht sicher wählen lassen),
Valet-/Gast-Modus, Kindersicherung setzen, Niedrig-Energie-Modus, komplexe Zeitpläne
hinzufügen/entfernen. Volle Liste mit Begründung: `BLE_UNTESTED_COMMANDS` in
[`hub/app/diag.py`](hub/app/diag.py).

**Sicherheitshinweis:** Der private Schlüssel liegt unverschlüsselt auf dem Stick
(`/root/.ble/<name>/key_private.pem`). Bei Verlust/Diebstahl des Sticks: BLE-Schlüssel
sofort in der Tesla-App entfernen, PIN-to-Drive im Fahrzeug aktivieren.

## Ursprüngliches teslausb

Alles unterhalb dieser Zeile ist die ursprüngliche teslausb-Dokumentation und betrifft den
unveränderten Kern, auf dem der Hub aufsetzt.

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
