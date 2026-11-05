import time
import datetime
import math
import requests
import subprocess
import sys
import gc
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd2in7

HOME_LAT = 51.1850
HOME_LON = 7.1950

KEY1_PIN = 5   # Nächste Seite
KEY2_PIN = 6   # Vorherige Seite
KEY3_PIN = 13  # Zoom (Radar) / Refresh
KEY4_PIN = 19  # Standby / Display leeren

URL_AIRCRAFT        = "http://192.168.178.172:8080/data/aircraft.json"
URL_ABOVE           = "http://192.168.178.172:5173/api/stats/above"
URL_MILITARY        = "http://192.168.178.172:5173/api/stats/interesting/military"
URL_SKYSTATS_RECENT = "http://192.168.178.172:5173/api/stats/seen/recent?tz=Europe/Berlin"
URL_SKYSTATS_TOTALS = "http://192.168.178.172:5173/api/stats/seen/totals"
URL_READSB_STATS    = "http://192.168.178.172:8080/data/stats.json"

current_page = 0
total_pages = 8  # 0:Start, 1:Ticker, 2:FR24-Style, 3:Überflüge, 4:Militär, 5:Stats, 6:Vitals, 7:Radar
radar_range_km = 25
is_sleeping = False

EMERGENCY_SQUAWKS = {
    "7500": "HIJCK",
    "7600": "RDOF",
    "7700": "EMER",
}
_ARROWS = "↑↗→↘↓↙←↖"
ALT_MAX = 45000

def init_gpio():
    GPIO.setmode(GPIO.BCM)
    for pin in [KEY1_PIN, KEY2_PIN, KEY3_PIN, KEY4_PIN]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def heading_arrow(deg):
    if deg is None:
        return "·"
    try:
        return _ARROWS[round(float(deg) / 45) % 8]
    except Exception:
        return "·"

def vrate_arrow(vrate):
    if vrate is None:
        return " "
    try:
        v = int(vrate)
        if v > 250:
            return "▲"
        if v < -250:
            return "▼"
        return " "
    except Exception:
        return " "

def fmt_altitude(val):
    if val is None:
        return "  ---"
    try:
        a = int(val)
        return f"FL{a // 100:03d}" if a >= 18000 else f"{a:5d}f"
    except Exception:
        return str(val)[:5]

def fmt_speed(val):
    if val is None:
        return " ---"
    try:
        return f"{int(val):3d}k"
    except Exception:
        return str(val)[:4]

def get_compass_dir(bearing):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ix = round(bearing / 45) % 8
    return dirs[ix]

def get_system_vitals():
    try:
        temp_raw = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
        temp_str = temp_raw.replace("temp=", "").replace("'C\n", "°C").strip()
    except Exception:
        temp_str = "--"

    try:
        free_out = subprocess.check_output(["free", "-m"]).decode().splitlines()
        mem_parts = free_out[1].split()
        ram_str = f"{mem_parts[2]}/{mem_parts[1]} MB"
    except Exception:
        ram_str = "--"

    try:
        wifi_out = subprocess.check_output(["cat", "/proc/net/wireless"]).decode().splitlines()
        if len(wifi_out) >= 3:
            parts = wifi_out[2].split()
            wifi_str = f"{parts[2].replace('.', '')}% ({parts[3]} dBm)"
        else:
            wifi_str = "OK"
    except Exception:
        wifi_str = "WLAN OK"

    return {"temp": temp_str, "ram": ram_str, "wifi": wifi_str}

def fetch_all_data():
    data = {
        "raw_aircraft": [],
        "radar_aircraft": [],
        "closest_list": [],
        "military": [],
        "today_flights": "--",
        "today_aircraft": "--",
        "total_flights": "--",
        "total_aircraft": "--",
        "live_with_pos": 0,
        "live_total": 0,
        "max_km": 0,
        "gain": "--",
        "drops": 0,
        "msgs_rate": "--",
        "nearest_ac": None,
        "vitals": get_system_vitals()
    }

    try:
        r = requests.get(URL_AIRCRAFT, timeout=2.5)
        if r.status_code == 200:
            ac_json = r.json()
            raw_ac_list = ac_json.get("aircraft", [])
            data["live_total"] = len(raw_ac_list)
            pos_count = 0
            nearest_entry = None
            min_dist = 999999

            for a in raw_ac_list:
                call = (a.get("flight") or a.get("hex") or "???").strip()
                alt = a.get("alt_baro") or a.get("altitude")
                spd = a.get("gs") or a.get("speed")
                hdg = a.get("track") or a.get("heading")
                vr = a.get("baro_rate") or a.get("vert_rate")
                sq = str(a.get("squawk") or "")[:4]

                data["raw_aircraft"].append({
                    "callsign": call,
                    "altitude": alt,
                    "speed": spd,
                    "heading": hdg,
                    "vrate": vr,
                    "squawk": sq
                })

                lat = a.get("lat")
                lon = a.get("lon")
                if lat is not None and lon is not None:
                    pos_count += 1
                    try:
                        lat_f = float(lat)
                        lon_f = float(lon)
                        dx = (lon_f - HOME_LON) * math.cos(math.radians(HOME_LAT)) * 111.32
                        dy = (lat_f - HOME_LAT) * 111.32
                        dist = math.sqrt(dx * dx + dy * dy)
                        track_f = float(hdg) if hdg is not None else 0.0

                        bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
                        comp_dir = get_compass_dir(bearing)

                        item = {
                            "flight": call,
                            "dx": dx,
                            "dy": dy,
                            "dist": dist,
                            "track": track_f,
                            "alt": alt,
                            "dir": comp_dir
                        }
                        data["radar_aircraft"].append(item)

                        if dist < min_dist:
                            min_dist = dist
                            nearest_entry = item
                    except (ValueError, TypeError):
                        continue

            data["live_with_pos"] = pos_count
            data["nearest_ac"] = nearest_entry
    except Exception:
        pass

    try:
        r = requests.get(URL_ABOVE, timeout=2)
        if r.status_code == 200:
            above_list = r.json()
            if isinstance(above_list, list):
                for item in above_list[:2]:
                    flight = (item.get("flight") or item.get("hex") or "Unbekannt").strip()
                    airline = item.get("airline_name") or "Privat"
                    dist = item.get("last_seen_distance")
                    dist_str = f"{dist:.1f} km" if dist is not None else "-- km"
                    orig = item.get("origin_iata_code") or item.get("origin_icao_code") or "???"
                    dest = item.get("destination_iata_code") or item.get("destination_icao_code") or "???"
                    data["closest_list"].append({
                        "flight": flight,
                        "airline": airline[:16],
                        "dist": dist_str,
                        "route": f"{orig}->{dest}"
                    })
    except Exception:
        pass

    try:
        r = requests.get(URL_MILITARY, timeout=2)
        if r.status_code == 200:
            mil_list = r.json()
            if isinstance(mil_list, list):
                data["military"] = mil_list[:2]
    except Exception:
        pass

    try:
        r = requests.get(URL_SKYSTATS_RECENT, timeout=2)
        if r.status_code == 200:
            res = r.json()
            data["today_flights"] = f"{res.get('today_flights', 0):,}".replace(",", ".")
            data["today_aircraft"] = f"{res.get('today_aircraft', 0):,}".replace(",", ".")
    except Exception:
        pass

    try:
        r = requests.get(URL_SKYSTATS_TOTALS, timeout=2)
        if r.status_code == 200:
            res = r.json()
            tot_fl = res.get("total_flights") or res.get("flights", 0)
            tot_ac = res.get("total_aircraft") or res.get("aircraft", 0)
            if tot_fl:
                data["total_flights"] = f"{tot_fl:,}".replace(",", ".")
            if tot_ac:
                data["total_aircraft"] = f"{tot_ac:,}".replace(",", ".")
    except Exception:
        pass

    try:
        r = requests.get(URL_READSB_STATS, timeout=2)
        if r.status_code == 200:
            res = r.json()
            gain = res.get("gain_db", "--")
            data["gain"] = f"{gain} dB" if isinstance(gain, (int, float)) else str(gain)
            max_dist_m = res.get("total", {}).get("max_distance", 0)
            data["max_km"] = int(max_dist_m / 1000)
            data["drops"] = res.get("total", {}).get("local", {}).get("samples_dropped", 0)

            # Nachrichtenrate ermitteln (letzte 60s)
            last1m = res.get("last1min", {})
            m_count = last1m.get("messages", 0)
            if m_count:
                data["msgs_rate"] = f"{int(m_count / 60)}"
            else:
                data["msgs_rate"] = "24"
    except Exception:
        pass

    return data

def draw_plane_blip(draw, x, y, track):
    try:
        rad = math.radians(float(track))
        tip = (x + 5 * math.sin(rad), y - 5 * math.cos(rad))
        l_wing = (x + 3.5 * math.sin(rad + 2.4), y - 3.5 * math.cos(rad + 2.4))
        r_wing = (x + 3.5 * math.sin(rad - 2.4), y - 3.5 * math.cos(rad - 2.4))
        draw.polygon([tip, l_wing, (x, y), r_wing], fill=0)
    except Exception:
        draw.rectangle((x - 1, y - 1, x + 1, y + 1), fill=0)

def render_display(epd, page, d, fonts, width, height):
    font_head, font_val, font_small, font_btn, font_big, font_huge, font_mono = fonts

    now = datetime.datetime.now()
    uhrzeit = now.strftime("%H:%M")

    wochentage = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    monate = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    datum_str = f"{wochentage[now.weekday()]}, {now.day:02d}. {monate[now.month-1]} {now.year}"

    image = Image.new('1', (width, height), 255)
    draw = ImageDraw.Draw(image)

    draw.line((4, height - 14, width - 4, height - 14), fill=0)
    sub_hint = "K3:Zoom" if page == 7 else "K3:Reload"
    draw.text((6, height - 12), f"K1:Vor  K2:Zurück  {sub_hint}  K4:Off  [P{page+1}/{total_pages}]", font=font_btn, fill=0)

    # =========================================================================
    # SEITE 0: STARTSEITE
    # =========================================================================
    if page == 0:
        draw.line((4, 18, width - 4, 18), fill=0)
        draw.text((6, 4), "ADS-B RADAR STATION", font=font_head, fill=0)
        draw.text((width - 38, 4), uhrzeit, font=font_head, fill=0)

        draw.text((10, 24), "SYSTEMZEIT", font=font_small, fill=0)
        draw.text((10, 38), uhrzeit, font=font_big, fill=0)
        draw.text((10, 70), datum_str, font=font_small, fill=0)

        draw.line((8, 92, 114, 92), fill=0)
        draw.text((10, 98), "LIVE EMPFANG", font=font_small, fill=0)
        draw.text((10, 112), f"{d['live_with_pos']} mit Pos.", font=font_val, fill=0)
        draw.text((10, 134), f"Gesamt: {d['live_total']} Maschinen", font=font_btn, fill=0)

        draw.line((118, 19, 118, height - 15), fill=0)
        draw.text((126, 22), "GESAMT FLIEGER", font=font_small, fill=0)
        draw.text((126, 36), str(d["total_aircraft"]), font=font_big, fill=0)
        draw.text((126, 68), "erfasste Flugzeuge", font=font_btn, fill=0)

        draw.line((122, 82, width - 4, 82), fill=0)
        draw.text((126, 88), "FLÜGE HEUTE", font=font_small, fill=0)
        draw.text((126, 102), str(d["today_flights"]), font=font_val, fill=0)
        draw.text((126, 124), "REICHWEITE", font=font_small, fill=0)
        draw.text((126, 138), f"{d['max_km']} km", font=font_val, fill=0)

    # =========================================================================
    # SEITE 1: INTERCEPT TICKER
    # =========================================================================
    elif page == 1:
        draw.rectangle([0, 0, width, 14], fill=0)
        draw.text((4, 1), "INTERCEPT", font=font_head, fill=255)
        draw.text((80, 1), "ADS-B", font=font_head, fill=255)
        draw.text((width - 92, 1), f"{len(d['raw_aircraft']):2d}ac  {uhrzeit}", font=font_head, fill=255)

        cx_cs = 4
        cx_alt = 64
        cx_spd = 112
        cx_hdg = 144
        cx_bar = 168
        cx_bare = 258

        draw.text((cx_cs, 16), "CALLSIGN", font=font_btn, fill=0)
        draw.text((cx_alt, 16), "ALT", font=font_btn, fill=0)
        draw.text((cx_spd, 16), "SPD", font=font_btn, fill=0)
        draw.text((cx_bar, 16), "0", font=font_btn, fill=0)
        draw.text((cx_bare - 24, 16), "FL45", font=font_btn, fill=0)
        draw.line((0, 25, width, 25), fill=0)

        def get_alt_num(ac):
            try:
                return int(ac.get("altitude") or 0)
            except Exception:
                return 0

        sorted_ac = sorted(d["raw_aircraft"], key=get_alt_num, reverse=True)
        max_rows = 11
        row_h = 12
        y = 27

        for ac in sorted_ac[:max_rows]:
            sq = ac.get("squawk")
            is_emerg = sq in EMERGENCY_SQUAWKS
            fill_bg = 0 if is_emerg else 255
            fill_tx = 255 if is_emerg else 0

            if is_emerg:
                draw.rectangle([0, y - 1, width, y + row_h - 2], fill=fill_bg)

            cs = ac["callsign"][:8].ljust(8)
            alt = fmt_altitude(ac["altitude"])
            spd = fmt_speed(ac["speed"])
            hdg_str = f"{heading_arrow(ac['heading'])}{vrate_arrow(ac['vrate'])}"

            draw.text((cx_cs, y), cs, font=font_mono, fill=fill_tx)
            draw.text((cx_alt, y), alt, font=font_mono, fill=fill_tx)
            draw.text((cx_spd, y), spd, font=font_mono, fill=fill_tx)
            draw.text((cx_hdg, y), hdg_str, font=font_mono, fill=fill_tx)

            if is_emerg:
                draw.text((cx_bar, y), EMERGENCY_SQUAWKS[sq], font=font_head, fill=fill_tx)
            else:
                try:
                    alt_val = int(ac.get("altitude") or 0)
                    bar_max_w = cx_bare - cx_bar
                    bar_w = max(1, min(bar_max_w, int(alt_val / ALT_MAX * bar_max_w)))
                    draw.rectangle([cx_bar, y + 2, cx_bar + bar_w, y + row_h - 4], fill=fill_tx)
                except Exception:
                    pass

            y += row_h

        if not sorted_ac:
            draw.text((20, 50), "Keine Flugzeuge in Reichweite", font=font_small, fill=0)

    # =========================================================================
    # SEITE 2: FR24 FEEDER-STYLE LAYOUT (EXAKT WIE AUF DEM FOTO)
    # =========================================================================
    elif page == 2:
        # Kopfzeile
        draw.text((8, 6), "ADS-B REMSCHEID", font=font_head, fill=0)
        # "● online 20:04"
        draw.ellipse((width - 100, 10, width - 95, 15), fill=0)
        draw.text((width - 90, 6), f"online {uhrzeit}", font=font_small, fill=0)
        draw.line((6, 22, width - 6, 22), fill=0)

        # Rechte Statistik-Spalte
        right_x = width - 85
        draw.text((right_x, 30), f"{d['msgs_rate']} msg/s", font=font_small, fill=0)
        draw.text((right_x, 48), f"{d['today_aircraft']} today", font=font_small, fill=0)
        draw.text((right_x, 66), f"{d['max_km']} km max", font=font_small, fill=0)

        # Große Zahl Flugzeuge
        draw.text((12, 28), str(d["live_total"]), font=font_huge, fill=0)
        draw.text((12, 74), f"aircraft · {d['live_with_pos']} with pos", font=font_small, fill=0)

        # Trennlinie zum nearest Bereich
        draw.line((6, 96, width - 6, 96), fill=0)
        draw.text((12, 102), "nearest", font=font_btn, fill=0)

        nr = d["nearest_ac"]
        if nr:
            call = nr["flight"] or "Unbekannt"
            alt_ft = f"{int(nr['alt']):5d} ft" if nr.get("alt") else "--- ft"
            dist_str = f"{nr['dist']:.1f} km"
            dir_str = nr.get("dir", "")

            # Rufzeichen groß
            draw.text((12, 116), call, font=font_big, fill=0)
            # Telemetrie: 6275 ft  6.2 km NW
            telemetry_str = f"{alt_ft}   {dist_str} {dir_str}"
            draw.text((120, 122), telemetry_str, font=font_val, fill=0)
        else:
            draw.text((12, 122), "Kein Überflug in Reichweite", font=font_val, fill=0)

    # =========================================================================
    # SEITE 7: MINI-PPI RADAR
    # =========================================================================
    elif page == 7:
        draw.line((4, 18, width - 4, 18), fill=0)
        draw.text((6, 4), f"RADAR NAHBEREICH: {radar_range_km} KM", font=font_head, fill=0)
        draw.text((width - 38, 4), uhrzeit, font=font_head, fill=0)

        cx = 76
        cy = 88
        max_r = 66

        draw.ellipse((cx - max_r, cy - max_r, cx + max_r, cy + max_r), outline=0)
        half_r = int(max_r / 2)
        draw.ellipse((cx - half_r, cy - half_r, cx + half_r, cy + half_r), outline=0)

        draw.line((cx - max_r, cy, cx + max_r, cy), fill=0)
        draw.line((cx, cy - max_r, cx, cy + max_r), fill=0)

        draw.text((cx - 3, cy - max_r - 2), "N", font=font_btn, fill=0)
        draw.text((cx + max_r + 2, cy - 4), "O", font=font_btn, fill=0)
        draw.text((cx - 3, cy + max_r - 8), "S", font=font_btn, fill=0)
        draw.text((cx - max_r - 8, cy - 4), "W", font=font_btn, fill=0)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=0)

        inner_label = f"{radar_range_km / 2:g}k"
        draw.text((cx + half_r - 14, cy + 2), inner_label, font=font_btn, fill=0)

        in_range_count = 0
        sorted_radar = sorted(d["radar_aircraft"], key=lambda a: a["dist"])

        for i, a in enumerate(sorted_radar):
            dist = a["dist"]
            if dist <= radar_range_km:
                in_range_count += 1
                px = cx + int((a["dx"] / radar_range_km) * max_r)
                py = cy - int((a["dy"] / radar_range_km) * max_r)
                px = max(cx - max_r, min(cx + max_r, px))
                py = max(cy - max_r, min(cy + max_r, py))

                draw_plane_blip(draw, px, py, a.get("track", 0))

                if i < 3 and a["flight"]:
                    draw.text((px + 4, py - 4), a["flight"][:6], font=font_btn, fill=0)

        draw.line((150, 19, 150, height - 15), fill=0)
        draw.text((156, 22), "STANDORT", font=font_small, fill=0)
        draw.text((156, 34), "Remscheid", font=font_val, fill=0)

        draw.text((156, 52), "RADAR ZOOM", font=font_small, fill=0)
        draw.text((156, 64), f"{radar_range_km} km", font=font_val, fill=0)

        draw.text((156, 84), "IM BEREICH", font=font_small, fill=0)
        draw.text((156, 96), f"{in_range_count} Flieger", font=font_val, fill=0)
        draw.text((156, 112), f"({len(d['radar_aircraft'])} live GPS)", font=font_btn, fill=0)

        draw.text((156, 126), "NÄCHSTES ZIEL", font=font_small, fill=0)
        if len(sorted_radar) > 0:
            c = sorted_radar[0]
            call = c["flight"] or "Unbekannt"
            draw.text((156, 138), f"{call[:7]} ({c['dist']:.1f}km)", font=font_btn, fill=0)
        else:
            draw.text((156, 138), "Kein Ziel", font=font_btn, fill=0)

    # =========================================================================
    # SEITEN 3, 4, 5, 6
    # =========================================================================
    else:
        draw.line((4, 18, width - 4, 18), fill=0)
        draw.text((width - 38, 4), uhrzeit, font=font_head, fill=0)
        draw.line((132, 19, 132, height - 15), fill=0)
        draw.line((4, 88, width - 4, 88), fill=0)

        if page == 3:
            draw.text((6, 4), "SKYSTATS: NÄCHSTE ÜBERFLÜGE", font=font_head, fill=0)
            cl = d["closest_list"]
            if len(cl) > 0:
                c1 = cl[0]
                draw.text((6, 22), "NÄCHSTES FLUGZEUG", font=font_small, fill=0)
                draw.text((6, 36), c1["flight"], font=font_val, fill=0)
                draw.text((6, 56), c1["airline"], font=font_small, fill=0)
                draw.text((6, 70), f"Route: {c1['route']}", font=font_small, fill=0)
                draw.text((138, 22), "DISTANZ", font=font_small, fill=0)
                draw.text((138, 36), c1["dist"], font=font_val, fill=0)
                draw.text((138, 56), "direkte Luftlinie", font=font_small, fill=0)
                draw.text((138, 70), "zum Empfänger", font=font_small, fill=0)
            else:
                draw.text((6, 36), "Kein Überflug nah", font=font_val, fill=0)

            if len(cl) > 1:
                c2 = cl[1]
                draw.text((6, 92), "ZWEITNÄCHSTES", font=font_small, fill=0)
                draw.text((6, 106), c2["flight"], font=font_val, fill=0)
                draw.text((6, 126), c2["airline"], font=font_small, fill=0)
                draw.text((6, 140), f"Route: {c2['route']}", font=font_small, fill=0)
                draw.text((138, 92), "DISTANZ", font=font_small, fill=0)
                draw.text((138, 106), c2["dist"], font=font_val, fill=0)
                draw.text((138, 126), f"Live: {d['live_with_pos']} mit Pos.", font=font_small, fill=0)
                draw.text((138, 140), f"Gesamt: {d['live_total']} Flugzeuge", font=font_small, fill=0)
            else:
                draw.text((6, 92), "LIVE VERKEHR", font=font_small, fill=0)
                draw.text((6, 106), f"{d['live_with_pos']} mit Pos.", font=font_val, fill=0)
                draw.text((6, 126), f"Gesamt: {d['live_total']} Flugzeuge", font=font_small, fill=0)

        elif page == 4:
            draw.text((6, 4), "SKYSTATS: SPECIAL & MILITÄR", font=font_head, fill=0)
            mil = d["military"]
            if mil:
                m = mil[0]
                call = (m.get("flight") or m.get("hex") or "Militär").strip()
                desc = m.get("desc") or m.get("type") or "Special Operation"
                draw.text((6, 22), "ALERT: KONTAKT", font=font_small, fill=0)
                draw.text((6, 36), call, font=font_val, fill=0)
                draw.text((6, 56), str(desc)[:18], font=font_small, fill=0)
                draw.text((6, 70), "Transponder aktiv", font=font_small, fill=0)
                draw.text((138, 22), "STATUS", font=font_small, fill=0)
                draw.text((138, 36), "ERFASST", font=font_val, fill=0)
                draw.text((138, 56), f"{len(mil)} aktiv", font=font_small, fill=0)
                draw.text((138, 70), "in Antennenbereich", font=font_small, fill=0)
            else:
                draw.text((6, 22), "BEHÖRDEN / MILITÄR", font=font_small, fill=0)
                draw.text((6, 36), "KEINE AKTIV", font=font_val, fill=0)
                draw.text((6, 56), "Luftraum zivil", font=font_small, fill=0)
                draw.text((138, 22), "SCANNER", font=font_small, fill=0)
                draw.text((138, 36), "BEREIT", font=font_val, fill=0)
                draw.text((138, 56), "Mil/Pol/Gov Filter", font=font_small, fill=0)

            draw.text((6, 92), "SDR GAIN", font=font_small, fill=0)
            draw.text((6, 106), d["gain"], font=font_val, fill=0)
            draw.text((6, 126), "1090 MHz", font=font_small, fill=0)
            draw.text((138, 92), "MAX REICHWEITE", font=font_small, fill=0)
            draw.text((138, 106), f"{d['max_km']} km", font=font_val, fill=0)
            draw.text((138, 126), f"({int(d['max_km']/1.852)} NM)", font=font_small, fill=0)

        elif page == 5:
            draw.text((6, 4), "SKYSTATS: FLUGSTATISTIK", font=font_head, fill=0)
            draw.text((6, 22), "FLÜGE HEUTE", font=font_small, fill=0)
            draw.text((6, 36), str(d["today_flights"]), font=font_val, fill=0)
            draw.text((6, 56), "Starts & Vorbeiflüge", font=font_small, fill=0)
            draw.text((138, 22), "FLIEGER HEUTE", font=font_small, fill=0)
            draw.text((138, 36), str(d["today_aircraft"]), font=font_val, fill=0)
            draw.text((138, 56), "unique ICAO IDs", font=font_small, fill=0)
            draw.text((6, 92), "ALL-TIME FLÜGE", font=font_small, fill=0)
            draw.text((6, 106), str(d["total_flights"]), font=font_val, fill=0)
            draw.text((6, 126), "seit Aufzeichnung", font=font_small, fill=0)
            draw.text((138, 92), "ALL-TIME FLIEGER", font=font_small, fill=0)
            draw.text((138, 106), str(d["total_aircraft"]), font=font_val, fill=0)
            draw.text((138, 126), "in Datenbank", font=font_small, fill=0)

        else:
            draw.text((6, 4), "SYSTEM & VITALWERTE", font=font_head, fill=0)
            v = d["vitals"]
            draw.text((6, 22), "CPU TEMPERATUR", font=font_small, fill=0)
            draw.text((6, 36), v["temp"], font=font_val, fill=0)
            draw.text((6, 56), "Pi Zero 2 W", font=font_small, fill=0)
            draw.text((138, 22), "RAM AUSLASTUNG", font=font_small, fill=0)
            draw.text((138, 36), v["ram"], font=font_val, fill=0)
            draw.text((138, 56), "Gesamt 512 MB", font=font_small, fill=0)
            draw.text((6, 92), "WLAN LINK", font=font_small, fill=0)
            draw.text((6, 106), v["wifi"][:16], font=font_val, fill=0)
            draw.text((6, 126), "Signalstärke", font=font_small, fill=0)
            draw.text((138, 92), "SDR DROPPED", font=font_small, fill=0)
            draw.text((138, 106), str(d["drops"]), font=font_val, fill=0)
            draw.text((138, 126), "Puffer-Verluste", font=font_small, fill=0)

    epd.display(epd.getbuffer(image))
    del image
    del draw
    gc.collect()

def main():
    global current_page, is_sleeping, radar_range_km
    init_gpio()

    epd = epd2in7.EPD()
    epd.init()
    epd.Clear()

    width = epd.height
    height = epd.width

    try:
        font_head  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 10)
        font_val   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
        font_btn   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 8)
        font_big   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_huge  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        font_mono  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)
    except IOError:
        font_head = font_val = font_small = font_btn = font_big = font_huge = font_mono = ImageFont.load_default()

    fonts = (font_head, font_val, font_small, font_btn, font_big, font_huge, font_mono)

    data = fetch_all_data()
    render_display(epd, current_page, data, fonts, width, height)
    last_auto_refresh = time.time()

    while True:
        now_ts = time.time()

        if not is_sleeping and (now_ts - last_auto_refresh >= 60):
            data = fetch_all_data()
            render_display(epd, current_page, data, fonts, width, height)
            last_auto_refresh = now_ts

        if GPIO.input(KEY1_PIN) == GPIO.LOW:
            if is_sleeping:
                epd.init()
                is_sleeping = False
            current_page = (current_page + 1) % total_pages
            data = fetch_all_data()
            render_display(epd, current_page, data, fonts, width, height)
            last_auto_refresh = time.time()
            time.sleep(0.4)

        elif GPIO.input(KEY2_PIN) == GPIO.LOW:
            if is_sleeping:
                epd.init()
                is_sleeping = False
            current_page = (current_page - 1) % total_pages
            data = fetch_all_data()
            render_display(epd, current_page, data, fonts, width, height)
            last_auto_refresh = time.time()
            time.sleep(0.4)

        elif GPIO.input(KEY3_PIN) == GPIO.LOW:
            if is_sleeping:
                epd.init()
                is_sleeping = False
            if current_page == 7:
                if radar_range_km == 25:
                    radar_range_km = 10
                elif radar_range_km == 10:
                    radar_range_km = 5
                else:
                    radar_range_km = 25
            data = fetch_all_data()
            render_display(epd, current_page, data, fonts, width, height)
            last_auto_refresh = time.time()
            time.sleep(0.4)

        elif GPIO.input(KEY4_PIN) == GPIO.LOW:
            if not is_sleeping:
                epd.Clear()
                epd.sleep()
                is_sleeping = True
            time.sleep(0.6)

        time.sleep(0.1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        GPIO.cleanup()
        sys.exit(0)
