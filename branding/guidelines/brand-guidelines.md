# DIM Creator v3 · Mini Brand Guidelines

**Stand:** 04.11.2026 · **Asset:** freigegebenes Aqua-/Türkis-Signet mit transparentem Hintergrund.

## 1. Logo und Identität

Das primäre Markenasset ist **ausschließlich das Signet**. Es enthält keinen Schriftzug und keine Hintergrundkachel. Das Logo soll in Windows, macOS und Linux dieselbe Form behalten. Die OS-spezifischen Dateien sind **technische Exporte**, keine unterschiedlichen Markendesigns.

## 2. Farbe

Die folgenden HEX-Werte sind aus dem genehmigten PNG angenähert; das Original verwendet Farbverläufe und Übergänge.

| Name | HEX | Einsatz |
|---|---|---|
| Aqua | `#1CECF5` | Leuchtendes Highlight, Signet |
| Türkis | `#04AABB` | Primärer mittlerer Farbton |
| Mint | `#17DCAD` | Verbindungsflächen, sekundärer Akzent |
| Tiefblau | `#02738F` | Tiefe und Kontrast im Signet |
| Grün | `#5EF198` | Zweiter heller Akzent |

**Neutralflächen für Präsentation, keine im Logo eingebauten Hintergründe:** Dunkel `#101820`, Hell `#F5F8FA`.

## 3. Größen und Schutzzone

- Master: 1024×1024, RGBA, sRGB-orientiert, transparenter Hintergrund.
- App-/Installer-Icon: Multi-Size Windows ICO, klassisches macOS ICNS, Linux PNGs.
- Für 16–32 px werden die proportional **optisch skalierte** Ausgaben mit geringfügig kleinerem Leerraum verwendet.
- Empfehlung: Mindestens 10 % Schutzzone **außerhalb** der sichtbaren Symbolkante zu benachbarten Elementen. Das ist ein Gestaltungstipp, keine offizielle technische Norm.
- Kleine Größen im echten Betriebssystem testen. Falls das Signet dort zu viele innere Details verliert, eine separate *Micro-Icon-Optimierung* erst nach Abnahme vornehmen, nicht automatisch ein anderes Logo erstellen.

## 4. Verwendung

**Do**

- Die freigegebene, unveränderte Silhouette verwenden.
- Proportional skalieren und ausreichend Raum lassen.
- Transparenz erhalten.
- In Light/Dark und auf Taskleiste/Dock testen.
- Auf dieselbe Datei für App und Installer zurückgreifen.

**Don't**

- Kein Text „DIM Creator“ in das Symbol einbauen.
- Keine Pfeile, Ringe, Kacheln, Konturen oder Versandkartons hinzufügen.
- Nicht strecken, kippen, drehen oder Spiegeln.
- Keine beliebigen neuen Verlaufsfarben erzeugen.
- Keine JPEG-Datei ohne Transparenz als App-Icon verwenden.

## 5. Technische Ausgaben

| Plattform/Verwendung | Datei | Bemerkung |
|---|---|---|
| Windows App/NSIS | `tauri/src-tauri/icons/icon.ico` | Multi-Resolution, 16/24/32/48/64/128/256 |
| macOS | `tauri/src-tauri/icons/icon.icns` | Klassisches ICNS, mehrere Auflösungen |
| Linux | `linux/hicolor/*/apps/dim-creator.png` | PNGs nach Hicolor-Struktur |
| Tauri | `tauri/src-tauri/icons/` | Standardnamen und Struktur |
| Website | `web/favicon.ico` / `favicon-32x32.png` | Kein Text im Icon |
| Windows-/Linux-Tray | `tray/color/tray-24.png` | Nur falls Tray eingesetzt wird |
| macOS-Tray | `tray/macos/trayTemplate.png`, `trayTemplate@2x.png` | Template-Icon, systemseitige Einfärbung |
| Installer | `installer/nsis/installer.ico` | Identisches Markenlogo, kein zweites Design |

## 6. Plattformhinweis

Eine passende ICO-/ICNS-Datei ist **keine Garantie**, dass die endgültige App den jeweiligen aktuellen Plattformdesignrichtlinien vollständig entspricht. Für macOS muss vor Veröffentlichung Dock, Finder, Systemmaskierung und gegebenenfalls Icon Composer geprüft werden. Der eigenständige Marken-Asset bleibt transparent und unverändert.

## 7. Herkunft

Basis ist das von dir freigegebene `DIM-Creator-Logo-v3/icon.png` aus der vorherigen Freigabe. Kein neues Logo erstellt. Ausgabebildgrößen sind technisch exportiert; die exakten Farben des Originals bleiben in den Farbgrafiken enthalten. Die monochromen Tray-Dateien entstehen aus dessen Alpha-Silhouette.
