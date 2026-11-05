# SkyStats ADS-B e-Paper Dashboard (Waveshare 2.7")

Autonomes ADS-B Status- und Telemetrie-Display für den **Raspberry Pi Zero 2 W** mit **Waveshare 2.7" e-Paper HAT** (264x176 Pixel).

## Features
- **Seite 0:** Startseite (Uhrzeit, Datum, Gesamtzahlen & Status)
- **Seite 1:** Intercept ADS-B Ticker (Callsign, FL, Speed, Track, V-Rate & Höhenbalken bis FL450)
- **Seite 2:** FR24 Feeder Dashboard (Kompakte Stationswerte, Msg/s, Nearest Flight mit Distanz & Himmelsrichtung)
- **Seite 3:** Nächste Überflüge (SkyStats API `/api/stats/above`)
- **Seite 4:** Special & Militär-Watch (SkyStats API `/api/stats/interesting/military`)
- **Seite 5:** Tages- und Allzeitstatistiken
- **Seite 6:** System & Vitalwerte (CPU-Temp, RAM, WLAN-Pegel, SDR-Drops)
- **Seite 7:** Mini-PPI-Radar (Echte Koordinatenprojektion, umschaltbar: 25 km / 10 km / 5 km)

## Tastensteuerung (Waveshare Onboard Buttons)
- **Key 1 (GPIO 5):** Nächste Seite
- **Key 2 (GPIO 6):** Vorherige Seite
- **Key 3 (GPIO 13):** Radar-Zoom (auf Seite 7: 25 -> 10 -> 5 km) / Sofortiger Refresh
- **Key 4 (GPIO 19):** Display ausschalten (Standby / Deep Sleep)

## Auto-Refresh
Jede aktive Seite aktualisiert sich alle 60 Sekunden eigenständig im Hintergrund, ohne auf die Startseite zurückzuspringen.
