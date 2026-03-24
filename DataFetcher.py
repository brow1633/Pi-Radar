import json
import math
import os

import Classes
import requests


ADSB_LOL_ROUTESET_URL = "https://api.adsb.lol/api/0/routeset"
ADSDB_CALLSIGN_URL_TEMPLATE = "https://api.adsbdb.com/v0/callsign/{callsign}"
PUBLIC_ADSB_LOL_URL_TEMPLATE = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{radius}"
LOGOS_DIR = os.path.join(os.path.dirname(__file__), "logos")
ROUTE_ARROW = ">"


def _extract_aircraft_list(aircraft_data):
    if isinstance(aircraft_data, dict):
        if isinstance(aircraft_data.get("ac"), list):
            return aircraft_data["ac"]
        if isinstance(aircraft_data.get("aircraft"), list):
            return aircraft_data["aircraft"]
    return []


def _safe_strip(value):
    if value is None:
        return ""
    return str(value).strip()


def get_target_callsign(target):
    if target is None:
        return ""

    for attr in ("flt", "cls", "reg"):
        value = _safe_strip(getattr(target, attr, ""))
        if value:
            return value
    return ""


def _coerce_float(value, default=-999):
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = _safe_strip(value)
    if not text:
        return default

    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _coerce_int(value, default=-999):
    numeric = _coerce_float(value, default=None)
    if numeric is None:
        return default
    try:
        return int(round(float(numeric)))
    except (TypeError, ValueError):
        return default


def _build_detail_lookup_key(tgt):
    candidates = [
        _safe_strip(getattr(tgt, "hex", "")),
        get_target_callsign(tgt),
        _safe_strip(getattr(tgt, "reg", "")),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return ""


def _match_logo_filename(airline_code):
    airline_code = _safe_strip(airline_code).upper()
    if not airline_code or not os.path.isdir(LOGOS_DIR):
        return ""

    try:
        for filename in os.listdir(LOGOS_DIR):
            path = os.path.join(LOGOS_DIR, filename)
            if os.path.isfile(path) and filename.upper().startswith(f"{airline_code}."):
                return path
    except OSError:
        return ""

    return ""


def _build_route_label(route_entry):
    if not isinstance(route_entry, dict):
        return ""

    route_iata = route_entry.get("_airport_codes_iata") or []
    if isinstance(route_iata, list) and len(route_iata) >= 2:
        origin = _safe_strip(route_iata[0]).upper()
        dest = _safe_strip(route_iata[-1]).upper()
        if origin and dest:
            return f"{origin} {ROUTE_ARROW} {dest}"

    airport_codes = route_entry.get("airport_codes") or []
    if isinstance(airport_codes, list) and len(airport_codes) >= 2:
        origin = _safe_strip(airport_codes[0]).upper()
        dest = _safe_strip(airport_codes[-1]).upper()
        if origin and dest:
            return f"{origin} {ROUTE_ARROW} {dest}"

    return ""


def build_route_details(route_response_json):
    details = {
        "route_label": "",
        "airline_code": "",
        "logo_path": "",
    }

    if isinstance(route_response_json, list) and route_response_json:
        route_entry = route_response_json[0]
        if isinstance(route_entry, dict):
            details["route_label"] = _build_route_label(route_entry)
            details["airline_code"] = _safe_strip(route_entry.get("airline_code", "")).upper()
            details["logo_path"] = _match_logo_filename(details["airline_code"])
        return details

    if isinstance(route_response_json, dict):
        flightroute = route_response_json.get("response", {}).get("flightroute", {})
        if isinstance(flightroute, dict):
            origin = _safe_strip(flightroute.get("origin", {}).get("iata_code", "")).upper()
            destination = _safe_strip(flightroute.get("destination", {}).get("iata_code", "")).upper()
            if origin and destination:
                details["route_label"] = f"{origin} {ROUTE_ARROW} {destination}"
            details["airline_code"] = _safe_strip(flightroute.get("airline", {}).get("icao", "")).upper()
            details["logo_path"] = _match_logo_filename(details["airline_code"])
        return details

    return details


def get_route_info_json(callsign, lat, lon, session=None):
    callsign = _safe_strip(callsign)
    if not callsign:
        return None

    planes = {
        "planes": [
            {
                "callsign": callsign,
                "lat": lat,
                "lng": lon,
            }
        ]
    }

    client = session or requests
    try:
        response = client.post(
            ADSB_LOL_ROUTESET_URL,
            data=json.dumps(planes),
            timeout=10,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if response.status_code == 200:
            return response.json()
    except Exception as error:
        print("Route Detail Error: ", error)
        return None

    return None


def get_adsbdb_route_info_json(callsign, session=None):
    callsign = _safe_strip(callsign)
    if not callsign:
        return None

    client = session or requests
    try:
        response = client.get(
            ADSDB_CALLSIGN_URL_TEMPLATE.format(callsign=callsign),
            timeout=10,
            headers={"Accept": "application/json"},
        )
        if response.status_code == 200:
            return response.json()
    except Exception as error:
        print("Fallback Route Detail Error: ", error)
        return None

    return None


def fetch_route_details_for_target(target, session=None):
    details = {
        "route_label": "",
        "airline_code": "",
        "logo_path": "",
    }

    callsign = _safe_strip(getattr(target, "flt", ""))
    lat = getattr(target, "lat", None)
    lon = getattr(target, "lng", None)

    if not callsign or lat in (None, -999) or lon in (None, -999):
        return details

    fallback_response_json = get_adsbdb_route_info_json(callsign, session=session)
    details = build_route_details(fallback_response_json)
    if details["route_label"] or details["airline_code"]:
        return details

    route_response_json = get_route_info_json(callsign, lat, lon, session=session)
    return build_route_details(route_response_json)


def apply_details_to_target(target, details):
    if target is None or not isinstance(details, dict):
        return target

    target.route_label = _safe_strip(details.get("route_label", ""))
    target.airline_code = _safe_strip(details.get("airline_code", ""))
    target.logo_path = _safe_strip(details.get("logo_path", ""))
    return target


def fetchADSBData(homePos, url, session=None):
    tgts = []
    client = session or requests

    try:
        r = client.get(url, timeout=4)
        aircraft_data = r.json()

        dedup = {}
        for a in _extract_aircraft_list(aircraft_data):
            timestmp = aircraft_data.get("now")

            tgt = Classes.Aircraft()

            tgt.hex = a.get("hex")
            tgt.lat = _coerce_float(a.get("lat"))
            tgt.lng = _coerce_float(a.get("lon"))
            tgt.flt = _safe_strip(a.get("flight"))
            tgt.reg = _safe_strip(a.get("r") or a.get("registration") or a.get("reg"))
            tgt.swk = a.get("squawk")
            tgt.alt = _coerce_int(a.get("alt_geom"))
            if tgt.alt is None:
                tgt.alt = _coerce_int(a.get("alt_baro"))
            if tgt.alt == -999:
                tgt.alt = _coerce_int(a.get("alt_baro"))
            tgt.spd = _coerce_float(a.get("gs"))
            tgt.trk = _coerce_float(a.get("track"))
            tgt.cat = a.get("category")
            tgt.type = _safe_strip(a.get("t") or a.get("type"))

            seen_pos = a.get("seen_pos")
            if seen_pos is None:
                seen_pos = a.get("seen")

            if tgt.reg is None or len(tgt.reg) < 1:
                tgt.reg = tgt.hex

            if tgt.flt is None or len(tgt.flt) < 1:
                tgt.flt = tgt.reg

            if tgt.swk is None:
                tgt.swk = 9999

            if seen_pos is None:
                seen_pos = 0

            tgt.time = _coerce_float(seen_pos, default=0)
            tgt.detail_lookup_key = _build_detail_lookup_key(tgt)
            tgt.details_requested = False

            if tgt.alt != -999 and tgt.lat != -999 and tgt.lng != -999 and tgt.spd != -999:
                vector = AngleCalc(homePos, tgt.alt, tgt.lat, tgt.lng)
                tgt.dis = round(vector[0] / 1852, 3)
                tgt.ang = round(vector[1], 1)
                if tgt.hex in dedup:
                    if tgt.time < dedup[tgt.hex].time:
                        dedup[tgt.hex] = tgt
                else:
                    dedup[tgt.hex] = tgt

        return list(dedup.values())
    except Exception as error:
        print("Data Download Error: ", error)
        return None


def AngleCalc(homePos, alt_buff, lat_buff, lng_buff):
    dis_buff = 999.9
    dis_2D = 999.9

    dis_3D = 999.9

    d_alt = alt_buff * 0.3048

    d_alt = d_alt - homePos.alt

    d_lat = lat_buff - homePos.lat
    d_lng = lng_buff - homePos.lng

    d_lat = d_lat * 60
    d_lng = d_lng * 60 * math.cos(homePos.lat * math.pi / 180)

    d_lat = d_lat * 1852
    d_lng = d_lng * 1852

    d_dis = math.sqrt(d_lat * d_lat + d_lng * d_lng)
    dis_2D = d_dis

    azi = math.acos(d_lat / dis_2D) * 180 / math.pi

    if d_lng < 0:
        azi = 360 - azi
    else:
        azi = azi

    return [dis_2D, azi]
