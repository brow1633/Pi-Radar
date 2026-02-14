import pygame
from pygame import gfxdraw
import Classes
import math
import time


_RUNWAYS_OVERLAY_CACHE_KEY = None
_RUNWAYS_OVERLAY_CACHE_SURFACE = None

_MARKINGS_CACHE_KEY = None
_MARKINGS_CACHE_SURFACE = None

_GRID_CACHE_KEY = None
_GRID_CACHE_SURFACE = None

_INFOBOX_BG_CACHE = {}

_TEXT_CACHE = {}
_TEXT_CACHE_MAX = 512

TARGET_STALE_SEC = 60


def _target_is_fresh(rdr_tgt, now_ts=None):
    """Return True when a target has been updated recently enough to draw."""
    if now_ts is None:
        now_ts = time.time()

    last_seen_ts = getattr(rdr_tgt, "last_seen_ts", None)
    if last_seen_ts is not None:
        return (now_ts - last_seen_ts) <= TARGET_STALE_SEC

    age = getattr(rdr_tgt, "age", None)
    if age in (None, -999):
        return True
    return age <= TARGET_STALE_SEC


def _render_text_cached(font, text: str, antialias: bool, color):
    key = (id(font), text, bool(antialias), tuple(color))
    img = _TEXT_CACHE.get(key)
    if img is None:
        if len(_TEXT_CACHE) >= _TEXT_CACHE_MAX:
            _TEXT_CACHE.clear()
        img = font.render(text, antialias, color)
        _TEXT_CACHE[key] = img
    return img


def _get_grid_overlay_surface(screen, grid_space: int, color_rgb):
    global _GRID_CACHE_KEY, _GRID_CACHE_SURFACE

    w = screen.get_width()
    h = screen.get_height()
    cache_key = (w, h, int(grid_space), tuple(color_rgb))

    if _GRID_CACHE_KEY != cache_key or _GRID_CACHE_SURFACE is None:
        s = pygame.Surface((w, h)).convert()
        key = (1, 2, 3)
        s.fill(key)
        s.set_colorkey(key)

        cx = w / 2
        cy = h / 2
        for i in range(-7, 7):
            y = cy + grid_space * i + 1
            x = cx + grid_space * i + 1
            pygame.draw.line(s, color=color_rgb, start_pos=[0, y], end_pos=[w, y], width=1)
            pygame.draw.line(s, color=color_rgb, start_pos=[x, 0], end_pos=[x, h], width=1)

        _GRID_CACHE_SURFACE = s
        _GRID_CACHE_KEY = cache_key

    return _GRID_CACHE_SURFACE


def _get_infobox_bg_surface(box_w: int, box_h: int, box_alpha: int):
    key = (int(box_w), int(box_h), int(box_alpha))
    s = _INFOBOX_BG_CACHE.get(key)
    if s is None:
        s = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        s.fill((15, 15, 15, box_alpha))
        _INFOBOX_BG_CACHE[key] = s
    return s


def _polar_to_screen(screen, dis_nm: float, ang_deg: float, dis_range: float, conv_fact: float):
    cx = screen.get_width() / 2
    cy = screen.get_height() / 2
    x = cx + math.sin(ang_deg * math.pi / 180) * dis_nm * 100 / dis_range * conv_fact
    y = cy - math.cos(ang_deg * math.pi / 180) * dis_nm * 100 / dis_range * conv_fact
    return x, y


def _build_runways_overlay_surface(screen, dis_range, opts, runways):
    # Fast overlay: colorkey surface (no per-pixel alpha) cached and blitted each frame.
    # This is significantly cheaper than blending a full-screen SRCALPHA surface.
    overlay = pygame.Surface(screen.get_size()).convert()
    key = (1, 2, 3)
    overlay.fill(key)
    overlay.set_colorkey(key)

    fill_rgb = getattr(opts, "runway_fill_color", (245, 225, 137))
    edge_rgb = getattr(opts, "runway_border_color", (120, 120, 120))

    conv_fact = 1
    if opts.metric:
        conv_fact = 1.852

    # 1 ft -> NM
    ft_to_nm = 0.3048 / 1852.0

    for rw in runways:
        x1, y1 = _polar_to_screen(screen, rw.le_dis_nm, rw.le_ang_deg, dis_range, conv_fact)
        x2, y2 = _polar_to_screen(screen, rw.he_dis_nm, rw.he_ang_deg, dis_range, conv_fact)

        dx = x2 - x1
        dy = y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len < 1:
            continue

        width_nm = max(0.0, float(rw.width_ft) * ft_to_nm)
        half_w_px = (width_nm * 100 / dis_range * conv_fact) / 2.0
        if half_w_px < 1.0:
            half_w_px = 1.0

        # Perpendicular unit vector.
        px = -dy / seg_len
        py = dx / seg_len

        p1 = (int(x1 + px * half_w_px), int(y1 + py * half_w_px))
        p2 = (int(x1 - px * half_w_px), int(y1 - py * half_w_px))
        p3 = (int(x2 - px * half_w_px), int(y2 - py * half_w_px))
        p4 = (int(x2 + px * half_w_px), int(y2 + py * half_w_px))

        pts = [p1, p2, p3, p4]
        pygame.draw.polygon(overlay, fill_rgb, pts)
        pygame.draw.polygon(overlay, edge_rgb, pts, width=1)

    return overlay


def DrawRunwaysOverlay(screen, dis_range, opts, runways_index=None):
    global _RUNWAYS_OVERLAY_CACHE_KEY, _RUNWAYS_OVERLAY_CACHE_SURFACE

    if runways_index is None or not getattr(runways_index, "ready", False):
        _RUNWAYS_OVERLAY_CACHE_KEY = None
        _RUNWAYS_OVERLAY_CACHE_SURFACE = None
        return

    # Match existing visible radius behavior (targets use dis_range * 5).
    visible_nm = dis_range * 5
    runways = runways_index.query_by_max_distance_nm(visible_nm)
    if not runways:
        _RUNWAYS_OVERLAY_CACHE_KEY = None
        _RUNWAYS_OVERLAY_CACHE_SURFACE = None
        return

    cache_key = (
        screen.get_width(),
        screen.get_height(),
        float(dis_range),
        bool(getattr(opts, "metric", False)),
        float(visible_nm),
        int(getattr(runways_index, "version", 0)),
        int(len(runways)),
    )

    if _RUNWAYS_OVERLAY_CACHE_KEY != cache_key or _RUNWAYS_OVERLAY_CACHE_SURFACE is None:
        _RUNWAYS_OVERLAY_CACHE_SURFACE = _build_runways_overlay_surface(screen, dis_range, opts, runways)
        _RUNWAYS_OVERLAY_CACHE_KEY = cache_key

    screen.blit(_RUNWAYS_OVERLAY_CACHE_SURFACE, (0, 0))

opt = [False,False,False]

fonts = []

def Draw(mode,screen,raw_tgts,rdr_tgts,dis_range,sweep_angle,fonts_in,opts,selected_target=None,selected_trail=None,runways_index=None):
    global opt
    global fonts

    fonts = fonts_in
    opt = opts
    
    #col_back = [37,37,37]
    col_back = getattr(opts, "background_color", [21,20,46])
    screen.fill(col_back)
    #Draw Grid Lines
    grid_space = 100
    if opts.grid:
        grid = _get_grid_overlay_surface(screen, grid_space, [50, 50, 50])
        screen.blit(grid, (0, 0))

    conv_fact = 1
    
    if opts.metric:
        conv_fact = 1.852

    if raw_tgts is not None:
        cx = screen.get_width() / 2
        cy = screen.get_height() / 2
        remaining = []

        min_alt_ft = int(getattr(opts, "min_alt_ft", 0) or 0)

        for tgt in raw_tgts:
            # Filter low altitude targets early to reduce work.
            if min_alt_ft > 0 and getattr(tgt, "alt", -999) not in (None, -999) and tgt.alt < min_alt_ft:
                continue

            in_visible_range = tgt.dis < dis_range * 5
            if not in_visible_range:
                rdr_tgts.pop(getattr(tgt, "hex", None), None)
                continue

            if (not tgt.drawn) and sweep_angle > tgt.ang and sweep_angle <= tgt.ang + 0.9:
                rdr_tgt = Classes.RadarTarget()
                rdr_tgt.pos_x = cx + math.sin(tgt.ang * math.pi / 180) * tgt.dis * 100 / dis_range * conv_fact
                rdr_tgt.pos_y = cy - math.cos(tgt.ang * math.pi / 180) * tgt.dis * 100 / dis_range * conv_fact
                rdr_tgt.trk = tgt.trk
                rdr_tgt.ang = tgt.ang
                rdr_tgt.dis = tgt.dis
                rdr_tgt.spd = tgt.spd
                rdr_tgt.alt = tgt.alt
                rdr_tgt.age = tgt.time
                rdr_tgt.cls = tgt.flt
                rdr_tgt.type = tgt.type
                rdr_tgt.hex = tgt.hex
                rdr_tgt.last_seen_ts = time.time()

                sze = 2
                if tgt.cat == "A1":
                    sze = 2
                elif tgt.cat == "A2":
                    sze = 3
                if tgt.cat == "A3":
                    sze = 4
                if tgt.cat == "A4":
                    sze = 4
                if tgt.cat == "A5":
                    sze = 5
                rdr_tgt.sze = sze

                rdr_tgts[rdr_tgt.hex] = rdr_tgt
            else:
                remaining.append(tgt)

        raw_tgts[:] = remaining

    # Draw runways underneath the sweep/aircraft (cached for performance).
    DrawRunwaysOverlay(screen, dis_range, opts, runways_index)

    if mode == 0:
        AnalogDraw1(screen,rdr_tgts,dis_range,sweep_angle)
    elif mode == 1:
        AnalogDraw2(screen,rdr_tgts,dis_range,sweep_angle)
    elif mode == 2:
        AnalogDraw3(screen,rdr_tgts,dis_range,sweep_angle)
    elif mode == 3:
        DigitalDraw(screen,rdr_tgts,dis_range,sweep_angle)

    if selected_target is not None:
        if selected_trail:
            DrawTrail(screen,dis_range,selected_trail,opts)
        DrawInfoBox(screen,fonts,selected_target,opts)


def AnalogDraw1(screen,rdr_tgts,dis_range,sweep_angle):
    global fonts
    col_mark = [205,205,205]
    
    #Draw Scan Bar
    for i in range (0,20):
        j = 20 - i
        line_x = screen.get_width() / 2 + math.sin((sweep_angle - j / 5) * math.pi / 180) * 540
        line_y = screen.get_height() / 2 - math.cos((sweep_angle - j / 5) * math.pi / 180) * 540
        col_scan = [39 + 11 * i / 20, 39 + 211 * i / 20, 39 + 11 * i / 20]
        pygame.draw.line(screen,color=col_scan,start_pos=[screen.get_width() / 2, screen.get_height() / 2],end_pos=[line_x, line_y], width=3)

    #Draw Radar Range Lines
    for j in range (1,6):
        for i in range (0,90):
            ang = (- sweep_angle - i - 180) * math.pi / 180

            center = [screen.get_width() / 2,screen.get_height() / 2]
            rect = pygame.Rect(0, 1, 2, 3)
            rect.center = center[0] - 100 * j, center[1] - 100 * j
            rect.width = 200 * j
            rect.height = 200 * j
            col_scan = [39 + 11 * i / 90, 39 + 211 * i / 90, 39 + 11 * i / 90]
            pygame.draw.arc(screen, col_scan, rect,ang, ang + 1 * math.pi / 180, 1)
    
    #Handle Radar Targets
    now_ts = time.time()
    for rdr_tgt in rdr_tgts.values():
        if getattr(opt, "min_alt_ft", 0) and getattr(rdr_tgt, "alt", -999) not in (None, -999) and rdr_tgt.alt < opt.min_alt_ft:
            continue
        if _target_is_fresh(rdr_tgt, now_ts):
            col = [round(20 * rdr_tgt.fade / 1000,0) + 37, round(190 * rdr_tgt.fade / 1000,0) + 37, round(20 * rdr_tgt.fade / 1000,0) + 37]
            sta_pos_x = rdr_tgt.pos_x + math.cos(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            sta_pos_y = rdr_tgt.pos_y + math.sin(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            end_pos_x = rdr_tgt.pos_x - math.cos(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            end_pos_y = rdr_tgt.pos_y - math.sin(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            pygame.draw.line(screen,color=col,start_pos=[sta_pos_x, sta_pos_y],end_pos=[end_pos_x, end_pos_y], width=rdr_tgt.sze)

        rdr_tgt.fade = rdr_tgt.fade * 0.98

    to_delete = [tgt.hex for tgt in rdr_tgts.values() if tgt.fade < 10]
    for id in to_delete:
        del rdr_tgts[id]
    
    #Draw Center Circle
    pygame.draw.circle(screen,color=col_mark,center=[screen.get_width() / 2, screen.get_height() / 2], radius=3)


def AnalogDraw2(screen,rdr_tgts,dis_range,sweep_angle):
    global fonts
    col_mark = [205,205,205]
       
    #Handle Radar Targets
    now_ts = time.time()
    for rdr_tgt in rdr_tgts.values():
        if getattr(opt, "min_alt_ft", 0) and getattr(rdr_tgt, "alt", -999) not in (None, -999) and rdr_tgt.alt < opt.min_alt_ft:
            continue
        if _target_is_fresh(rdr_tgt, now_ts):
            col = [round(20 * rdr_tgt.fade / 1000,0) + 37, round(190 * rdr_tgt.fade / 1000,0) + 37, round(20 * rdr_tgt.fade / 1000,0) + 37]
            sta_pos_x = rdr_tgt.pos_x + math.cos(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            sta_pos_y = rdr_tgt.pos_y + math.sin(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            end_pos_x = rdr_tgt.pos_x - math.cos(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            end_pos_y = rdr_tgt.pos_y - math.sin(rdr_tgt.ang * math.pi / 180) * 4 * rdr_tgt.sze / 2
            pygame.draw.line(screen,color=col,start_pos=[sta_pos_x, sta_pos_y],end_pos=[end_pos_x, end_pos_y], width=rdr_tgt.sze)

        rdr_tgt.fade = rdr_tgt.fade * 0.998

    to_delete = [tgt.hex for tgt in rdr_tgts.values() if tgt.fade < 10]
    for id in to_delete:
        del rdr_tgts[id]

    #Draw Scan Bar
    for i in range (0,20):
        j = 20 - i
        line_x = screen.get_width() / 2 + math.sin((sweep_angle - j / 5) * math.pi / 180) * 500
        line_y = screen.get_height() / 2 - math.cos((sweep_angle - j / 5) * math.pi / 180) * 500
        col_scan = [39 + 11 * i / 20, 39 + 211 * i / 20, 39 + 11 * i / 20]
        pygame.draw.line(screen,color=col_scan,start_pos=[screen.get_width() / 2, screen.get_height() / 2],end_pos=[line_x, line_y], width=3)

    DrawMarkings(screen,fonts,col_mark,dis_range)

    #Draw Center Circle
    pygame.draw.circle(screen,color=col_mark,center=[screen.get_width() / 2, screen.get_height() / 2], radius=3)

def AnalogDraw3(screen,rdr_tgts,dis_range,sweep_angle):
    global fonts
    col_mark = [205,205,205]
    
    #Handle Radar Targets
    now_ts = time.time()
    for rdr_tgt in rdr_tgts.values():
        if getattr(opt, "min_alt_ft", 0) and getattr(rdr_tgt, "alt", -999) not in (None, -999) and rdr_tgt.alt < opt.min_alt_ft:
            continue
        if _target_is_fresh(rdr_tgt, now_ts):
            col = [round(20 * rdr_tgt.fade / 1000,0) + 37, round(190 * rdr_tgt.fade / 1000,0) + 37, round(20 * rdr_tgt.fade / 1000,0) + 37]
            pygame.draw.circle(screen,color=col,center=[rdr_tgt.pos_x, rdr_tgt.pos_y], radius=7)
        
        rdr_tgt.fade = rdr_tgt.fade * 0.998

    to_delete = [tgt.hex for tgt in rdr_tgts.values() if tgt.fade < 10]
    for id in to_delete:
        del rdr_tgts[id]

    #Draw Scan Bar
    for i in range (0,20):
        j = 20 - i
        line_x = screen.get_width() / 2 + math.sin((sweep_angle - j / 5) * math.pi / 180) * 540
        line_y = screen.get_height() / 2 - math.cos((sweep_angle - j / 5) * math.pi / 180) * 540
        col_scan = [39 + 11 * i / 20, 39 + 211 * i / 20, 39 + 11 * i / 20]
        pygame.draw.line(screen,color=col_scan,start_pos=[screen.get_width() / 2, screen.get_height() / 2],end_pos=[line_x, line_y], width=3)

    DrawMarkings(screen,fonts,col_mark,dis_range)
    
    #Draw Center Circle
    pygame.draw.circle(screen,color=col_mark,center=[screen.get_width() / 2, screen.get_height() / 2], radius=3)


def DigitalDraw(screen,rdr_tgts,dis_range,sweep_angle):
    global fonts
    col_mark = getattr(opt, "markings_color", [205,205,205])
    
    DrawMarkings(screen,fonts,col_mark,dis_range)
    
    #Handle Radar Targets
    now_ts = time.time()
    for rdr_tgt in rdr_tgts.values():  
        if getattr(opt, "min_alt_ft", 0) and getattr(rdr_tgt, "alt", -999) not in (None, -999) and rdr_tgt.alt < opt.min_alt_ft:
            continue
        #Draw new targets behind sweep bar      
        if _target_is_fresh(rdr_tgt, now_ts):
            col = getattr(opt, "plane_color", (97,118,237))
            pygame.draw.circle(screen,color=col,center=[rdr_tgt.pos_x, rdr_tgt.pos_y], radius=3)
            
            if rdr_tgt.spd > 0:
                line_x = rdr_tgt.pos_x + math.sin(rdr_tgt.trk * math.pi / 180) *  rdr_tgt.spd * 100 / dis_range / 60 / 3
                line_y = rdr_tgt.pos_y - math.cos(rdr_tgt.trk * math.pi / 180) *  rdr_tgt.spd * 100 / dis_range / 60 / 3
                pygame.draw.line(screen,col,[rdr_tgt.pos_x, rdr_tgt.pos_y],[line_x, line_y], True)
                img = _render_text_cached(fonts[0], rdr_tgt.cls, True, getattr(opt, "plane_text_color", (97,118,237)))
                label_offset_y = -20
                if rdr_tgt.trk >= 270 or rdr_tgt.trk <= 90:
                    label_offset_y = 10
                screen.blit(img, (rdr_tgt.pos_x - 20, rdr_tgt.pos_y + label_offset_y))

    # Keep contacts on screen between sweeps; only purge clearly stale contacts.
    to_delete = [tgt.hex for tgt in rdr_tgts.values() if not _target_is_fresh(tgt, now_ts)]
    for id in to_delete:
        del rdr_tgts[id]

    #Draw Scan Bar
    line_x = screen.get_width() / 2 + math.sin(sweep_angle * math.pi / 180) * 500
    line_y = screen.get_height() / 2 - math.cos(sweep_angle * math.pi / 180) * 500
    pygame.draw.line(screen,color=getattr(opt, "scanbar_color", [97,237,174]),start_pos=[screen.get_width() / 2, screen.get_height() / 2],end_pos=[line_x, line_y], width=2)


def DrawMarkings(screen,fonts,col_mark,dis_range):
    global opt
    global _MARKINGS_CACHE_KEY, _MARKINGS_CACHE_SURFACE

    w = screen.get_width()
    h = screen.get_height()
    cache_key = (w, h, int(dis_range), bool(opt.metric), tuple(col_mark), id(fonts[1]))

    if _MARKINGS_CACHE_KEY != cache_key or _MARKINGS_CACHE_SURFACE is None:
        s = pygame.Surface((w, h)).convert()
        key = (1, 2, 3)
        s.fill(key)
        s.set_colorkey(key)

        cx = w / 2
        cy = h / 2

        # Draw range circles + labels
        range_unit = "KM" if opt.metric else "NM"
        for i in range(1, 6):
            gfxdraw.aacircle(s, int(cx), int(cy), 100 * i, col_mark)
            img = _render_text_cached(fonts[1], str(i * dis_range) + range_unit, True, col_mark)
            s.blit(img, (cx - 20, cy + 100 * i + 10))

        # Draw indexes
        for i in range(0, 16):
            angle = i * 22.5
            tick_len = 0

            if angle == 0 or angle == 90 or angle == 270:
                tick_len = 20
            elif angle == 45 or angle == 135 or angle == 225 or angle == 315:
                tick_len = 15
            else:
                for j in range(1, 17):
                    if angle == 22.5 * j:
                        tick_len = 5
                        continue

            if tick_len > 0:
                line_pos1_x = cx + math.sin(angle * math.pi / 180) * (cx - 39)
                line_pos1_y = cy - math.cos(angle * math.pi / 180) * (cy - 39)
                line_pos2_x = cx + math.sin(angle * math.pi / 180) * (cx - 39 + tick_len)
                line_pos2_y = cy - math.cos(angle * math.pi / 180) * (cy - 39 + tick_len)
                pygame.draw.line(s, color=col_mark, start_pos=[line_pos1_x, line_pos1_y], end_pos=[line_pos2_x, line_pos2_y], width=2)

        for i in range(0, 4):
            angle = i * 90
            line_pos1_x = cx + math.sin(angle * math.pi / 180) * 5
            line_pos1_y = cy - math.cos(angle * math.pi / 180) * 5
            line_pos2_x = cx + math.sin(angle * math.pi / 180) * 10
            line_pos2_y = cy - math.cos(angle * math.pi / 180) * 10
            pygame.draw.line(s, color=col_mark, start_pos=[line_pos1_x, line_pos1_y], end_pos=[line_pos2_x, line_pos2_y], width=2)

        # Draw 90° text markings
        img = _render_text_cached(fonts[1], "360", True, col_mark)
        img = pygame.transform.rotate(img, 0)
        s.blit(img, (cx - img.get_width() / 2 + 2, 45))

        img = _render_text_cached(fonts[1], "090", True, col_mark)
        img = pygame.transform.rotate(img, 270)
        s.blit(img, (w - 67, cy - img.get_height() / 2 + 2))

        img = _render_text_cached(fonts[1], "180", True, col_mark)
        img = pygame.transform.rotate(img, 180)
        s.blit(img, (cx - img.get_width() / 2 + 2, h - 67))

        img = _render_text_cached(fonts[1], "270", True, col_mark)
        img = pygame.transform.rotate(img, 90)
        s.blit(img, (45, cy - img.get_height() / 2 + 2))

        _MARKINGS_CACHE_SURFACE = s
        _MARKINGS_CACHE_KEY = cache_key

    screen.blit(_MARKINGS_CACHE_SURFACE, (0, 0))


def DrawDebugInfo(screen,fonts,mode,fps,dwnl_stats):
    img = _render_text_cached(fonts[1], "Mode:  " + str(mode), True, [250,250,250])
    screen.blit(img, (200,200))
    img = _render_text_cached(fonts[1], "Rate:   " + str(fps) + "fps", True, [250,250,250])
    screen.blit(img, (200,225))
    img = _render_text_cached(fonts[1], "D/E/%:  " + str(dwnl_stats[0]) + " / " + str(dwnl_stats[1]) + " / " + str(round(dwnl_stats[1] / dwnl_stats[0] * 100,1)) + "%", True, [250,250,250])
    screen.blit(img, (200,250))

def DrawConfigError(screen,fonts):
    img = _render_text_cached(fonts[1], "ERROR IN radar.cfg File", True, [255, 0, 0])
    screen.blit(img, (screen.get_width() / 2 - 100, screen.get_height() / 2))
    img = _render_text_cached(fonts[1], "Please check configuration!", True, [255, 255, 255])
    screen.blit(img, (screen.get_width() / 2 - 115, screen.get_height() / 2 + 25))

def DrawUI(screen,fonts,UIElement):
    if isinstance(UIElement, Classes.Button):
        DrawButton(screen,fonts,UIElement)
    elif isinstance(UIElement, Classes.Text):
        DrawTextDisplay(screen,fonts,UIElement)
    elif isinstance(UIElement, Classes.Rectangle):
        DrawRectangle(screen,UIElement)

def DrawButton(screen,fonts,button):
    #Draw Outer Rectangle
    rect = pygame.Rect([button.pos[0] - 5, button.pos[1] - 5],[button.sze[0] + 10, button.sze[1] + 10])
    pygame.draw.rect(screen,[100,100,100],rect)
    button.rect = rect

    #Draw Inner Rectangle
    rect = pygame.Rect(button.pos,button.sze)
    mousePos = pygame.mouse.get_pos()
    
    if button.high:
        if rect.collidepoint(mousePos):
            col = [0,225,0]
        else:
            col = [0,150,0]
    else:
        if rect.collidepoint(mousePos):
            col = [225,225,225]
        else:
            col = [175,175,175]
    pygame.draw.rect(screen,col,rect)

    img = fonts[1].render(button.txt, True, [0, 0, 0])
    screen.blit(img, (button.pos[0] + button.sze[0] / 2 - img.get_width() / 2,button.pos[1] + button.sze[1] / 2 - img.get_height() / 2))

def DrawTextDisplay(screen,fonts,display):
    img = fonts[display.fnt_sze].render(display.txt, True, [255, 255, 255])
    screen.blit(img, (display.pos[0] + display.sze[0] / 2 - img.get_width() / 2,display.pos[1] + display.sze[1] / 2 - img.get_height() / 2))

def DrawRectangle(screen,rectanle):
    #Draw Inner Rectangle
    s = pygame.Surface((rectanle.sze))
    s.fill(rectanle.col)
    s.set_alpha(rectanle.alpha)
    screen.blit(s,rectanle.pos)

def DrawTrail(screen,dis_range,trail_points,opts):
    # Draw recent path for the selected target.
    if dis_range <= 0 or not trail_points:
        return

    conv_fact = 1
    if opts.metric:
        conv_fact = 1.852

    center_x = screen.get_width() / 2
    center_y = screen.get_height() / 2

    # Trail points are already stored in time order.
    coords = []
    for p in trail_points:
        dis = p.get("dis", 0)
        ang = p.get("ang", 0)
        x = center_x + math.sin(ang * math.pi / 180) * dis * 100 / dis_range * conv_fact
        y = center_y - math.cos(ang * math.pi / 180) * dis * 100 / dis_range * conv_fact
        coords.append((int(x), int(y)))

    # Draw points
    # for c in coords:
        # pygame.draw.circle(screen, [120, 200, 255], (int(c[0]), int(c[1])), 3)

    # Draw faint trail segments
    if len(coords) >= 2:
        pygame.draw.lines(screen, [120, 200, 255], False, coords, 2)

def DrawInfoBox(screen,fonts,selected_target,opts):
    # Draw small overlay with speed and altitude near the selected radar target.
    box_w = 170
    box_h = 88
    offset_x = 20
    offset_y = -80

    box_alpha = 185

    box_x = selected_target.pos_x + offset_x
    box_y = selected_target.pos_y + offset_y

    # Keep the box inside the screen bounds
    box_x = max(10, min(box_x, screen.get_width() - box_w - 10))
    box_y = max(10, min(box_y, screen.get_height() - box_h - 10))

    rect = pygame.Rect(box_x, box_y, box_w, box_h)

    bg = _get_infobox_bg_surface(box_w, box_h, box_alpha)
    screen.blit(bg, (box_x, box_y))
    pygame.draw.rect(screen, [200,200,200], rect, width=1)

    callsign = selected_target.cls if selected_target.cls else "N/A"
    alt_val = selected_target.alt if selected_target.alt is not None else -999
    spd_val = selected_target.spd if selected_target.spd is not None else -999
    type_val = selected_target.type

    alt_str = "ALT: N/A"
    spd_str = "SPD: N/A"
    type_str = "TYPE: N/A"

    if alt_val != -999:
        alt_str = "ALT: " + str(int(alt_val)) + " ft"
    if spd_val != -999:
        spd_str = "SPD: " + str(int(spd_val)) + " kt"
    if type_val != "":
        type_str = "TYPE: " + str(type_val)

    img_callsign = _render_text_cached(fonts[1], callsign, True, [255,255,255])
    img_alt = _render_text_cached(fonts[0], alt_str, True, [200,200,200])
    img_spd = _render_text_cached(fonts[0], spd_str, True, [200,200,200])
    img_type = _render_text_cached(fonts[0], type_str, True, [200,200,200])

    screen.blit(img_callsign, (box_x + 10, box_y + 8))
    screen.blit(img_alt, (box_x + 10, box_y + 32))
    screen.blit(img_spd, (box_x + 10, box_y + 50))
    screen.blit(img_type, (box_x + 10, box_y + 68))
