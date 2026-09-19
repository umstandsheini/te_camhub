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

## Benötigte Hardware

| Teil | Empfehlung | Hinweise |
|---|---|---|
| **Raspberry Pi** | Raspberry Pi 4 Model B, 2 GB RAM oder mehr | Getestet mit dem Pi 4 (1 GB RAM) – das reicht, ist aber knapp. Der Pi 4 kann über seine USB-C-Buchse gleichzeitig Strom beziehen und sich dem Auto gegenüber als USB-Laufwerk ausgeben. Andere Modelle sind nicht getestet; der Pi 5 und der Pi Zero 2 W (512 MB RAM) passen nicht. |
| **Datenträger** | USB-3-SSD mit UASP, 128 GB oder mehr | Getestet: Netac Z Slim 250 GB. Der Pi 4 startet direkt von USB (ohne eingesteckte microSD-Karte). Alternativ eine „High Endurance“-microSD ab 64 GB – die Dashcam schreibt ständig, normale Karten verschleißen schnell. |
| **Kabel zum Auto** | USB-A-auf-USB-C (oder USB-C-auf-USB-C), mit Datenleitungen | Reine Ladekabel funktionieren nicht. Anschluss: der USB-Port, an dem sonst der Dashcam-Stick steckt. |
| **Netzteil** | Offizielles Raspberry-Pi-USB-C-Netzteil (15 W) | Nur für die Einrichtung (20–40 Minuten) und für Updates am Schreibtisch; im Auto versorgt das Auto den Pi. |
| **Heim-WLAN** | – | In Reichweite des Parkplatzes, damit Clips aufs NAS gehen. |
| Optional | NAS mit SMB-Freigabe, Home Assistant mit MQTT, USB-WLAN-Stick | Das NAS nimmt die Aufnahmen auf; ein USB-WLAN-Stick hilft, wenn das eingebaute WLAN in der Garage zu schwach ist. BLE fürs Auto ist im Pi eingebaut. |
| Zum Flashen | Ein PC mit [Raspberry Pi Imager](https://www.raspberrypi.com/software/) oder balenaEtcher | – |

## Installation

Für den Raspberry Pi gibt es kein ISO – das Gegenstück ist ein **Speicherabbild (`.img.xz`)**, das man genauso mit einem Flash-Programm auf den Datenträger schreibt. Es enthält Raspberry Pi OS Lite (Bookworm, 64 Bit) und dieses Projekt; die eigentliche Einrichtung läuft beim ersten Start auf dem Pi selbst.

1. **Image laden:** `te_camhub-<version>.img.xz` von der [Release-Seite](https://github.com/umstandsheini/te_camhub/releases/latest) herunterladen (die `.sha256`-Datei daneben enthält die Prüfsumme).
2. **Flashen:** Raspberry Pi Imager öffnen → Gerät „Raspberry Pi 4“ → Betriebssystem „Eigenes Image verwenden“ → die `.img.xz` wählen → SSD bzw. microSD wählen → bei der Frage nach **Anpassungen „Nein“** (der Imager würde sonst ein eigenes Startskript schreiben, das die Einrichtung ersetzt).
3. **Zugangsdaten eintragen** – einer der beiden Wege:
   - **Weg A, Konfigurationsdatei:** Datenträger nach dem Flashen neu einstecken. Auf dem Laufwerk `bootfs` die Datei `teslausb_setup_variables.conf` mit einem Texteditor öffnen, mindestens `SSID` und `WIFIPASS` eintragen; NAS, Laufwerksgrößen und Gerätename optional. Speichern, auswerfen.
   - **Weg B, Einrichtungs-Hotspot:** nichts ändern. Nach dem ersten Start (Schritt 4) öffnet der Pi nach 2–3 Minuten das WLAN **`TeslaCam-Setup`** (Passwort `teslacam-setup`). Handy verbinden – die Einrichtungsseite erscheint von selbst, sonst `http://192.168.4.1` öffnen. WLAN, NAS und Gerätename eintragen, speichern.
4. **Starten:** Datenträger in den Pi, Pi **mit dem Netzteil** starten (noch nicht im Auto). Die Einrichtung dauert 20–40 Minuten mit mehreren Neustarts: Partitionen und USB-Laufwerke anlegen, Pakete laden, Hub installieren. Fortschritt: `teslausb-headless-setup.log` auf `bootfs`.
5. **Tresor einrichten:** Im Heim-WLAN `https://teslausb.local` öffnen (oder der gewählte Gerätename bzw. die IP-Adresse aus dem Router). Die Warnung zum selbst ausgestellten Zertifikat bestätigen, ein **Tresor-Passwort** festlegen – es ist zugleich das Login und schützt Schlüssel, Token und Fahrtenbuch (siehe unten). Ohne dieses Passwort sind verschlüsselte Aufnahmen nicht lesbar.
6. **Ins Auto:** Pi mit dem Datenkabel an den USB-Port des Autos anschließen. Das Auto erkennt die Laufwerke nach etwa 6 Sekunden.

**Wenn etwas hängt:** Findet der Pi das eingetragene WLAN nicht (Tippfehler im Passwort), öffnet er nach ein paar Minuten wieder `TeslaCam-Setup` mit den bisherigen Angaben. Schnelles Dauerblinken aller LEDs bedeutet „Einrichtung fehlgeschlagen“ – die Ursache steht im Log auf `bootfs`; nach dem Beheben einfach neu starten, die Einrichtung setzt fort. Der Einrichtungs-Hotspot nutzt ein öffentlich bekanntes Passwort und ist nur bis zum Ende der Einrichtung aktiv; wer ihn ganz vermeiden will, nimmt Weg A.

**SSH** ist nach der Installation nur mit Schlüssel möglich: `SSH_PUBKEY` in der Konfigurationsdatei eintragen (Benutzer `pi`). Ohne Schlüssel bekommt `pi` ein zufälliges Passwort, das niemand kennt – die Bedienung läuft komplett über die Hub-Oberfläche.

## Updates

Der Hub fragt alle 6 Stunden auf GitHub nach der neuesten Veröffentlichung (Releases dieses Repositorys). Gibt es eine neuere Version, erscheint nach dem Anmelden ein Hinweis, in der Navigation steht „Update“ neben **Diagnose**, und die Karte **Hub-Software** zeigt Version und Änderungen. „Nach Updates suchen“ fragt sofort nach.

„Update installieren“ (nur im Heim-WLAN):

1. **Sicherung** nach `/backingfiles/hub-backups/hub-<alte-version>-<zeit>.tar.gz`: Programm (`/opt/teslacam-hub`, `/root/te_camhub`, `/root/bin`), systemd-Dienste, Konfiguration, WLAN-Profile, TLS-Schlüssel und der Status-Ordner mit Tresor und Fahrtenbuch (ohne den neu erzeugbaren Cache verschlüsselter Vorschaubilder). Die letzten drei Sicherungen bleiben. Sie enthalten die Zugangsdaten der Konfiguration im Klartext – genau wie die Konfiguration selbst auf derselben SSD.
2. **Download** des Pakets `te_camhub-<version>.tar.gz` samt SHA-256-Prüfsumme, Prüfung, Entpacken; jede Python-Datei muss sich mit dem Python des Pi übersetzen lassen.
3. **Installation** in einem eigenen systemd-Dienst (`teslacam-hub-update`), weil `install.sh` den Hub neu startet: Quellbaum ersetzen, `hub/install.sh` ausführen, warten, bis der Hub wieder antwortet. Danach neu anmelden.
4. **Automatische Wiederherstellung:** Schlägt `install.sh` fehl oder antwortet der Hub nicht innerhalb von 4 Minuten, spielt das Skript Programm und Dienste aus der Sicherung zurück und startet den Hub neu. Protokoll: `/mutable/hub-update.log` und die Karte auf der Diagnose-Seite.

Das Update tauscht das Hub-Programm und die teslausb-Skripte; das Betriebssystem aktualisiert die eigene Karte „Betriebssystem-Updates“. Ein bereits laufendes `archiveloop` nutzt die neue Version ab dem nächsten Start des Pi.

**Sicherung von Hand zurückspielen** (per SSH):

```bash
sudo mount / -o remount,rw
```
```bash
sudo tar -xzf /backingfiles/hub-backups/<datei>.tar.gz -C / opt/teslacam-hub root/te_camhub root/bin
```
```bash
sudo systemctl restart teslacam-hub && sudo mount / -o remount,ro
```

Konfiguration oder Tresor lassen sich genauso einzeln zurückholen (`root/teslausb_setup_variables.conf`, `backingfiles/decrypt-viewer-state`).

## Eine neue Version veröffentlichen

Ein Tag der Form `vJJJJ.MM.TT` (bei mehreren am Tag `vJJJJ.MM.TT.N`) startet den Workflow `.github/workflows/release.yml`. Er baut das Paket für die Update-Funktion (`te_camhub-<tag>.tar.gz`, darin `VERSION` = Tag), das Installations-Image (`te_camhub-<tag>.img.xz`) und die Prüfsummen und legt damit das GitHub-Release an. Ab dann bieten alle installierten Hubs das Update an.

```bash
git tag v2026.09.17 && git push origin v2026.09.17
```

Das Image lässt sich auch lokal bauen (Linux oder WSL, als root): `sudo tools/build-image.sh` nimmt den eingecheckten Stand (`git archive HEAD`), lädt das Basis-Image einmal nach `.image-cache/`, prüft dessen SHA-256 und schreibt `dist/te_camhub-dev-<commit>.img.xz`.

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
[`hub/app/diag.py`](hub/app/diag.py), `BLE_READS`/`BLE_ACTIONS`). Die Hub-Oberfläche zeigt diese
Liste im Menü **Fahrzeug (BLE)** an, sobald ein Schlüssel gekoppelt ist.

**Erlaubt (vom Fahrzeug bestätigt):**

- **Lesen — uneingeschränkt, alle Kategorien:** Ladezustand, Verriegelung/Türen, Klima,
  Reifendruck, Standort, Fahrzustand, Medien(-Details), Lade-/Vorklimatisierungs-Zeitplan,
  Software-Update-Status, Kindersicherung, VCSEC-Basiszustand (funktioniert auch bei
  schlafendem Fahrzeug), alle eingetragenen Schlüssel auflisten, Ping.
- **Steuern:** Laden starten/stoppen, Ladegrenze/-strom setzen, Lade-Zeitplan abbrechen,
  Fahrzeug aufwecken (weckt nur, hält nicht wach — siehe
  [unten](#aufwecken-ist-nicht-wachhalten-gemessen-2026-09-13)), Zubehör-Stromversorgung
  an/aus (gilt laut Tesla nicht für den Datenport von Dashcam/USB, in der Praxis bestätigt).

**Abgelehnt** (vom Fahrzeug selbst mit `INSUFFICIENT_PRIVILEGES` bzw.
`GENERICERROR_UNAUTHORIZED` zurückgewiesen, nicht nur client-seitig verweigert):
alle Medien-Steuerbefehle (Lautstärke, Titel, Play/Pause, Favoriten), Ver-/Entriegeln,
Fenster, Klima ein/aus, Sentry-Modus, Tonneau, Lenkradheizung — außerdem Ladeport
öffnen/schließen, Hupen und Lichter blinken lassen: am 2026-07-10 morgens noch akzeptiert,
wenige Stunden später mit derselben Rolle und demselben Schlüssel abgelehnt (Tesla weist
selbst darauf hin, dass sich Rollenrechte ändern können).

**Bewusst nie automatisch getestet** (Befehl existiert technisch, aber ein unerwarteter
Erfolg wäre riskanter als die Erkenntnis wert): Fernstart (`drive`, Risiko einer
Fahrzeugbewegung), Schlüssel hinzufügen/entfernen/umbenennen (Risiko, eigenen Zugriff zu
verlieren), Software-Update starten/abbrechen, Gastdaten löschen, Frunk öffnen (kein
Schließbefehl vorhanden), Kofferraum (Schließen nicht auf allen Modellen verfügbar),
Sitzheizung/Zieltemperatur (brauchen Parameter, die sich nicht sicher wählen lassen),
Valet-/Gast-Modus, Kindersicherung setzen, Niedrig-Energie-Modus, komplexe Zeitpläne
hinzufügen/entfernen. (Die frühere Detail-Liste mit Begründung existiert im Code nicht
mehr, siehe Git-Historie von [`hub/app/diag.py`](hub/app/diag.py).)

**Sicherheitshinweis:** Der private Schlüssel liegt unverschlüsselt auf dem Stick
(`/root/.ble/<name>/key_private.pem`). Bei Verlust/Diebstahl des Sticks: BLE-Schlüssel
sofort in der Tesla-App entfernen, PIN-to-Drive im Fahrzeug aktivieren.

### Aufwecken ist nicht Wachhalten (gemessen 2026-09-13)

Das Auto versorgt den USB-Port des Hubs nur, solange es wach ist. Ob sich ein verriegeltes,
geparktes Auto per BLE-`wake` wach halten lässt, wurde live über den BLE-Proxy in der Garage
gemessen ([esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble), Knopf „Wake up“ —
nach Stand der Recherche derselbe Weckbefehl wie `tesla-control wake`):

| Versuch | Ergebnis |
|---|---|
| verriegelt, keine Befehle | schläft nach 5:56 min ein |
| `wake` alle 120 s, 14 min | 3× eingeschlafen, wach jeweils nur 1:01 bis 3:55 min |
| `wake` alle 60 s, 8 min | 2× eingeschlafen, wach jeweils nur 2:31 bzw. 3:06 min |

- `wake` weckt ein schlafendes Auto, aber nicht immer beim ersten Versuch.
- `wake` an ein **waches** Auto verlängert dessen Wachzeit nicht: dreimal schlief es 20–41 s
  nach so einem Befehl ein. Deshalb hilft auch kein kürzerer Takt.
- Das deckt sich mit upstream: [vehicle-command#397](https://github.com/teslamotors/vehicle-command/issues/397)
  berichtet dasselbe, teslausb nutzt für BLE deshalb `charge-port-close` als Impuls — und
  genau das lehnt das Fahrzeug für `charging_manager` ab (siehe oben).

**Folge:** Weder „Auto wach halten“ noch „Vor dem Einschlafen synchronisieren“ halten das Auto
derzeit zuverlässig wach, beide nutzen `wake`. Die Entscheidungslogik des Sync-Wachhaltens
([`hub/app/synchold.py`](hub/app/synchold.py)) bleibt richtig, nur der Mechanismus muss ersetzt
werden. Belegte Alternativen: Sentry-Modus (upstream dokumentiert; braucht einen Schlüssel,
der Sentry setzen darf — `charging_manager` darf es nicht) oder ein entriegeltes Auto (hält
meist, aber nicht immer).

## Claude-Assistent: Lightshows und Boombox-Sounds unterwegs

Menüpunkt **Assistent**: ein Chat, in dem Claude im Netz nach Lightshows und Boombox-Sounds sucht, sie auf den Pi lädt, prüft und – nach Bestätigung per Klick – auf die USB-Laufwerke spielt. Gedacht für unterwegs über den Handy-Hotspot, wenn kein Laptop zum Kopieren dabei ist.

- Läuft über die Claude-API (`claude-opus-5`, Abrechnung pro Nutzung, getrennt vom Claude-Abo). Der API-Key liegt verschlüsselt im Tresor, nicht in der Konfigurationsdatei. Lehnt das Modell eine Anfrage ab, übernimmt serverseitig Anthropics Standard-Ersatzmodell (`fallbacks: "default"`).
- Websuche und Seitenabruf laufen bei Anthropic. Der Pi führt nur die Tool-Schleife und ein paar eng begrenzte Werkzeuge aus: Download in einen Zwischenspeicher (`/backingfiles/hub-assistant-staging`), Prüfen, Installieren, Entfernen. Keine Shell, keine beliebigen Pfade.
- Installieren und Entfernen warten immer auf „Ausführen“ im Browser. Geschrieben wird mit kurz vom Auto getrennten Laufwerken und unter archiveloops Archiv-Lock (`files.with_drives_detached`).
- Downloads auf Adressen im lokalen oder privaten Netz werden abgewiesen, auch nach Weiterleitungen – eine Webseite soll den Assistenten nicht über WireGuard ins Heimnetz schicken können.
- Geprüft wird nach [teslamotors/light-show](https://github.com/teslamotors/light-show): FSEQ v2 unkomprimiert, 48 oder 200 Kanäle, mindestens 15 ms Schrittweite, höchstens 4 h; Audio mp3/wav, 44,1 kHz empfohlen. Boombox: das Auto bietet nur die ersten fünf Dateien (alphabetisch) an.
- Warum nicht Claude Code direkt auf dem Pi: braucht 4 GB RAM (dieser Pi 4 hat 1 GB) und wäre im Browser praktisch eine Root-Shell.

## Betriebssystem-Updates (Diagnose-Seite)

- „Nach Updates suchen“ ist jederzeit gefahrlos: `apt-get update` in Wegwerf-Listen unter `/tmp`, dann eine Simulation; die Systempartition bleibt schreibgeschützt. Zeigt auch Sicherheitsupdates und eine früher unterbrochene Installation (`dpkg --audit`).
- „Jetzt installieren“ nur im Heim-WLAN und nicht während einer Archivierung (archiveloops Lock): `/` und `/boot/firmware` beschreibbar, `dpkg --configure -a`, `apt-get upgrade --with-new-pkgs` (entfernt nie Pakete, behält Konfigurationsdateien), `apt-get clean`, zurück auf schreibgeschützt. Protokoll: `/mutable/os-update.log`. Während das läuft, lehnt der Hub alle anderen Aktionen ab, die `/` selbst umschalten.
- **Strom:** Auch zu Hause hängt der Pi im Auto am USB-Strom des Autos, und der fällt weg, sobald das Auto einschläft. Ein mittendrin abgebrochenes Update ist der eine Fall, der das System wirklich beschädigen kann – also Auto wach halten (Wächter-Modus) oder am Netzteil aktualisieren.

## WLAN-Netze (Einstellungen)

Das Heim-WLAN (`SSID`/`WIFIPASS`) hat immer Vorrang. Darunter eine sortierbare Liste weiterer Netze (Handy-Hotspots usw.), gespeichert als `WIFI_NETWORKS` in der Konfiguration (wie die anderen Zugangsdaten: in der Oberfläche nie im Klartext, im Backup-Export enthalten, beim Tresor-Zurücksetzen gelöscht). Jedes Netz wird ein NetworkManager-Profil `TESLAUSB_WIFI_<n>` mit Priorität -10, -11, … Der frühere einzelne Hotspot (`HOTSPOT_SSID`) wird bei der ersten Änderung als erster Eintrag übernommen.

## Verschlüsselte Vorschaubilder und Fahrdaten

Entschlüsselte Videos liegen nur im RAM (`/dev/shm`) und verschwinden beim Sperren des Tresors und bei jedem Neustart. Vorschaubilder und Fahrdaten (GPS, Tempo, Gang) behält der Hub zusätzlich **verschlüsselt auf der SSD** (`/backingfiles/decrypt-viewer-state/derived/`): versiegelt mit dem Tresor-Hauptschlüssel (AES-256-GCM, dasselbe Verfahren wie bei den Schlüssel-Dateien neben den Clips), Fahrdaten vorher komprimiert, Dateinamen nur ein Hash der Clip-Kennung. Nach dem Anmelden sind Übersicht und Kartenpunkte damit sofort da, ohne jeden Clip (~36 MB) erneut zu entschlüsseln; ein ausgebauter Stick bleibt trotzdem wertlos. Fahrdaten für die Kartenpunkte werden direkt aus der Kopie gelesen, nicht in den RAM gelegt; in den RAM kommt nur, was der Player gerade abspielt. Kopien älter als 120 Tage werden entfernt, beim Zurücksetzen des Tresors alle.

## WLAN-Auswahl und Access Point (Einstellungen → WLAN-Netze)

Zwei Fallen, beide im Auto aufgetreten:

- **NetworkManager wechselt nicht von selbst zurück.** Die Priorität entscheidet nur, *während* er ein Netz auswählt. Einmal mit einem Handy-Hotspot verbunden, bleibt der Pi dort, auch wenn das Heim-WLAN wieder in Reichweite ist (18.09.2026: online über ein Handy im Auto, aber zu Hause unsichtbar – kein Zugriff, kein NAS-Abgleich, keine Home-Assistant-Daten).
- **Der eigene Access Point blockiert das Funkgerät.** Ohne USB-WLAN-Stick teilen sich Access Point (`ap0`) und Client (`wlan0`) ein Funkgerät; der Access Point nagelt den Client auf seinen Kanal fest. Ein Handy-Hotspot auf einem anderen Kanal ist dann kaum erreichbar.

`hub/wifi-watch.sh` (Timer, jede Minute) übernimmt deshalb beide Aufgaben – es ersetzt die früheren Einzelwächter für AP-Rückfall und Heim-WLAN, die sich gegenseitig im Weg standen:

1. **Bekannte Netze, bestes zuerst:** Heim-WLAN, danach die Liste aus den Einstellungen in ihrer Reihenfolge.
2. Auf dem besten erreichbaren Netz verbunden → nichts zu tun, Access Point aus.
3. Auf einem schlechteren verbunden → wechseln (das deckt „zurück nach Hause“ und „besserer Hotspot in Reichweite“ ab).
4. Gar nicht verbunden → erst den Access Point herunternehmen (Funkgerät frei), dann suchen und das beste bekannte Netz verbinden.
5. Findet er dreimal in Folge nichts Bekanntes → Access Point an, als Zugang wenn sonst nichts geht.

Das Heim-WLAN gilt zusätzlich als erreichbar, wenn das Auto laut letzter bekannter Position in der **Zuhause-Zone** steht (`HOME_LAT`/`HOME_LON`, Umkreis `HOME_RADIUS_M`, Standard 150 m) oder – solange keine Zone bekannt ist – sein Profil ein **verstecktes** Netz ist, das im Scan nie auftauchen kann. Die Zone lernt der Hub selbst, sobald er im Heim-WLAN hängt und eine Position vom Auto kennt (`home_zone_loop` in `server.py`); die Position steht unverschlüsselt in `last_location.json` – sie ist der Parkplatz, nicht die Route, die Route bleibt verschlüsselt.

Ein fehlgeschlagener Verbindungsversuch pausiert 10 Minuten. Standardmäßig ist der Access Point **nur Rückfall** (`AP_FALLBACK_ONLY=true`); `false` hält ihn dauerhaft an, was nur mit USB-WLAN-Stick (zweites Funkgerät) sinnvoll ist. Ganz abschalten lässt sich der Wächter mit `HOME_WIFI_PREFER=false`. Protokoll: `/mutable/home-wifi.log`.

## Ereignis-Daten bei den entschlüsselten Clips

Das Auto legt zu jedem Sentry-/Saved-Ereignis `event.json` (Auslöser, Zeitpunkt, Position) und `thumb.png` neben die Videos. teslausb archiviert beides zuverlässig nach `TeslaCam/EncryptedClips/…` (gemessen 18.09.2026: 110 von 110 Ereignisordnern hatten beide Dateien) — nur landen sie nie im entschlüsselten Zweig `decrypted/…`, den ein Viewer wie Te_FITI indiziert (0 von 548). Nichts geht also verloren, die Dateien reisen bloß nicht mit.

**Wichtig – diese Metadaten sind selbst verschlüsselt, anders als die Videos.** Die Videos nutzen den Cloud-Weg (verpackter Schlüssel im Kopf bei Offset 4096, per Konto-Token von Tesla abrufbar – so entschlüsselt der Hub sie). `event.json`/`thumb.png` nutzen dagegen den eCryptfs-**Passwort-Weg** (Tag-3-Paket, Salz `0011223344556677`, 65536 MD5-Runden, AES-128) mit dem Schlüsselnamen **`_CONSOLE`** – einem fahrzeug-internen Konsolen-Schlüssel. Er liegt nicht bei Tesla, wird über keine Schnittstelle herausgegeben und ist außerhalb des Autos nicht zu bekommen. Deshalb sind diese beiden Dateien **nicht entschlüsselbar**, siehe `EVENT_JSON_ENCRYPTED_CLIPS_ISSUE.md` und den offenen Punkt unten.

`nassync.mirror_event_files()` (läuft im NAS-Abgleich) macht daraus das Beste, ohne den Schlüssel zu brauchen:

- **Unverschlüsselte Alt-Clips** (vor Firmware 2026.20): `event.json`/`thumb.png` sind Klartext und werden kopiert – nie überschreiben, nie löschen.
- **Verschlüsselte Clips:** Der `_CONSOLE`-Container wird **nicht** kopiert (er läse sich für einen Viewer wie kaputtes JSON). Stattdessen wird eine klar gekennzeichnete **Ersatz-`event.json`** geschrieben (`"reconstructed": true`) mit Zeitpunkt aus dem Ordnernamen und, falls entschlüsselte Telemetrie vorliegt, Näherungs-Position. Der Auslösegrund fehlt ehrlich (`"unbekannt (rekonstruiert)"`). `thumb.png` wird nicht erfunden.
- **Selbstheilend:** Eine frühere Fassung hatte die rohen Container versehentlich nach `decrypted/` kopiert; solche Container werden beim nächsten Lauf wieder entfernt. Live-Lauf 19.09.2026: 243 Container entfernt, 125 Ersatzdateien.

**Offener Punkt – `_CONSOLE`-Schlüssel:** Stand 19.09.2026 ist der Schlüssel weder öffentlich bekannt (Internet-/GitHub-Recherche: alle Werkzeuge entschlüsseln nur die Videos), noch erraten (~60 Kandidaten inkl. VIN und Tresorpasswort gegen die Header-Signatur geprüft), noch abfangbar (er entsteht und bleibt in der Fahrzeug-Konsole, fließt über USB/BLE/Cloud nie). Der einzige realistische Weg wäre eine künftige Community-Extraktion aus der Firmware – der Passwort-Weg ist im Entschlüsselungsmodul schon nachgebaut, es fehlt allein die Passphrase. Bis dahin bleibt es bei der Ersatz-`event.json`. Kandidat für später: den Grund aus unserer eigenen Telemetrie erschließen (Beschleunigungsspitze → `sentry_aware_accel_…`, Ordnertyp → Sentry vs. manuell).

## Video-Verschlüsselung im Auto: an oder aus?

Seit Firmware **2026.20** verschlüsselt das Auto Dashcam-/Sentry-Aufnahmen standardmäßig auf dem Datenträger. Abschaltbar unter **Fahrzeug → Sicherheit → „Dashcam-Aufnahmen verschlüsseln“**. Die Entscheidung ist ein echter Kompromiss – hier die Abwägung speziell für diesen Aufbau (Hub + NAS + externer Viewer).

| Aspekt | Verschlüsselung **AN** (Standard) | Verschlüsselung **AUS** |
|---|---|---|
| **Videos bei Diebstahl von Stick/Pi** | Auf der SSD unlesbar – der Schlüssel liegt **nicht** auf dem Pi, sondern kommt von Tesla. Stärkster Schutz, den es hier gibt. | Klartext-MP4 – wer den Datenträger hat, sieht alles (Personen, Kennzeichen, Wohnort …). Datenschutz-Risiko. |
| **`event.json` / `thumb.png`** | **Ebenfalls verschlüsselt, aber mit dem Konsolen-Schlüssel `_CONSOLE`** – außerhalb des Autos **nicht** entschlüsselbar (siehe Abschnitt oben). Auslösegrund und Original-Vorschaubild sind für jeden externen Viewer verloren; der Hub liefert nur eine Ersatz-`event.json`. | Klartext – **voller Auslösegrund, Original-Vorschaubild, Position**. Te_FITI & Co. zeigen alles. |
| **Wiedergabe / Aufwand** | Clips brauchen Schlüsselabruf + Entschlüsselung (Hub oder dashcam.tesla.com). Braucht ein **Tesla-Konto-Token** und beim Holen neuer Schlüssel Internet. | Clips sind sofort abspielbar, überall, ohne Konto, ohne Online-Abruf. Einfachere Kette. |
| **Snapshot-Risiko** | Ein Schnappschuss mitten im Schreiben kann einen **dauerhaft unentschlüsselbaren** Clip erzeugen (leerer Schlüsselblock). teslausb fängt das ab (`make_snapshot.sh`), es bleibt aber eine Fehlerquelle. | Entfällt – ein halb geschriebener MP4 ist höchstens kurz, nie „für immer kaputt“. |
| **Pi-Last (1 GB RAM)** | Entschlüsseln kostet CPU/RAM; auf dem kleinen Pi historisch eine OOM-Quelle (abgesichert, aber real). | Keine Entschlüsselung nötig. |
| **Langzeit-Abhängigkeit** | Alte Clips brauchen weiter Teslas Schlüsseldienst. Ändert/entfernt Tesla ihn, könnten sie unlesbar werden. | Keine Abhängigkeit von Tesla. |

**Für diesen Aufbau wichtig – die Verschlüsselung schützt nur die Pi-SSD, nicht das Ganze:**

- Das **NAS** hält die Aufnahmen ohnehin **entschlüsselt** (`decrypted/`), und bei aktiven Rohschlüsseln (`NAS_RAW_KEYS`) zusätzlich die Klartext-Schlüssel daneben. Der „unlesbar bei Diebstahl“-Vorteil gilt also für den Pi, **nicht** fürs NAS – dort ist der Inhalt so oder so offen.
- Der Hub braucht bei aktiver Verschlüsselung den **Tesla-Konto-Token** (im Tresor) und muss Schlüssel online holen. Ohne das bleiben neue Clips unlesbar.
- Der einzige *inhaltliche* Verlust durch die Verschlüsselung ist die **`event.json`** (Auslösegrund + Vorschaubild) – der Rest lässt sich mit Schlüssel voll wiederherstellen.

**Faustregel:**

- **Verschlüsselung AN lassen**, wenn dein Hauptszenario „Pi/Stick wird aus dem Auto entwendet“ ist und dir der Schutz der Videoinhalte auf dem Gerät wichtiger ist als vollständige Event-Metadaten. Den Verlust der `event.json` mildert die Ersatzdatei ab.
- **Verschlüsselung AUS**, wenn du auf **vollständige Ereignisdaten** (Grund, Vorschaubild) Wert legst, den Ablauf einfach halten willst und der Diebstahlschutz stattdessen woanders sitzt – z. B. der Pi steckt verdeckt/gesichert, und das NAS steht im gesicherten Heimnetz. Dann arbeitet die ganze Kette ohne Tesla-Konto, ohne Online-Abruf und ohne den Snapshot-Fallstrick.

Beides funktioniert mit dem Hub. Bei **AN** greifen Schlüsselabruf, Ersatz-`event.json` und die verschlüsselten Ableitungen; bei **AUS** ist alles Klartext und der Viewer zeigt native Ereignisdaten. (Quellen: [Not a Tesla App](https://www.notateslaapp.com/news/4225/tesla-enables-dashcam-clip-encryption-in-update-202620), [Drive Tesla Canada](https://driveteslacanada.ca/news/tesla-2026-20-dashcam-encryption-parental-controls/).)

## „Ist der Hub beim Auto?“ (Fahrzeug-Seite)

Von der USB-Seite kann der Pi nicht erkennen, an welchem Auto er steckt — das Gadget weiß nur, dass *irgendein* Host die Laufwerke eingebunden hat. Eindeutig ist nur die BLE-Kopplung: `tesla-control` spricht genau die hinterlegte VIN an, authentifiziert mit dem Schlüssel, den dieses Fahrzeug akzeptiert hat. `presence.py` schickt deshalb alle 10 Minuten einen kurzen `ping` und zeigt das Ergebnis auf der Fahrzeug-Seite, im Ereignis-Log (nur bei Wechseln) und als HA-Sensor „Beim Auto (BLE)“.

Was das beweist: Das gekoppelte Fahrzeug ist in Bluetooth-Reichweite, also wenige Meter entfernt. Was es **nicht** beweist: dass der Hub eingesteckt ist — und ein schlafendes oder weit entferntes Auto antwortet einfach nicht. Ausbleibende Antwort ist deshalb kein Diebstahlalarm. Drei Fehlversuche in Folge sind nötig, bevor der Zustand auf „nicht bestätigt“ wechselt; während einer Fahrt genügen die ohnehin laufenden BLE-Abfragen. `ping` weckt kein schlafendes Auto.

## WireGuard-Wächter

Mit `WG_ALLOWED_IPS=0.0.0.0/0,::/0` (der Vorgabe für „VPN nach Hause“) leitet wg-quick **allen** Internetverkehr in den Tunnel und setzt den DNS-Server der Gegenstelle. Antwortet die nicht, bleibt der Pi ohne Namensauflösung und ohne Internet — während das Heimnetz weiterläuft, es also von außen nicht kaputt aussieht. Am 18.09.2026 war genau das der Fall: nie ein Handshake, 177 KB gesendet, 0 empfangen; der Hub kam weder an GitHub noch an Tesla, und der tote Resolver-Eintrag `192.168.6.1` überlebte den Tunnel selbst (resolvconf-Datensatz `wg0`).

`hub/wg-watch.sh` (Timer, alle 2 Minuten) stoppt einen solchen Tunnel: kein Handshake seit 180 s **und** voller Tunnel → `wg-quick` stoppen, den resolvconf-Datensatz entfernen, alle 30 Minuten neu versuchen. Ein geteilter Tunnel (ohne Standardroute) bleibt unangetastet, weil ein toter dort nichts kostet. `WG_ENABLED=false` schaltet den Wächter ganz ab. Protokoll: `/mutable/wg-watch.log`.

## GPS-Fahrtenbuch (verschlüsselt)

Die Blackbox zeichnet während der Fahrt alle 10 s Position, Richtung, Kilometerstand und Gang auf – ein Bewegungsprofil. Seit dem 15.09.2026 landet davon nichts mehr im Klartext auf der SSD. Weil der Tresor beim Fahren fast immer gesperrt ist (jeder Stromausfall sperrt ihn), verschlüsselt der Hub mit einem Schlüsselpaar: Der öffentliche Schlüssel liegt neben den Fahrten (`blackbox/trips.pub`) und reicht zum Schreiben, der private (RSA-3072) liegt im Tresor. Jede Fahrt-Datei (`*.tbx`) bekommt pro Hub-Start einen eigenen AES-256-Schlüssel, jeder Punkt wird einzeln mit AES-256-GCM verschlüsselt angehängt – ein Stromausfall kostet höchstens den Punkt, der gerade geschrieben wurde. Fahrtenliste, GPX-Export und die Übertragung aufs NAS brauchen deshalb den entsperrten Tresor; die km-Angabe bei „Fahrt beendet“ im Ereignis-Log kommt aus dem RAM. Das Schlüsselpaar entsteht beim ersten Anmelden nach dem Update; bis dahin aufgezeichnete Punkte liegen nur im RAM. Ältere Fahrten im Klartext (`*.jsonl`) werden beim ersten Anmelden verschlüsselt, der Klartext überschrieben und gelöscht (auf einer SSD ist das Überschreiben nicht garantiert). Beim Zurücksetzen des Tresors werden die verschlüsselten Fahrten gelöscht. Auf dem NAS liegen die Fahrten weiterhin als lesbare GPX-Dateien (`Fahrten/`).

## Wenn der Pi gestohlen wird

Die SSD selbst ist nicht verschlüsselt – der Pi muss im Auto ohne Eingabe starten und ins WLAN und aufs NAS kommen. Wer sie ausbaut, liest deshalb im Klartext: NAS-Benutzer und -Passwort, die WLAN-Passwörter (Heim-WLAN, Hotspots, eigener Access-Point), den MQTT-Zugang, die BLE-Schlüssel fürs Auto, die VIN sowie die Passwort-Hashes des Benutzers `pi` und von Samba. Ohne Tresor-Passwort wertlos sind: Clip-Schlüssel, Tesla-Konto-Token, Anthropic-Schlüssel, Vorschaubilder, Fahrdaten aus den Videos und das GPS-Fahrtenbuch; die Videos hat schon das Auto verschlüsselt, entschlüsselte Videos liegen nur im RAM. Mit WLAN- und NAS-Passwort kommt ein Dieb aber in Reichweite des Hauses ins Heimnetz und ans NAS – dort liegen je nach Einrichtung entschlüsselte Videos (`decrypted/`), unverschlüsselte Rohschlüssel (`*.rawkey.json`) und die Fahrten als GPX.

Nach einem Diebstahl solltest du:

1. NAS-, WLAN- und MQTT-Passwort ändern.
2. Beide BLE-Schlüssel in der Schlüsselliste des Autos löschen.
3. Das Tesla-Passwort ändern.

## Startzeiten (Diagnose-Seite)

Der Hub misst jeden Start des Pi aus dem dauerhaften Journal und protokolliert ihn in `boottimes.jsonl` im Status-Ordner (`/backingfiles/decrypt-viewer-state/`) – auch rückwirkend für alle Starts, die das Journal noch kennt, und über die Journal-Rotation hinaus. Gemessen wird in Sekunden ab Einschalten: SSD eingehängt, USB-Gadget gebunden, **Laufwerke vom Auto eingebunden**, WLAN verbunden, Hub erreichbar, systemd fertig (Kernel/Userspace) – dazu, ob der Lauf davor sauber heruntergefahren wurde oder einfach der Strom weg war (im Auto der Normalfall). Anzeige als Tabelle auf der Diagnose-Seite, ein Eintrag pro Start im Ereignis-Log und die HA-Sensoren „Start: Laufwerke bereit“ und „Start: Hub bereit“. Referenz nach dem Neuaufbau auf der Netac-SSD: Laufwerke nach ~6 s, WLAN ~10 s, Hub ~16 s.

## Auf dem NAS gelöschte Clips (Einstellungen → NAS)

teslausb führt eine Liste der Dateien, die es schon aufs NAS kopiert hat (`/mutable/sentry_files_archived`), und kopiert keine davon ein zweites Mal. Wer auf dem NAS Clips löscht, um Platz zu sparen, bekommt sie also nicht wieder hochgeladen – solange der Clip noch in den lokalen Snapshots liegt, gilt sein Listeneintrag. Der Hub wertet das aus: Ein Clip, der schon übertragen war und auf dem NAS nicht mehr liegt (auch nicht unter `decrypted/` oder `broken/`), erscheint als „🗑 auf NAS gelöscht“ und zählt für die Abdeckung (Übersicht, HA-Sensor „NAS-Archivierung“) als erledigt. Der Schalter „Auf dem NAS gelöschte Clips nicht erneut übertragen“ (`NAS_SKIP_DELETED`, Standard: an) dreht das um: Ausgeschaltet nimmt der Hub solche Dateien unter dem Archiv-Lock von der Liste, und der nächste Archivlauf überträgt sie erneut. Findet der Hub auf dem NAS gar keinen Clip, gilt das als falscher Pfad, nicht als „alles gelöscht“ – dann wird nichts markiert und nichts erneut übertragen.

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
