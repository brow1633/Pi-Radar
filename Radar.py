import pygame
from pygame import gfxdraw
from pygame.locals import *
import time
import math
import copy
from collections import deque
import DataFetcher
import Classes
import Drawer
import Menu
import threading
import os
from queue import Empty, Queue
import Runways

version = "0.2.0"

# pygame setup
pygame.init()

screen = pygame.display.set_mode((1080, 1080),pygame.FULLSCREEN|SCALED)
clock = pygame.time.Clock()
dt = 0

path_mod = ""

if os.name == 'nt':
    font1 = pygame.font.SysFont('ocrastdopentype', 15)
    font2 = pygame.font.SysFont('ocrastdopentype', 20)
    font3 = pygame.font.SysFont('ocrastdopentype', 25)
elif os.name == 'posix' or os.name != 'nt':
    path_mod = os.path.join(os.path.join(os.path.expanduser('~')), '.config') + "/pi-radar/"
    # pygame.mouse.set_visible(False)
    font1 = pygame.font.SysFont('quicksand', 15)
    font2 = pygame.font.SysFont('quicksand', 20)
    font3 = pygame.font.SysFont('quicksand', 25)

fonts = [font1,font2,font3]

mouse_down = [False,False]

t0 = time.time()

#mode = 1 #1 - Analog Radar, 2 - Digital Radar
sweep_angle = 270

b_key_plus_pressed = False
b_key_minus_pressed = False

TRAIL_WINDOW_SEC = 500
TRAIL_SAMPLE_MIN_DT_SEC = 0.5
TRAIL_MAX_POINTS = 500
TRAIL_CLEANUP_PERIOD_SEC = 1.0


opts = Classes.Options()
opts.vers = version

rdr_tgts = {}
data_lock = threading.Lock()
raw_tgts = []
raw_tgts_new = []
fetch_thread = None
fetch_in_progress = False
detail_thread = None
detail_fetch_in_progress = set()
detail_cache = {}
detail_queue = Queue()

fps = 0
dwnl_stats = [1,0] #0 - Total Downloads, #1 - Errors

menu_modes = [False,0,0] #0 - Open, 
menu_level = 0

run = True
UIElements = []
selected_hex = None
trails = {}
trail_last_sample_ts = {}
trail_last_cleanup_ts = 0.0

runways_index = Runways.RunwaysIndex()

# Range per ring (total radius is dis_range * 5).
# Totals: 5, 10, 15, 20, 25, 50
RANGE_STEPS = [1, 2, 3, 4, 5, 10]

opts = Menu.LoadOptions(path_mod,opts)

# Start background runways download/load at startup.
if opts.config_ok:
    Runways.start_background_load(runways_index, path_mod, opts.homePos)
else:
    threading.Thread(target=lambda: Runways.ensure_runways_csv(path_mod), daemon=True).start()

# Use adsb.lol API if no local URL has been defined.
if len(opts.url) < 2:
    opts.url = DataFetcher.PUBLIC_ADSB_LOL_URL_TEMPLATE.format(
        lat=opts.homePos.lat,
        lon=opts.homePos.lng,
        radius=250,
    )
    opts.source = "adsb.lol API"
else:
    opts.source = "Local URL: " + opts.url

def Stop():
    global run
    run = False

def SelectTargetAt(mouse_pos):
    """Return the closest radar target to the click position within pick radius."""
    nearest = None
    nearest_dist = 9999

    min_alt_ft = int(getattr(opts, "min_alt_ft", 0) or 0)

    for tgt in rdr_tgts.values():
        if min_alt_ft > 0 and getattr(tgt, "alt", -999) not in (None, -999) and tgt.alt < min_alt_ft:
            continue
        dist = math.hypot(mouse_pos[0] - tgt.pos_x, mouse_pos[1] - tgt.pos_y)
        if dist < nearest_dist:
            nearest = tgt
            nearest_dist = dist

    if nearest is not None and nearest_dist <= 25:
        return copy.copy(nearest)

    return None

def UpdateTrails(active_targets):
    """Store recent dis/ang samples for each target keyed by hex."""
    global trails, trail_last_sample_ts, trail_last_cleanup_ts
    now_ts = time.time()

    if not active_targets:
        return

    # Accept dicts (hex -> RadarTarget) or iterables of targets.
    targets_iter = active_targets.values() if isinstance(active_targets, dict) else active_targets

    min_alt_ft = int(getattr(opts, "min_alt_ft", 0) or 0)

    for tgt in list(targets_iter):
        if tgt is None or not getattr(tgt, "hex", None):
            continue
        if min_alt_ft > 0 and getattr(tgt, "alt", -999) not in (None, -999) and tgt.alt < min_alt_ft:
            continue
        if tgt.dis is None or tgt.ang is None:
            continue

        hex_id = tgt.hex

        last_ts = trail_last_sample_ts.get(hex_id, 0.0)
        if (now_ts - last_ts) < TRAIL_SAMPLE_MIN_DT_SEC:
            continue

        hist = trails.get(hex_id)
        if hist is None:
            # Cap points so memory/CPU stays bounded even with long windows.
            max_points = max(2, min(TRAIL_MAX_POINTS, int(opts.trail_length_s / TRAIL_SAMPLE_MIN_DT_SEC) + 1))
            hist = deque(maxlen=max_points)
            trails[hex_id] = hist

        # Avoid adding near-identical points (helps when data updates faster than motion).
        if hist:
            last = hist[-1]
            if abs(last.get("dis", 0.0) - tgt.dis) < 0.01 and abs(last.get("ang", 0.0) - tgt.ang) < 0.1:
                trail_last_sample_ts[hex_id] = now_ts
                continue

        hist.append({"dis": tgt.dis, "ang": tgt.ang, "ts": now_ts})
        trail_last_sample_ts[hex_id] = now_ts

    # Cleanup old points periodically (not every frame).
    if (now_ts - trail_last_cleanup_ts) >= TRAIL_CLEANUP_PERIOD_SEC:
        cutoff = now_ts - opts.trail_length_s
        for hex_id, hist in list(trails.items()):
            while hist and hist[0].get("ts", 0) < cutoff:
                hist.popleft()
            if not hist:
                trails.pop(hex_id, None)
                trail_last_sample_ts.pop(hex_id, None)
        trail_last_cleanup_ts = now_ts

def DataProcessing():
    global raw_tgts_new
    global opts
    global dwnl_stats
    global fetch_in_progress
    
    try:
        if opts.config_ok:
            raw_tgts_tmp = DataFetcher.fetchADSBData(opts.homePos,opts.url)
            with data_lock:
                raw_tgts_new = raw_tgts_tmp
            dwnl_stats[0] += 1
            if raw_tgts_new is None:
                dwnl_stats[1] += 1
    finally:
        with data_lock:
            fetch_in_progress = False


def _detail_cache_key(tgt):
    if tgt is None:
        return ""
    return (
        getattr(tgt, "detail_lookup_key", "")
        or getattr(tgt, "hex", "")
        or DataFetcher.get_target_callsign(tgt)
        or getattr(tgt, "reg", "")
    )


def QueueTargetDetails(tgt):
    if tgt is None:
        return

    cache_key = _detail_cache_key(tgt)
    tgt.detail_lookup_key = cache_key
    if not cache_key:
        return

    cached = detail_cache.get(cache_key)
    if cached is not None:
        DataFetcher.apply_details_to_target(tgt, cached)
        tgt.details_requested = True
        return

    if cache_key in detail_fetch_in_progress:
        tgt.details_requested = True
        return

    callsign = DataFetcher.get_target_callsign(tgt)
    if not callsign or getattr(tgt, "lat", -999) == -999 or getattr(tgt, "lng", -999) == -999:
        return

    tgt.details_requested = True
    detail_fetch_in_progress.add(cache_key)
    detail_queue.put(
        {
            "cache_key": cache_key,
            "callsign": callsign,
            "lat": tgt.lat,
            "lon": tgt.lng,
        }
    )


def SyncCachedDetails(targets):
    if not targets:
        return

    targets_iter = targets.values() if isinstance(targets, dict) else targets
    for tgt in list(targets_iter):
        if tgt is None:
            continue
        cache_key = _detail_cache_key(tgt)
        if not cache_key:
            continue
        cached = detail_cache.get(cache_key)
        if cached is not None:
            DataFetcher.apply_details_to_target(tgt, cached)


def DetailWorker():
    while run:
        try:
            job = detail_queue.get(timeout=0.25)
        except Empty:
            continue

        cache_key = job.get("cache_key", "")
        try:
            detail_target = Classes.Aircraft()
            detail_target.flt = job.get("callsign", "")
            detail_target.lat = job.get("lat", -999)
            detail_target.lng = job.get("lon", -999)
            details = DataFetcher.fetch_route_details_for_target(detail_target)
            detail_cache[cache_key] = details
            with data_lock:
                SyncCachedDetails(raw_tgts)
                SyncCachedDetails(raw_tgts_new)
                SyncCachedDetails(rdr_tgts)
        except Exception as error:
            print("Detail Fetch Error: ", error)
            detail_cache[cache_key] = {
                "route_label": "",
                "airline_code": "",
                "logo_path": "",
            }
        finally:
            detail_fetch_in_progress.discard(cache_key)
            detail_queue.task_done()

def StartFetchThread():
    global fetch_thread, fetch_in_progress
    with data_lock:
        if fetch_in_progress:
            return
        fetch_in_progress = True

    fetch_thread = threading.Thread(target=task1, daemon=True)
    fetch_thread.start()


def StartDetailThread():
    global detail_thread
    if detail_thread is not None and detail_thread.is_alive():
        return

    detail_thread = threading.Thread(target=DetailWorker, daemon=True)
    detail_thread.start()

def DataDrawing():
    global raw_tgts, raw_tgts_new
    global run, screen, sweep_angle, menu_modes
    global fps, opts
    global UIElements, selected_hex, trails

    while run:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                run = False
            elif (event.type == pygame.MOUSEBUTTONDOWN or event.type == pygame.FINGERDOWN) and not mouse_down[0]:
                mouse_down[0] = True
                mousePos = pygame.mouse.get_pos()

                if not menu_modes[0]:
                    hit = SelectTargetAt(mousePos)
                    if hit is not None:
                        selected_hex = hit.hex
                    else:
                        if selected_hex is not None:
                            selected_hex = None
                        else:
                            menu_modes[0] = True
                            menu_level = 0
                else:
                    for UIElement in UIElements:
                        if isinstance(UIElement, Classes.Button):
                            if UIElement.rect.collidepoint(mousePos):
                                if UIElement.tag =="RETURN":
                                    if menu_level > 0:
                                        menu_level -= 1
                                    else:
                                        menu_modes[0] = False
                                        selected_hex = None
                                if menu_level == 0:
                                    if UIElement.tag == "EXIT":
                                        run = False
                                    if UIElement.tag == "MODE_UP":
                                        if opts.mode < 3:
                                            opts.mode += 1
                                            rdr_tgts.clear()
                                    if UIElement.tag == "MODE_DN":
                                        if opts.mode > 0:
                                            opts.mode -= 1
                                            rdr_tgts.clear()
                                    if UIElement.tag == "RNG_UP":
                                        if opts.dis_range in RANGE_STEPS:
                                            i = RANGE_STEPS.index(opts.dis_range)
                                            if i < (len(RANGE_STEPS) - 1):
                                                opts.dis_range = RANGE_STEPS[i + 1]
                                                if opts.mode == 3:
                                                    rdr_tgts.clear()
                                    if UIElement.tag == "RNG_DN":
                                        if opts.dis_range in RANGE_STEPS:
                                            i = RANGE_STEPS.index(opts.dis_range)
                                            if i > 0:
                                                opts.dis_range = RANGE_STEPS[i - 1]
                                                if opts.mode == 3:
                                                    rdr_tgts.clear()
                                    if UIElement.tag == "OPTIONS":
                                        menu_level = 1
                                elif menu_level == 1:
                                    if "DEBUG" in UIElement.tag:
                                       opts.debug = UIElement.tag.split("_")[1] == "True"

                                    if "GRID" in UIElement.tag:
                                        opts.grid = UIElement.tag.split("_")[1] == "True"

                                    if "METRIC" in UIElement.tag:
                                        opts.metric = UIElement.tag.split("_")[1] == "True"

                                    if "SAVE" in UIElement.tag:
                                        Menu.SaveOptions(path_mod,opts)            

            elif event.type == pygame.MOUSEBUTTONUP or event.type == pygame.FINGERUP:
                mouse_down[0] = False

        if opts.config_ok:
            UpdateTrails(rdr_tgts)
            with data_lock:
                SyncCachedDetails(raw_tgts)
                SyncCachedDetails(raw_tgts_new)
                SyncCachedDetails(rdr_tgts)
            selected_trail = []
            selected_target = None
            if selected_hex is not None:
                selected_target = rdr_tgts.get(selected_hex)
                if selected_target is not None:
                    QueueTargetDetails(selected_target)
                    cached = detail_cache.get(_detail_cache_key(selected_target))
                    if cached is not None:
                        DataFetcher.apply_details_to_target(selected_target, cached)
                    selected_trail = list(trails.get(selected_hex, ()))

            with data_lock:
                Drawer.Draw(opts.mode,screen,raw_tgts,rdr_tgts,opts.dis_range,sweep_angle,fonts,opts,selected_target,selected_trail,runways_index)
            if opts.debug:
                Drawer.DrawDebugInfo(screen,fonts,opts.mode,fps,dwnl_stats)
        else:
            Drawer.DrawConfigError(screen,fonts)            
        
        if menu_modes[0]:
            UIElements = Menu.Main(screen,menu_level,opts)
            for UIElement in UIElements:
                Drawer.DrawUI(screen,fonts,UIElement)

        keys = pygame.key.get_pressed()
        if keys[pygame.K_PLUS] and opts.dis_range in RANGE_STEPS:
            if not b_key_plus_pressed:
                i = RANGE_STEPS.index(opts.dis_range)
                if i < (len(RANGE_STEPS) - 1):
                    opts.dis_range = RANGE_STEPS[i + 1]
                b_key_plus_pressed = True
                if opts.mode == 3:
                    rdr_tgts.clear()
        else:
            b_key_plus_pressed = False
        
        if keys[pygame.K_MINUS] and opts.dis_range in RANGE_STEPS:
            if not b_key_minus_pressed:
                i = RANGE_STEPS.index(opts.dis_range)
                if i > 0:
                    opts.dis_range = RANGE_STEPS[i - 1]
                b_key_minus_pressed = True
                if opts.mode == 3:
                    rdr_tgts.clear()
        else:
            b_key_minus_pressed = False
        
        sweep_angle += 0.8
        with data_lock:
            needs_mid_sweep_fetch = raw_tgts_new is None and not fetch_in_progress

        if needs_mid_sweep_fetch:
            if sweep_angle > 180 and sweep_angle < 180 + 40 * dt:
                StartFetchThread()
        
        if sweep_angle > 359:
            with data_lock:
                raw_tgts = raw_tgts_new
                raw_tgts_new = None
            sweep_angle = 0
            StartFetchThread()

        pygame.display.flip()
        dt = clock.tick(40) / 1000
        fps = round(clock.get_fps(),0)

def task1():
    DataProcessing()

StartFetchThread()
StartDetailThread()

DataDrawing()

Menu.SaveOptions(path_mod,opts)
pygame.quit()
