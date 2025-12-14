import csv
import os
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

import DataFetcher


RUNWAYS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"


def _ft_to_nm(feet: float) -> float:
    # 1 ft = 0.3048 m, 1 NM = 1852 m
    return (feet * 0.3048) / 1852.0


def _safe_float(value: str) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip().strip('"')
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _safe_int(value: str) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip().strip('"')
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def get_default_data_dir(path_mod: str) -> str:
    """Return a writable data directory for cached downloads."""
    if path_mod:
        return os.path.join(path_mod, "data")
    # Fallback: repo-local data directory.
    return os.path.join(os.path.dirname(__file__), "data")


def ensure_runways_csv(path_mod: str, url: str = RUNWAYS_CSV_URL, filename: str = "runways.csv") -> str:
    """Ensure runways CSV exists locally; download if missing."""
    data_dir = get_default_data_dir(path_mod)
    os.makedirs(data_dir, exist_ok=True)

    dest_path = os.path.join(data_dir, filename)
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        return dest_path

    tmp_path = dest_path + ".tmp"
    with requests.get(url, stream=True, timeout=20) as r:
        r.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)

    os.replace(tmp_path, dest_path)
    return dest_path


@dataclass(frozen=True)
class RunwayOverlay:
    le_dis_nm: float
    le_ang_deg: float
    he_dis_nm: float
    he_ang_deg: float
    width_ft: float
    center_dis_nm: float


class RunwaysIndex:
    """Thread-safe in-memory runway overlays sorted by center distance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runways_sorted: List[RunwayOverlay] = []
        self._ready = False
        self._error: Optional[str] = None
        self._loaded_at_ts: float = 0.0
        self._version: int = 0

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    @property
    def version(self) -> int:
        """Monotonic counter incremented when runway data is replaced."""
        with self._lock:
            return self._version

    def set_error(self, msg: str) -> None:
        with self._lock:
            self._error = msg
            self._ready = False

    def set_data(self, runways_sorted: List[RunwayOverlay]) -> None:
        with self._lock:
            self._runways_sorted = runways_sorted
            self._ready = True
            self._error = None
            self._loaded_at_ts = time.time()
            self._version += 1

    def query_by_max_distance_nm(self, max_distance_nm: float) -> List[RunwayOverlay]:
        """Return runways with center distance <= max_distance_nm."""
        with self._lock:
            if not self._ready or not self._runways_sorted:
                return []
            # Manual bisect to avoid importing bisect for one use.
            lo = 0
            hi = len(self._runways_sorted)
            while lo < hi:
                mid = (lo + hi) // 2
                if self._runways_sorted[mid].center_dis_nm <= max_distance_nm:
                    lo = mid + 1
                else:
                    hi = mid
            return self._runways_sorted[:lo]


def build_index_for_home(csv_path: str, home_pos) -> List[RunwayOverlay]:
    runways: List[RunwayOverlay] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            le_lat = _safe_float(row.get("le_latitude_deg"))
            le_lng = _safe_float(row.get("le_longitude_deg"))
            he_lat = _safe_float(row.get("he_latitude_deg"))
            he_lng = _safe_float(row.get("he_longitude_deg"))
            width_ft = _safe_float(row.get("width_ft"))

            if le_lat is None or le_lng is None or he_lat is None or he_lng is None:
                continue
            if width_ft is None:
                width_ft = 0.0

            # DataFetcher.AngleCalc returns distance in meters and azimuth in degrees.
            le_vec = DataFetcher.AngleCalc(home_pos, 0, le_lat, le_lng)
            he_vec = DataFetcher.AngleCalc(home_pos, 0, he_lat, he_lng)

            le_dis_nm = float(le_vec[0]) / 1852.0
            he_dis_nm = float(he_vec[0]) / 1852.0
            le_ang = float(le_vec[1])
            he_ang = float(he_vec[1])

            center_dis_nm = (le_dis_nm + he_dis_nm) / 2.0

            runways.append(
                RunwayOverlay(
                    le_dis_nm=le_dis_nm,
                    le_ang_deg=le_ang,
                    he_dis_nm=he_dis_nm,
                    he_ang_deg=he_ang,
                    width_ft=float(width_ft),
                    center_dis_nm=center_dis_nm,
                )
            )

    runways.sort(key=lambda r: r.center_dis_nm)
    return runways


def start_background_load(index: RunwaysIndex, path_mod: str, home_pos) -> None:
    """Download (if needed) and build the runway index on a background thread."""

    def _worker() -> None:
        try:
            csv_path = ensure_runways_csv(path_mod)
            data = build_index_for_home(csv_path, home_pos)
            index.set_data(data)
        except Exception as e:
            index.set_error(f"runways load failed: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
