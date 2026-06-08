"""
CineVault - Netflix-style Media Player
Dark theme with red wavy accent background
Embedded VLC playback, watch tracking, resume, mini-player mode
"""

import os, re, sys, json, time, math, threading, pathlib, subprocess, shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

# ── deps (bundled in exe — imported directly) ────────────────────────────────
try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFilter
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS & PATHS
# ═══════════════════════════════════════════════════════════════════════════════
CONFIG_PATH  = pathlib.Path.home() / ".cinevault_config.json"
DB_PATH      = pathlib.Path.home() / ".cinevault_db.json"
WATCHED_MARK = 0.95   # 95% = finished
VIDEO_EXT    = {'.mp4','.mkv','.avi','.mov','.wmv','.m4v','.ts','.flv'}

# ── colours ───────────────────────────────────────────────────────────────────
BG       = "#0a0a0f"
BG2      = "#0f0f1a"
CARD     = "#141420"
CARD_H   = "#1e1e30"
ACCENT   = "#e50914"
ACCENT2  = "#ff2d3a"
GOLD     = "#f5c518"
MUTED    = "#5a5a7a"
TEXT     = "#f0f0f0"
TEXT2    = "#a0a0c0"
GREEN    = "#46d369"
PROG_BG  = "#2a2a3a"

# ── fonts ─────────────────────────────────────────────────────────────────────
F_TITLE  = ("Georgia", 22, "bold")
F_HEAD   = ("Georgia", 13, "bold")
F_CARD   = ("Segoe UI", 10, "bold")
F_SMALL  = ("Segoe UI", 8)
F_UI     = ("Segoe UI", 9)
F_UI_B   = ("Segoe UI Semibold", 9)
F_MONO   = ("Consolas", 8)

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG & DATABASE
# ═══════════════════════════════════════════════════════════════════════════════
def load_json(path, default):
    try:    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except: return default

def save_json(path, data):
    try:    pathlib.Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except: pass

def load_cfg():  return load_json(CONFIG_PATH, {})
def save_cfg(d): save_json(CONFIG_PATH, d)
def load_db():   return load_json(DB_PATH, {"movies":{},"series":{},"history":[]})
def save_db(d):  save_json(DB_PATH, d)

# ═══════════════════════════════════════════════════════════════════════════════
#  LIBRARY SCANNER
# ═══════════════════════════════════════════════════════════════════════════════
def scan_movies(folder):
    """Return list of {title, path, year} dicts."""
    movies = []
    if not folder or not os.path.isdir(folder): return movies
    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in VIDEO_EXT:
            name, _ = os.path.splitext(fname)
            m = re.search(r'\((\d{4})\)', name)
            year  = m.group(1) if m else ""
            title = re.sub(r'\(\d{4}\)', '', name).strip()
            movies.append({"title": title, "year": year, "path": fpath})
    return movies

def scan_series(folder):
    """Return list of {show, seasons:{1:[{ep,path}]}} dicts."""
    series = []
    if not folder or not os.path.isdir(folder): return series
    for show in sorted(os.listdir(folder)):
        show_path = os.path.join(folder, show)
        if not os.path.isdir(show_path): continue
        seasons = {}
        for item in sorted(os.listdir(show_path)):
            item_path = os.path.join(show_path, item)
            if os.path.isdir(item_path):
                m = re.match(r'[Ss]eason\s*(\d+)', item)
                if m:
                    snum = int(m.group(1))
                    eps  = []
                    for ep in sorted(os.listdir(item_path)):
                        ep_path = os.path.join(item_path, ep)
                        if os.path.isfile(ep_path) and os.path.splitext(ep)[1].lower() in VIDEO_EXT:
                            em = re.match(r'(\d)(\d{2})', os.path.splitext(ep)[0])
                            ep_num = int(em.group(2)) if em else 0
                            eps.append({"ep": ep_num, "file": ep, "path": ep_path})
                    if eps:
                        seasons[snum] = eps
        if seasons:
            series.append({"show": show, "seasons": seasons, "path": show_path})
    return series

def fmt_duration(secs):
    if not secs: return "0:00"
    h = int(secs)//3600; m = (int(secs)%3600)//60; s = int(secs)%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

# ═══════════════════════════════════════════════════════════════════════════════
#  VLC WRAPPER
# ═══════════════════════════════════════════════════════════════════════════════
class VLCPlayer:
    def __init__(self, vlc_path):
        self.vlc_path  = vlc_path
        self.instance  = None
        self.player    = None
        self.available = False
        self._load()

    def _load(self):
        try:
            vlc_dir = os.path.dirname(self.vlc_path)
            if vlc_dir not in sys.path: sys.path.insert(0, vlc_dir)
            os.environ.setdefault("PYTHON_VLC_LIB_PATH", self.vlc_path)
            import ctypes
            if sys.platform == "win32":
                os.add_dll_directory(vlc_dir)
            import vlc as _vlc
            self._vlc      = _vlc
            self.instance  = _vlc.Instance("--no-xlib","--quiet")
            self.player    = self.instance.media_player_new()
            self.available = True
        except Exception as e:
            self.available = False

    def set_window(self, hwnd):
        if not self.available: return
        try:
            if sys.platform == "win32":
                self.player.set_hwnd(hwnd)
            else:
                self.player.set_xwindow(hwnd)
        except: pass

    def play(self, path, start_pos=0):
        if not self.available: return
        try:
            media = self.instance.media_new(path)
            self.player.set_media(media)
            self.player.play()
            if start_pos > 0:
                threading.Timer(1.5, lambda: self.seek_to(start_pos)).start()
        except: pass

    def pause(self):
        if self.available:
            try: self.player.pause()
            except: pass

    def stop(self):
        if self.available:
            try: self.player.stop()
            except: pass

    def seek(self, pct):
        if self.available:
            try: self.player.set_position(max(0.0, min(1.0, pct)))
            except: pass

    def seek_to(self, secs):
        dur = self.get_duration()
        if dur > 0: self.seek(secs / dur)

    def get_position(self):
        if not self.available: return 0.0
        try: return max(0.0, self.player.get_position() or 0.0)
        except: return 0.0

    def get_time(self):
        if not self.available: return 0
        try: return max(0, (self.player.get_time() or 0) // 1000)
        except: return 0

    def get_duration(self):
        if not self.available: return 0
        try: return max(0, (self.player.get_length() or 0) // 1000)
        except: return 0

    def set_volume(self, vol):
        if self.available:
            try: self.player.audio_set_volume(int(vol))
            except: pass

    def is_playing(self):
        if not self.available: return False
        try: return self.player.is_playing() == 1
        except: return False

    def is_ended(self):
        if not self.available: return False
        try:
            state = self.player.get_state()
            return str(state) in ("State.Ended","State.NothingSpecial")
        except: return False

# ═══════════════════════════════════════════════════════════════════════════════
#  WAVY BACKGROUND CANVAS
# ═══════════════════════════════════════════════════════════════════════════════
class WavyBackground(tk.Canvas):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, highlightthickness=0, **kw)
        self._phase   = 0.0
        self._img_ref = None
        self.bind("<Configure>", self._on_resize)
        self._animate()

    def _on_resize(self, e):
        self._draw(e.width, e.height)

    def _draw(self, w, h):
        if w < 10 or h < 10: return
        if not PIL_OK: return
        img  = Image.new("RGBA", (w, h), (10, 10, 15, 255))
        draw = ImageDraw.Draw(img)
        # Draw several semi-transparent red wavy bands
        colors = [
            (229, 9, 20, 18),
            (255, 45, 58, 12),
            (180, 5, 15, 22),
        ]
        for ci, (r,g,b,a) in enumerate(colors):
            pts = []
            amp   = h * (0.04 + ci * 0.025)
            freq  = 0.008 - ci * 0.002
            ybase = h * (0.25 + ci * 0.28)
            steps = max(w // 3, 20)
            for i in range(steps + 1):
                x = int(i * w / steps)
                y = int(ybase + amp * math.sin(freq * x + self._phase + ci * 1.2))
                pts.append((x, y))
            # fill below each wave
            poly = pts + [(w, h), (0, h)]
            draw.polygon(poly, fill=(r, g, b, a))
        # subtle vignette
        for edge in range(0, min(w,h)//4, 8):
            alpha = int(60 * (1 - edge / (min(w,h)//4)))
            draw.rectangle([edge, edge, w-edge, h-edge],
                           outline=(10,10,15,alpha), width=8)
        photo = ImageTk.PhotoImage(img)
        self._img_ref = photo
        self.delete("bg")
        self.create_image(0, 0, image=photo, anchor="nw", tags="bg")
        self.tag_lower("bg")

    def _animate(self):
        self._phase += 0.015
        w = self.winfo_width(); h = self.winfo_height()
        if w > 10 and h > 10: self._draw(w, h)
        self.after(50, self._animate)

# ═══════════════════════════════════════════════════════════════════════════════
#  TITLE CARD WIDGET
# ═══════════════════════════════════════════════════════════════════════════════
class TitleCard(tk.Frame):
    def __init__(self, parent, title, subtitle, progress, status,
                 on_play, on_right_click, **kw):
        super().__init__(parent, bg=CARD, cursor="hand2",
                         relief="flat", bd=0, **kw)
        self.on_play        = on_play
        self.on_right_click = on_right_click
        self._build(title, subtitle, progress, status)
        self.bind("<Button-1>",        lambda e: on_play())
        self.bind("<Button-3>",        on_right_click)
        self.bind("<Enter>",           self._hover_on)
        self.bind("<Leave>",           self._hover_off)
        for child in self.winfo_children():
            child.bind("<Button-1>",   lambda e: on_play())
            child.bind("<Button-3>",   on_right_click)
            child.bind("<Enter>",      self._hover_on)
            child.bind("<Leave>",      self._hover_off)

    def _build(self, title, subtitle, progress, status):
        # Colour band at top
        band = tk.Frame(self, bg=ACCENT, height=3)
        band.pack(fill="x")

        body = tk.Frame(self, bg=CARD, padx=10, pady=8)
        body.pack(fill="both", expand=True)

        # Status badge
        if status == "watched":
            badge_bg, badge_fg, badge_txt = "#1a3a1a", GREEN, "✓ Watched"
        elif status == "watching":
            badge_bg, badge_fg, badge_txt = "#3a1a1a", ACCENT2, "▶ Watching"
        else:
            badge_bg, badge_fg, badge_txt = PROG_BG, MUTED, "● Unwatched"

        tk.Label(body, text=badge_txt, font=("Segoe UI", 7, "bold"),
                 bg=badge_bg, fg=badge_fg, padx=4, pady=1).pack(anchor="ne")

        tk.Label(body, text=title, font=F_CARD, bg=CARD, fg=TEXT,
                 wraplength=160, justify="left").pack(anchor="w", pady=(4,0))

        if subtitle:
            tk.Label(body, text=subtitle, font=F_SMALL, bg=CARD,
                     fg=TEXT2).pack(anchor="w")

        # Progress bar
        pb_frame = tk.Frame(body, bg=PROG_BG, height=3)
        pb_frame.pack(fill="x", pady=(6,0))
        pb_frame.pack_propagate(False)
        if progress > 0:
            fill_w = int(progress * 160)
            col    = GREEN if progress >= WATCHED_MARK else ACCENT
            tk.Frame(pb_frame, bg=col, height=3,
                     width=fill_w).place(x=0, y=0, relheight=1)

        if progress > 0:
            tk.Label(body, text=f"{int(progress*100)}%", font=F_MONO,
                     bg=CARD, fg=MUTED).pack(anchor="e")

    def _hover_on(self, e=None):
        self.configure(bg=CARD_H)
        for c in self.winfo_children(): _set_bg_recursive(c, CARD_H)

    def _hover_off(self, e=None):
        self.configure(bg=CARD)
        for c in self.winfo_children(): _set_bg_recursive(c, CARD)

def _set_bg_recursive(w, color):
    try: w.configure(bg=color)
    except: pass
    for c in w.winfo_children():
        _set_bg_recursive(c, color)

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════
class CineVault:
    def __init__(self, root):
        self.root        = root
        self.cfg         = load_cfg()
        self.db          = load_db()
        self.vlc         = None
        self.current     = None   # {path, title, type, start_time, duration}
        self.mini_mode   = False
        self._poll_id    = None
        self._seek_drag  = False

        root.title("CineVault")
        geo = self.cfg.get("geometry","1280x780")
        root.geometry(geo)
        root.configure(bg=BG)
        root.minsize(900, 600)
        root.bind("<Configure>", self._on_resize)
        root.bind("<KeyPress>",  self._on_key)

        self._build_ui()
        self._init_vlc()
        self.root.after(500, self._refresh_library)

    # ── VLC init ──────────────────────────────────────────────────────────────
    def _init_vlc(self):
        path = self.cfg.get("vlc_path","")
        if path and os.path.isfile(path):
            self.vlc = VLCPlayer(path)
            if not self.vlc.available:
                self.vlc = None

    # ══════════════════════════════════════════════════════════════════════════
    #  UI BUILD
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # ── Sidebar ──
        self.sidebar = tk.Frame(self.root, bg="#08080f", width=180)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # ── Main area ──
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        # ── Pages ──
        self.pages = {}
        for name in ("home","movies","series","history","settings"):
            f = tk.Frame(self.main, bg=BG)
            self.pages[name] = f

        self._build_home()
        self._build_movies_page()
        self._build_series_page()
        self._build_history_page()
        self._build_settings_page()

        # ── Player area (hidden until playing) ──
        self.player_area = tk.Frame(self.root, bg="#000", width=0)
        # built lazily

        self._current_page = None
        self._show_page("home")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = self.sidebar
        # Logo
        logo = tk.Frame(sb, bg="#08080f", pady=18)
        logo.pack(fill="x")
        tk.Label(logo, text="CINEVAULT", font=("Georgia",20,"bold"),
                 bg="#08080f", fg=ACCENT).pack()
        tk.Label(logo, text="your media, your way",
                 font=("Segoe UI",8), bg="#08080f", fg=MUTED).pack()
        tk.Frame(sb, bg=ACCENT, height=1).pack(fill="x", padx=20)

        nav_items = [
            ("🏠  Home",      "home"),
            ("🎬  Movies",    "movies"),
            ("📺  Series",    "series"),
            ("📋  History",   "history"),
            ("⚙   Settings",  "settings"),
        ]
        self.nav_btns = {}
        for label, page in nav_items:
            btn = tk.Button(sb, text=label, font=F_UI_B,
                            bg="#08080f", fg=TEXT2, relief="flat",
                            anchor="w", padx=20, pady=10,
                            cursor="hand2",
                            activebackground=CARD, activeforeground=TEXT,
                            command=lambda p=page: self._show_page(p))
            btn.pack(fill="x")
            self.nav_btns[page] = btn

        # Now playing indicator at bottom of sidebar
        tk.Frame(sb, bg=ACCENT, height=1).pack(fill="x", padx=20, side="bottom", pady=(0,4))
        self.now_playing_lbl = tk.Label(sb, text="", font=("Segoe UI",7),
                                         bg="#08080f", fg=MUTED,
                                         wraplength=160, justify="left")
        self.now_playing_lbl.pack(side="bottom", fill="x", padx=10, pady=4)

    # ── Pages ─────────────────────────────────────────────────────────────────
    def _build_home(self):
        p = self.pages["home"]
        # WavyBackground disabled — caused click-blocking on Windows

        scroll = _ScrollFrame(p)
        scroll.pack(fill="both", expand=True)
        inner = scroll.inner

        tk.Label(inner, text="Continue Watching", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=12).pack(anchor="w", padx=20)
        self.continue_frame = tk.Frame(inner, bg=BG)
        self.continue_frame.pack(fill="x", padx=20, pady=(0,16))

        tk.Label(inner, text="Recently Added", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=8).pack(anchor="w", padx=20)
        self.recent_frame = tk.Frame(inner, bg=BG)
        self.recent_frame.pack(fill="x", padx=20)

    def _build_movies_page(self):
        p = self.pages["movies"]
        # WavyBackground disabled — caused click-blocking on Windows
        self._build_library_page(p, "movies")

    def _build_series_page(self):
        p = self.pages["series"]
        # WavyBackground disabled — caused click-blocking on Windows
        self._build_library_page(p, "series")

    def _build_library_page(self, parent, kind):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="both", expand=True)

        # toolbar
        bar = tk.Frame(top, bg=BG2, pady=8)
        bar.pack(fill="x", padx=0)

        tk.Label(bar, text=("🎬 Movies" if kind=="movies" else "📺 Series"),
                 font=F_HEAD, bg=BG2, fg=TEXT).pack(side="left", padx=16)

        # search
        sv = tk.StringVar()
        setattr(self, f"{kind}_search", sv)
        se = tk.Entry(bar, textvariable=sv, bg=CARD, fg=TEXT, relief="flat",
                      font=F_UI, insertbackground=TEXT, width=22)
        se.pack(side="left", padx=8, ipady=4)
        tk.Label(bar, text="🔍", bg=BG2, fg=MUTED).pack(side="left")
        sv.trace_add("write", lambda *_: self._filter_library(kind))

        # sort
        sort_var = tk.StringVar(value="Name")
        setattr(self, f"{kind}_sort", sort_var)
        sort_cb = ttk.Combobox(bar, textvariable=sort_var, width=16,
                               values=["Name","Recently Added","Last Watched"],
                               state="readonly", font=F_UI)
        sort_cb.pack(side="left", padx=8)
        sort_cb.bind("<<ComboboxSelected>>", lambda *_: self._filter_library(kind))

        # filter
        filt_var = tk.StringVar(value="All")
        setattr(self, f"{kind}_filter", filt_var)
        filt_cb = ttk.Combobox(bar, textvariable=filt_var, width=12,
                               values=["All","Unwatched","Watching","Watched"],
                               state="readonly", font=F_UI)
        filt_cb.pack(side="left", padx=4)
        filt_cb.bind("<<ComboboxSelected>>", lambda *_: self._filter_library(kind))

        tk.Button(bar, text="⟳ Refresh", command=self._refresh_library,
                  bg=CARD, fg=TEXT2, relief="flat", font=F_UI,
                  cursor="hand2", padx=8).pack(side="right", padx=12)

        # watched section label
        tk.Label(top, text="▸  Library", font=F_UI_B,
                 bg=BG, fg=TEXT2).pack(anchor="w", padx=16, pady=(10,2))

        scroll = _ScrollFrame(top)
        scroll.pack(fill="both", expand=True)
        setattr(self, f"{kind}_scroll", scroll)
        setattr(self, f"{kind}_inner",  scroll.inner)

        # watched section
        tk.Label(top, text="✓  Watched", font=F_UI_B,
                 bg=BG, fg=GREEN).pack(anchor="w", padx=16, pady=(10,2))
        ws = _ScrollFrame(top, height=180)
        ws.pack(fill="x", padx=0)
        setattr(self, f"{kind}_watched_scroll", ws)
        setattr(self, f"{kind}_watched_inner",  ws.inner)

    def _build_history_page(self):
        p = self.pages["history"]
        # WavyBackground disabled — caused click-blocking on Windows
        overlay = tk.Frame(p, bg=BG)
        overlay.pack(fill="both", expand=True)

        tk.Label(overlay, text="Watch History", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=16).pack(anchor="w", padx=20)
        tk.Button(overlay, text="Clear History",
                  command=self._clear_history,
                  bg=CARD, fg=ACCENT, relief="flat", font=F_UI,
                  cursor="hand2", padx=10).pack(anchor="e", padx=20)

        cols = ("Title","Type","Date","Duration","Status")
        style = ttk.Style()
        style.configure("Hist.Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=24, font=F_UI)
        style.configure("Hist.Treeview.Heading", background=CARD_H,
                        foreground=TEXT2, font=F_UI_B)
        self.hist_tree = ttk.Treeview(overlay, columns=cols, show="headings",
                                       style="Hist.Treeview", height=20)
        for c in cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=160 if c=="Title" else 100)
        sb2 = ttk.Scrollbar(overlay, orient="vertical",
                             command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb2.set)
        self.hist_tree.pack(side="left", fill="both", expand=True, padx=(20,0), pady=8)
        sb2.pack(side="left", fill="y", pady=8)

    def _build_settings_page(self):
        p = self.pages["settings"]
        # WavyBackground disabled — caused click-blocking on Windows
        overlay = tk.Frame(p, bg=BG)
        overlay.pack(fill="both", expand=True)

        tk.Label(overlay, text="⚙  Settings", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=16).pack(anchor="w", padx=20)

        rows = [
            ("VLC Path",       "vlc_path",    "Path to libvlc.dll or vlc executable"),
            ("Movies Folder",  "movies_dir",  "Your organised movies output folder"),
            ("Series Folder",  "series_dir",  "Your organised series output folder"),
        ]
        self._setting_vars = {}
        for label, key, hint in rows:
            row = tk.Frame(overlay, bg=BG); row.pack(fill="x", padx=20, pady=6)
            tk.Label(row, text=label, font=F_UI_B, bg=BG, fg=TEXT,
                     width=16, anchor="w").pack(side="left")
            var = tk.StringVar(value=self.cfg.get(key,""))
            self._setting_vars[key] = var
            tk.Entry(row, textvariable=var, bg=CARD, fg=TEXT, relief="flat",
                     font=F_UI, insertbackground=TEXT,
                     width=46).pack(side="left", ipady=4, padx=(0,8))
            tk.Button(row, text="Browse",
                      command=lambda k=key, v=var: self._browse_setting(k,v),
                      bg=ACCENT, fg="white", relief="flat", font=F_UI,
                      cursor="hand2", padx=8).pack(side="left")
            tk.Label(row, text=hint, font=F_SMALL, bg=BG,
                     fg=MUTED).pack(anchor="w", padx=(140,0))

        tk.Button(overlay, text="💾  Save Settings", command=self._save_settings,
                  bg=ACCENT, fg="white", relief="flat",
                  font=("Segoe UI Semibold",10), cursor="hand2",
                  padx=16, pady=8).pack(padx=20, pady=16, anchor="w")

    # ══════════════════════════════════════════════════════════════════════════
    #  PLAYER PANEL  (built once, shown/hidden)
    # ══════════════════════════════════════════════════════════════════════════
    def _ensure_player_panel(self):
        if hasattr(self, "_player_built"): return
        self._player_built = True

        self.player_panel = tk.Frame(self.root, bg="#000")
        self.player_panel.pack(side="right", fill="both", expand=True)
        self.player_panel.pack_forget()

        # video canvas
        self.video_canvas = tk.Canvas(self.player_panel, bg="#000",
                                       highlightthickness=0, cursor="hand2")
        self.video_canvas.pack(fill="both", expand=True)
        self.video_canvas.bind("<Double-Button-1>", self._toggle_mini)
        self.video_canvas.bind("<Button-1>",        lambda e: self._toggle_pause())

        # controls bar
        ctrl = tk.Frame(self.player_panel, bg="#0d0d0d", pady=6)
        ctrl.pack(fill="x", side="bottom")

        # seek bar
        self.seek_var = tk.DoubleVar()
        self.seek_bar = ttk.Scale(ctrl, from_=0, to=1, variable=self.seek_var,
                                   orient="horizontal", command=self._on_seek_move)
        self.seek_bar.pack(fill="x", padx=10, pady=(0,4))
        self.seek_bar.bind("<ButtonPress-1>",   lambda e: setattr(self,"_seek_drag",True))
        self.seek_bar.bind("<ButtonRelease-1>", self._on_seek_release)

        # buttons row
        btn_row = tk.Frame(ctrl, bg="#0d0d0d")
        btn_row.pack(fill="x", padx=10)

        self.play_btn = tk.Button(btn_row, text="⏸", font=("Segoe UI",14),
                                   bg="#0d0d0d", fg=TEXT, relief="flat",
                                   cursor="hand2", command=self._toggle_pause)
        self.play_btn.pack(side="left")

        tk.Button(btn_row, text="⏹", font=("Segoe UI",12),
                  bg="#0d0d0d", fg=TEXT2, relief="flat",
                  cursor="hand2", command=self._stop_playback).pack(side="left",padx=4)

        self.time_lbl = tk.Label(btn_row, text="0:00 / 0:00",
                                  font=F_MONO, bg="#0d0d0d", fg=TEXT2)
        self.time_lbl.pack(side="left", padx=10)

        # volume
        tk.Label(btn_row, text="🔊", bg="#0d0d0d", fg=TEXT2).pack(side="right")
        self.vol_var = tk.IntVar(value=80)
        vol_sl = ttk.Scale(btn_row, from_=0, to=100, variable=self.vol_var,
                           orient="horizontal", length=80,
                           command=lambda v: self.vlc and self.vlc.set_volume(int(float(v))))
        vol_sl.pack(side="right", padx=4)

        self.now_lbl = tk.Label(btn_row, text="", font=F_UI,
                                 bg="#0d0d0d", fg=MUTED)
        self.now_lbl.pack(side="right", padx=10)

        # Mini player side list
        self.mini_list_frame = tk.Frame(self.player_panel, bg=BG2, width=260)
        self.mini_list_frame.pack_forget()

    # ══════════════════════════════════════════════════════════════════════════
    #  PLAYBACK
    # ══════════════════════════════════════════════════════════════════════════
    def _play(self, path, title, media_type, resume=True):
        if not self.vlc or not self.vlc.available:
            messagebox.showwarning("VLC not configured",
                "Please set the VLC path in Settings first.")
            return

        self._ensure_player_panel()
        self.player_panel.pack(side="right", fill="both", expand=True)

        # set window handle
        self.root.update()
        hwnd = self.video_canvas.winfo_id()
        self.vlc.set_window(hwnd)

        # resume position
        start = 0
        key   = self._db_key(path)
        if resume:
            prog = self.db.get("movies" if media_type=="movie" else "series",{})
            info = prog.get(key, {})
            pos  = info.get("position", 0)
            dur  = info.get("duration", 0)
            if dur > 0 and pos / dur < WATCHED_MARK:
                start = pos

        self.vlc.play(path, start_pos=start)
        if self.vlc.available:
            self.vlc.set_volume(self.vol_var.get())

        self.current = {
            "path":       path,
            "title":      title,
            "type":       media_type,
            "start_time": time.time(),
            "key":        key,
        }

        self.now_lbl.configure(text=f"▶  {title[:40]}")
        self.now_playing_lbl.configure(text=f"▶ {title[:24]}")
        self.play_btn.configure(text="⏸")

        # add history entry
        self.db["history"].insert(0, {
            "title":  title,
            "type":   media_type,
            "date":   time.strftime("%Y-%m-%d %H:%M"),
            "path":   path,
            "status": "watching",
        })
        save_db(self.db)
        self._poll_playback()
        self._build_mini_list(path, media_type)

    def _poll_playback(self):
        if not self.current or not self.vlc: return
        pos  = self.vlc.get_position()
        cur  = self.vlc.get_time()
        dur  = self.vlc.get_duration()

        # update seek bar
        if not self._seek_drag and dur > 0:
            self.seek_var.set(pos)
        if dur > 0:
            self.time_lbl.configure(
                text=f"{fmt_duration(cur)} / {fmt_duration(dur)}")

        # save progress
        key = self.current["key"]
        section = "movies" if self.current["type"]=="movie" else "series"
        if section not in self.db: self.db[section] = {}
        self.db[section][key] = {
            "position": cur, "duration": dur, "progress": pos,
            "title":    self.current["title"],
            "last_watched": time.strftime("%Y-%m-%d %H:%M"),
        }

        # mark watched at 95%
        if pos >= WATCHED_MARK:
            self.db[section][key]["watched"] = True
            self.db[section][key]["progress"] = 1.0
            # disable resume
            self.db[section][key]["position"] = 0
            self._check_season_complete(key)
            save_db(self.db)
            self._refresh_library()

        save_db(self.db)

        if self.vlc.is_ended():
            self._on_playback_ended()
            return

        self._poll_id = self.root.after(1000, self._poll_playback)

    def _on_playback_ended(self):
        if not self.current: return
        key     = self.current["key"]
        section = "movies" if self.current["type"]=="movie" else "series"
        if section in self.db and key in self.db[section]:
            self.db[section][key]["watched"]  = True
            self.db[section][key]["progress"] = 1.0
            self.db[section][key]["position"] = 0
        for h in self.db["history"]:
            if h.get("path") == self.current["path"]:
                h["status"] = "watched"; break
        save_db(self.db)
        self._check_season_complete(key)
        self._refresh_library()
        self.current = None

    def _toggle_pause(self):
        if not self.vlc: return
        self.vlc.pause()
        self.play_btn.configure(
            text="▶" if not self.vlc.is_playing() else "⏸")

    def _stop_playback(self):
        if self.vlc: self.vlc.stop()
        if self._poll_id:
            self.root.after_cancel(self._poll_id); self._poll_id = None
        self.current = None
        self.now_lbl.configure(text="")
        self.now_playing_lbl.configure(text="")
        if hasattr(self,"player_panel"):
            self.player_panel.pack_forget()

    def _on_seek_move(self, val):
        pass  # only seek on release

    def _on_seek_release(self, e):
        self._seek_drag = False
        if self.vlc:
            self.vlc.seek(self.seek_var.get())

    # ── Mini player ───────────────────────────────────────────────────────────
    def _toggle_mini(self, e=None):
        if not hasattr(self,"player_panel"): return
        if self.mini_mode:
            self._exit_mini()
        else:
            self._enter_mini()

    def _enter_mini(self):
        self.mini_mode = True
        self.video_canvas.configure(width=380, height=214)
        self.player_panel.pack_configure(expand=False, fill="none")
        self.mini_list_frame.pack(side="right", fill="both", expand=True)

    def _exit_mini(self):
        self.mini_mode = False
        self.mini_list_frame.pack_forget()
        self.video_canvas.configure(width=0, height=0)
        self.player_panel.pack_configure(expand=True, fill="both")

    def _build_mini_list(self, current_path, media_type):
        if not hasattr(self,"mini_list_frame"): return
        for w in self.mini_list_frame.winfo_children(): w.destroy()
        tk.Label(self.mini_list_frame,
                 text=("Up Next" if media_type=="series" else "More Movies"),
                 font=F_UI_B, bg=BG2, fg=TEXT, pady=8).pack(fill="x", padx=10)

        scroll = _ScrollFrame(self.mini_list_frame)
        scroll.pack(fill="both", expand=True)
        inner = scroll.inner

        if media_type == "movie":
            items = scan_movies(self.cfg.get("movies_dir",""))
            for m in items:
                if m["path"] == current_path: continue
                lbl = f"{m['title']} {('('+m['year']+')') if m['year'] else ''}"
                self._mini_item(inner, lbl, m["path"], "movie")
        else:
            # find next episode
            series = scan_series(self.cfg.get("series_dir",""))
            for show in series:
                for snum in sorted(show["seasons"]):
                    for ep in show["seasons"][snum]:
                        if ep["path"] == current_path: continue
                        lbl = f"{show['show']} S{snum:02d}E{ep['ep']:02d}"
                        self._mini_item(inner, lbl, ep["path"], "series")

    def _mini_item(self, parent, label, path, media_type):
        f = tk.Frame(parent, bg=BG2, cursor="hand2")
        f.pack(fill="x", padx=8, pady=2)
        tk.Label(f, text=label, font=F_UI, bg=BG2, fg=TEXT2,
                 wraplength=220, justify="left").pack(side="left", padx=8, pady=6)

        def _queue():
            self._queued = (path, label, media_type)
            for w in f.winfo_children(): w.configure(fg=GOLD)
        def _play_now():
            self._play(path, label, media_type)

        f.bind("<Button-1>",        lambda e: _queue())
        f.bind("<Double-Button-1>", lambda e: _play_now())
        for c in f.winfo_children():
            c.bind("<Button-1>",        lambda e: _queue())
            c.bind("<Double-Button-1>", lambda e: _play_now())
        f.bind("<Enter>", lambda e: f.configure(bg=CARD_H))
        f.bind("<Leave>", lambda e: f.configure(bg=BG2))

    # ══════════════════════════════════════════════════════════════════════════
    #  LIBRARY
    # ══════════════════════════════════════════════════════════════════════════
    def _refresh_library(self):
        self._populate_movies()
        self._populate_series()
        self._populate_home()
        self._populate_history()

    def _db_key(self, path):
        return os.path.basename(path)

    def _get_status(self, path):
        key = self._db_key(path)
        for section in ("movies","series"):
            info = self.db.get(section,{}).get(key,{})
            if info.get("watched"): return "watched"
            if info.get("progress",0) > 0.01: return "watching"
        return "unwatched"

    def _get_progress(self, path):
        key = self._db_key(path)
        for section in ("movies","series"):
            info = self.db.get(section,{}).get(key,{})
            if info.get("progress",0) > 0: return info["progress"]
        return 0.0

    def _populate_movies(self):
        inner   = self.movies_inner
        watched = self.movies_watched_inner
        for w in inner.winfo_children():   w.destroy()
        for w in watched.winfo_children(): w.destroy()

        search  = self.movies_search.get().lower()
        sort    = self.movies_sort.get()
        filt    = self.movies_filter.get()
        movies  = scan_movies(self.cfg.get("movies_dir",""))

        # sort
        if sort == "Name":           movies.sort(key=lambda m: m["title"])
        elif sort == "Recently Added": movies = list(reversed(movies))
        elif sort == "Last Watched":
            movies.sort(key=lambda m: self.db.get("movies",{}).get(
                self._db_key(m["path"]),{}).get("last_watched",""), reverse=True)

        row_w = None; row_i = 0
        row_w2 = None; row_i2 = 0
        COLS = 4

        for m in movies:
            if search and search not in m["title"].lower(): continue
            status   = self._get_status(m["path"])
            progress = self._get_progress(m["path"])
            if filt != "All" and status != filt.lower(): continue

            sub = m["year"] if m["year"] else ""
            card = TitleCard(
                inner if status != "watched" else watched,
                m["title"], sub, progress, status,
                on_play=lambda p=m["path"],t=m["title"]: self._play(p,t,"movie"),
                on_right_click=lambda e,p=m["path"],t=m["title"]: self._right_click(e,p,t,"movie"),
                width=180, height=130)

            target = inner if status != "watched" else watched
            ri     = row_i if status != "watched" else row_i2

            if ri % COLS == 0:
                new_row = tk.Frame(target, bg=BG)
                new_row.pack(anchor="w", pady=4)
                if status != "watched": row_w = new_row
                else:                   row_w2 = new_row

            dest_row = row_w if status != "watched" else row_w2
            card_copy = TitleCard(
                dest_row, m["title"], sub, progress, status,
                on_play=lambda p=m["path"],t=m["title"]: self._play(p,t,"movie"),
                on_right_click=lambda e,p=m["path"],t=m["title"]: self._right_click(e,p,t,"movie"),
                width=180, height=130)
            card_copy.pack(side="left", padx=6)

            if status != "watched": row_i += 1
            else:                   row_i2 += 1

    def _populate_series(self):
        inner   = self.series_inner
        watched = self.series_watched_inner
        for w in inner.winfo_children():   w.destroy()
        for w in watched.winfo_children(): w.destroy()

        search = self.series_search.get().lower()
        sort   = self.series_sort.get()
        filt   = self.series_filter.get()
        series = scan_series(self.cfg.get("series_dir",""))

        if sort == "Name": series.sort(key=lambda s: s["show"])

        for show in series:
            if search and search not in show["show"].lower(): continue

            # season selector
            all_watched = True
            for snum in sorted(show["seasons"]):
                eps        = show["seasons"][snum]
                s_watched  = all(self._get_status(ep["path"])=="watched" for ep in eps)
                if not s_watched: all_watched = False

                s_status  = "watched" if s_watched else (
                    "watching" if any(self._get_progress(ep["path"])>0.01 for ep in eps)
                    else "unwatched")
                if filt != "All" and s_status != filt.lower(): continue

                target = watched if s_watched else inner
                sec_lbl = tk.Label(target,
                    text=f"  {show['show']}  —  Season {snum}",
                    font=F_UI_B, bg=BG, fg=TEXT2)
                sec_lbl.pack(anchor="w", padx=16, pady=(10,2))

                row = tk.Frame(target, bg=BG); row.pack(anchor="w", padx=16, pady=4)
                for ep in eps:
                    prog    = self._get_progress(ep["path"])
                    status  = self._get_status(ep["path"])
                    ep_lbl  = f"E{ep['ep']:02d}"
                    card    = TitleCard(
                        row, ep_lbl,
                        f"{show['show']} S{snum}E{ep['ep']:02d}",
                        prog, status,
                        on_play=lambda p=ep["path"],t=f"{show['show']} S{snum:02d}E{ep['ep']:02d}": self._play(p,t,"series"),
                        on_right_click=lambda e,p=ep["path"],t=f"{show['show']} S{snum:02d}E{ep['ep']:02d}": self._right_click(e,p,t,"series"),
                        width=120, height=110)
                    card.pack(side="left", padx=4)

    def _populate_home(self):
        # Continue watching
        for w in self.continue_frame.winfo_children(): w.destroy()
        seen = set()
        for h in self.db.get("history",[])[:20]:
            p = h.get("path","")
            if not p or p in seen: continue
            seen.add(p)
            prog   = self._get_progress(p)
            status = self._get_status(p)
            if status in ("watching",) and prog < WATCHED_MARK:
                card = TitleCard(
                    self.continue_frame, h["title"], h.get("date",""),
                    prog, status,
                    on_play=lambda p2=p,t=h["title"],tp=h["type"]: self._play(p2,t,tp),
                    on_right_click=lambda e,p2=p,t=h["title"],tp=h["type"]: self._right_click(e,p2,t,tp),
                    width=180, height=130)
                card.pack(side="left", padx=6)

        # Recently added
        for w in self.recent_frame.winfo_children(): w.destroy()
        movies = list(reversed(scan_movies(self.cfg.get("movies_dir",""))))[:8]
        for m in movies:
            prog   = self._get_progress(m["path"])
            status = self._get_status(m["path"])
            card   = TitleCard(
                self.recent_frame, m["title"], m.get("year",""),
                prog, status,
                on_play=lambda p=m["path"],t=m["title"]: self._play(p,t,"movie"),
                on_right_click=lambda e,p=m["path"],t=m["title"]: self._right_click(e,p,t,"movie"),
                width=180, height=130)
            card.pack(side="left", padx=6)

    def _populate_history(self):
        self.hist_tree.delete(*self.hist_tree.get_children())
        for h in self.db.get("history",[]):
            dur = ""
            key = self._db_key(h.get("path",""))
            for sec in ("movies","series"):
                info = self.db.get(sec,{}).get(key,{})
                if info.get("duration"):
                    dur = fmt_duration(info["duration"]); break
            self.hist_tree.insert("", "end", values=(
                h.get("title",""), h.get("type",""),
                h.get("date",""), dur, h.get("status","")))

    def _filter_library(self, kind):
        if kind == "movies": self._populate_movies()
        else:                self._populate_series()

    # ── Season complete check ─────────────────────────────────────────────────
    def _check_season_complete(self, key):
        """If all eps in a season are watched, mark season complete."""
        series = scan_series(self.cfg.get("series_dir",""))
        for show in series:
            for snum, eps in show["seasons"].items():
                if any(self._db_key(ep["path"])==key for ep in eps):
                    if all(self.db.get("series",{}).get(
                           self._db_key(ep["path"]),{}).get("watched") for ep in eps):
                        # mark whole season
                        for ep in eps:
                            k2 = self._db_key(ep["path"])
                            self.db.setdefault("series",{})\
                                   .setdefault(k2,{})["season_complete"] = True
                        save_db(self.db)

    # ── Right-click menu ──────────────────────────────────────────────────────
    def _right_click(self, event, path, title, media_type):
        menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       font=F_UI, relief="flat")
        key     = self._db_key(path)
        section = "movies" if media_type=="movie" else "series"
        has_resume = self.db.get(section,{}).get(key,{}).get("position",0) > 0

        menu.add_command(label="▶  Play",
                         command=lambda: self._play(path,title,media_type))
        menu.add_command(label="✓  Mark as Watched",
                         command=lambda: self._mark_watched(path,media_type))
        menu.add_separator()
        if has_resume:
            menu.add_command(label="↩  Resume from " + fmt_duration(
                self.db[section][key]["position"]),
                command=lambda: self._play(path,title,media_type,resume=True))
            menu.add_command(label="✕  Clear Resume Point",
                command=lambda: self._clear_resume(path,media_type))
        menu.add_separator()
        menu.add_command(label="📁  Open Folder",
                         command=lambda: subprocess.Popen(
                             f'explorer /select,"{path}"'))
        menu.add_separator()
        menu.add_command(label="🗑  Remove from Library",
                         command=lambda: self._remove_from_library(path,media_type))
        menu.add_command(label="❌  Delete File",
                         command=lambda: self._delete_file(path,media_type))
        menu.tk_popup(event.x_root, event.y_root)

    def _mark_watched(self, path, media_type):
        key = self._db_key(path)
        sec = "movies" if media_type=="movie" else "series"
        self.db.setdefault(sec,{}).setdefault(key,{}).update(
            {"watched":True,"progress":1.0,"position":0})
        save_db(self.db); self._refresh_library()

    def _clear_resume(self, path, media_type):
        key = self._db_key(path)
        sec = "movies" if media_type=="movie" else "series"
        self.db.setdefault(sec,{}).setdefault(key,{})["position"] = 0
        save_db(self.db)

    def _remove_from_library(self, path, media_type):
        key = self._db_key(path)
        sec = "movies" if media_type=="movie" else "series"
        self.db.get(sec,{}).pop(key, None)
        save_db(self.db); self._refresh_library()

    def _delete_file(self, path, media_type):
        if messagebox.askyesno("Delete File",
            f"Permanently delete:\n{os.path.basename(path)}\n\nThis cannot be undone.",
            icon="warning"):
            try:
                os.remove(path)
                self._remove_from_library(path, media_type)
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _clear_history(self):
        if messagebox.askyesno("Clear History","Clear all watch history?"):
            self.db["history"] = []
            save_db(self.db); self._populate_history()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _browse_setting(self, key, var):
        if key == "vlc_path":
            p = filedialog.askopenfilename(
                title="Select VLC (libvlc.dll or vlc.exe)",
                filetypes=[("VLC files","*.dll *.exe"),("All","*.*")])
        else:
            p = filedialog.askdirectory(title=f"Select {key}")
        if p: var.set(p)

    def _save_settings(self):
        for k, v in self._setting_vars.items():
            self.cfg[k] = v.get()
        save_cfg(self.cfg)
        self._init_vlc()
        messagebox.showinfo("Saved","Settings saved!")
        self._refresh_library()

    # ── Navigation ────────────────────────────────────────────────────────────
    def _show_page(self, name):
        if self._current_page:
            self._current_page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self._current_page = self.pages[name]
        for n, btn in self.nav_btns.items():
            btn.configure(bg=CARD if n==name else "#08080f",
                          fg=TEXT if n==name else TEXT2)

    # ── Keyboard ──────────────────────────────────────────────────────────────
    def _on_key(self, e):
        key = e.keysym
        if key == "space":           self._toggle_pause()
        elif key == "Escape":
            if self.mini_mode: self._exit_mini()
        elif key == "f" or key=="F": self._toggle_fullscreen()
        elif key == "Right":
            if self.vlc: self.vlc.seek(min(1.0, self.vlc.get_position()+0.02))
        elif key == "Left":
            if self.vlc: self.vlc.seek(max(0.0, self.vlc.get_position()-0.02))

    def _toggle_fullscreen(self):
        state = self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", not state)

    def _on_resize(self, e):
        if e.widget == self.root:
            self.cfg["geometry"] = self.root.geometry()

    def on_close(self):
        if self.vlc: self.vlc.stop()
        save_cfg(self.cfg)
        save_db(self.db)
        self.root.destroy()

# ═══════════════════════════════════════════════════════════════════════════════
#  SCROLL FRAME HELPER
# ═══════════════════════════════════════════════════════════════════════════════
class _ScrollFrame(tk.Frame):
    def __init__(self, parent, height=None, **kw):
        kw.setdefault("bg", BG)
        super().__init__(parent, **kw)
        if height: self.configure(height=height)

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1*(e.delta//120),"units"))

# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app  = CineVault(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
