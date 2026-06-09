"""
CineVault - Netflix-style Media Player
Splash screen, embedded VLC, thumbnails, series drill-down, watch tracking
"""

import os, re, sys, json, time, math, threading, pathlib, subprocess, shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

# ── deps ──────────────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageTk, ImageDraw, ImageFilter
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════
CONFIG_PATH  = pathlib.Path.home() / ".cinevault_config.json"
DB_PATH      = pathlib.Path.home() / ".cinevault_db.json"
THUMB_DIR    = pathlib.Path.home() / ".cinevault_thumbs"
THUMB_DIR.mkdir(exist_ok=True)
WATCHED_MARK = 0.95
VIDEO_EXT    = {'.mp4','.mkv','.avi','.mov','.wmv','.m4v','.ts','.flv'}
CARD_W, CARD_H = 200, 150
THUMB_W, THUMB_H = 200, 112

BG       = "#0a0a0f"
BG2      = "#0f0f1a"
CARD     = "#141420"
CARD_H_C = "#1e1e30"
ACCENT   = "#e50914"
ACCENT2  = "#ff2d3a"
GOLD     = "#f5c518"
MUTED    = "#5a5a7a"
TEXT     = "#f0f0f0"
TEXT2    = "#a0a0c0"
GREEN    = "#46d369"
PROG_BG  = "#2a2a3a"
SIDEBAR  = "#08080f"

F_TITLE  = ("Georgia", 22, "bold")
F_HEAD   = ("Georgia", 13, "bold")
F_CARD   = ("Segoe UI", 9, "bold")
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
#  VLC WRAPPER — accepts vlc.exe OR libvlc.dll
# ═══════════════════════════════════════════════════════════════════════════════
class VLCPlayer:
    def __init__(self):
        self.instance  = None
        self.player    = None
        self.available = False
        self._vlc      = None

    def load(self, path):
        """Accept vlc.exe or libvlc.dll — resolve the dll automatically."""
        try:
            vlc_dir = os.path.dirname(os.path.abspath(path))
            # If user pointed to vlc.exe, find libvlc.dll in the same folder
            dll = os.path.join(vlc_dir, "libvlc.dll")
            if not os.path.isfile(dll):
                # Try the path itself if it ends in .dll
                if path.lower().endswith(".dll") and os.path.isfile(path):
                    dll = path
                    vlc_dir = os.path.dirname(dll)
                else:
                    return False, "libvlc.dll not found in VLC folder"

            os.environ["PYTHON_VLC_LIB_PATH"] = dll
            if vlc_dir not in sys.path:
                sys.path.insert(0, vlc_dir)
            if sys.platform == "win32":
                try: os.add_dll_directory(vlc_dir)
                except: pass

            import vlc as _vlc
            self._vlc      = _vlc
            self.instance  = _vlc.Instance("--no-xlib", "--quiet")
            self.player    = self.instance.media_player_new()
            self.available = True
            return True, "OK"
        except Exception as e:
            self.available = False
            return False, str(e)

    def set_window(self, hwnd):
        if not self.available: return
        try:
            if sys.platform == "win32": self.player.set_hwnd(hwnd)
            else:                       self.player.set_xwindow(hwnd)
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
            try: self.player.set_position(max(0.0, min(1.0, float(pct))))
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
            state = str(self.player.get_state())
            return "Ended" in state or "NothingSpecial" in state
        except: return False

# ═══════════════════════════════════════════════════════════════════════════════
#  THUMBNAIL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def thumb_path(video_path):
    key = pathlib.Path(video_path).stem
    return THUMB_DIR / f"{key}.jpg"

def extract_thumb(video_path):
    """Extract first frame via ffmpeg if available, else return None."""
    out = thumb_path(video_path)
    if out.exists(): return str(out)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-ss", "00:00:05",
            "-i", video_path,
            "-vframes", "1",
            "-vf", f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2",
            str(out)
        ], capture_output=True, timeout=15)
        if out.exists(): return str(out)
    except Exception:
        pass
    return None

def tmdb_poster(title, year=None, tmdb_key=""):
    """Fetch poster URL from TMDB."""
    if not REQUESTS_OK or not tmdb_key: return None
    try:
        params = {"api_key": tmdb_key, "query": title, "language": "en-US"}
        if year: params["year"] = year
        r = requests.get("https://api.themoviedb.org/3/search/movie",
                         params=params, timeout=5)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            poster = results[0].get("poster_path")
            if poster:
                return f"https://image.tmdb.org/t/p/w342{poster}"
    except: pass
    return None

def tmdb_tv_poster(title, tmdb_key=""):
    """Fetch TV show poster from TMDB."""
    if not REQUESTS_OK or not tmdb_key: return None
    try:
        params = {"api_key": tmdb_key, "query": title, "language": "en-US"}
        r = requests.get("https://api.themoviedb.org/3/search/tv",
                         params=params, timeout=5)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            poster = results[0].get("poster_path")
            if poster:
                return f"https://image.tmdb.org/t/p/w342{poster}"
    except: pass
    return None

def download_image(url, save_path):
    """Download image from URL to disk."""
    if not REQUESTS_OK: return False
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    except: return False

def make_placeholder(text, w=THUMB_W, h=THUMB_H):
    """Create a placeholder image with text."""
    if not PIL_OK: return None
    img  = Image.new("RGB", (w, h), color=(20, 20, 35))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, h], outline=(229, 9, 20), width=2)
    # Draw text centered
    lines = [text[i:i+16] for i in range(0, min(len(text), 48), 16)]
    y = h // 2 - len(lines) * 8
    for line in lines:
        bbox = draw.textbbox((0, 0), line)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, fill=(240, 240, 240))
        y += 18
    return img

def load_thumb_image(path, w=THUMB_W, h=THUMB_H):
    """Load image from path and resize, return PhotoImage or None."""
    if not PIL_OK: return None
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except: return None

# ═══════════════════════════════════════════════════════════════════════════════
#  LIBRARY SCANNER
# ═══════════════════════════════════════════════════════════════════════════════
def fmt_duration(secs):
    if not secs: return "0:00"
    h = int(secs)//3600; m = (int(secs)%3600)//60; s = int(secs)%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def scan_movies(folder):
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
                    if eps: seasons[snum] = eps
        if seasons:
            series.append({"show": show, "seasons": seasons, "path": show_path})
    return series

# ═══════════════════════════════════════════════════════════════════════════════
#  SPLASH SCREEN
# ═══════════════════════════════════════════════════════════════════════════════
class SplashScreen:
    def __init__(self, root, on_done):
        self.root    = root
        self.on_done = on_done
        self.alpha   = 0.0
        self._phase  = 0.0
        self._prog   = 0.0
        self._img_ref = None

        root.overrideredirect(True)
        root.attributes("-alpha", 0)
        root.configure(bg=BG)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w, h = 700, 400
        root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.canvas = tk.Canvas(root, width=w, height=h,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._w = w; self._h = h
        self._draw_frame()
        self._fade_in()

    def _draw_frame(self):
        w, h = self._w, self._h
        self.canvas.delete("all")

        if PIL_OK:
            img  = Image.new("RGBA", (w, h), (10, 10, 15, 255))
            draw = ImageDraw.Draw(img)
            colors = [(229,9,20,25),(255,45,58,15),(180,5,15,30)]
            for ci, (r,g,b,a) in enumerate(colors):
                pts   = []
                amp   = h * (0.06 + ci * 0.03)
                freq  = 0.007 - ci * 0.002
                ybase = h * (0.3 + ci * 0.25)
                for i in range(w // 2 + 1):
                    x = int(i * w / (w // 2))
                    y = int(ybase + amp * math.sin(freq * x + self._phase + ci * 1.2))
                    pts.append((x, y))
                poly = pts + [(w, h), (0, h)]
                draw.polygon(poly, fill=(r, g, b, a))
            photo = ImageTk.PhotoImage(img)
            self._img_ref = photo
            self.canvas.create_image(0, 0, image=photo, anchor="nw")

        # Title
        self.canvas.create_text(w//2, h//2 - 60,
            text="CINEVAULT",
            font=("Georgia", 48, "bold"),
            fill=ACCENT,
            stipple="" )

        # Subtitle
        sub_alpha = max(0, min(255, int((self._prog - 0.3) / 0.3 * 255)))
        if sub_alpha > 0:
            self.canvas.create_text(w//2, h//2 + 10,
                text="your media, your way",
                font=("Segoe UI", 14),
                fill=f"#{sub_alpha:02x}{sub_alpha:02x}{sub_alpha:02x}")

        # Loading bar background
        bar_y = h - 40
        self.canvas.create_rectangle(60, bar_y, w-60, bar_y+4,
                                     fill="#1a1a2e", outline="")
        # Loading bar fill
        bar_w = int((w - 120) * min(self._prog, 1.0))
        if bar_w > 0:
            self.canvas.create_rectangle(60, bar_y, 60+bar_w, bar_y+4,
                                         fill=ACCENT, outline="")

        # Version text
        self.canvas.create_text(w//2, h - 16,
            text="Loading...", font=("Segoe UI", 8), fill=MUTED)

    def _fade_in(self):
        self.alpha = min(1.0, self.alpha + 0.05)
        self.root.attributes("-alpha", self.alpha)
        self._phase += 0.04
        self._prog  += 0.012
        self._draw_frame()
        if self.alpha < 1.0 or self._prog < 1.0:
            self.root.after(30, self._fade_in)
        else:
            self.root.after(400, self._fade_out)

    def _fade_out(self):
        self.alpha = max(0.0, self.alpha - 0.06)
        self.root.attributes("-alpha", self.alpha)
        if self.alpha > 0:
            self.root.after(25, self._fade_out)
        else:
            self.root.destroy()
            self.on_done()

# ═══════════════════════════════════════════════════════════════════════════════
#  SCROLL FRAME
# ═══════════════════════════════════════════════════════════════════════════════
class ScrollFrame(tk.Frame):
    def __init__(self, parent, **kw):
        kw.setdefault("bg", BG)
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner.bind("<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self._win, width=e.width))
        self.canvas.bind("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))
        self.inner.bind("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))

# ═══════════════════════════════════════════════════════════════════════════════
#  MEDIA CARD WIDGET
# ═══════════════════════════════════════════════════════════════════════════════
class MediaCard(tk.Frame):
    def __init__(self, parent, title, subtitle, thumb_path_str,
                 progress, status, on_click, on_right_click=None, **kw):
        super().__init__(parent, bg=CARD, cursor="hand2",
                         width=CARD_W, height=CARD_H+50, **kw)
        self.pack_propagate(False)
        self._photo   = None
        self._on_click = on_click

        # Thumbnail area
        self.thumb_frame = tk.Label(self, bg="#1a1a2e",
                                    width=CARD_W, height=THUMB_H)
        self.thumb_frame.pack(fill="x")

        # Load thumbnail
        if thumb_path_str and os.path.isfile(thumb_path_str):
            photo = load_thumb_image(thumb_path_str)
            if photo:
                self._photo = photo
                self.thumb_frame.configure(image=photo)
        else:
            # Placeholder
            if PIL_OK:
                ph    = make_placeholder(title)
                photo = ImageTk.PhotoImage(ph)
                self._photo = photo
                self.thumb_frame.configure(image=photo)
            else:
                self.thumb_frame.configure(text=title[:20], fg=TEXT2,
                                           font=F_SMALL)

        # Progress bar under thumb
        pb = tk.Frame(self, bg=PROG_BG, height=3)
        pb.pack(fill="x")
        pb.pack_propagate(False)
        if progress > 0:
            col = GREEN if progress >= WATCHED_MARK else ACCENT
            tk.Frame(pb, bg=col, height=3).place(
                relx=0, rely=0, relwidth=min(progress, 1.0), relheight=1)

        # Info area
        info = tk.Frame(self, bg=CARD, pady=4)
        info.pack(fill="x", padx=6)

        # Status dot
        if status == "watched":
            dot, dot_col = "✓", GREEN
        elif status == "watching":
            dot, dot_col = "▶", ACCENT2
        else:
            dot, dot_col = "●", MUTED

        top_row = tk.Frame(info, bg=CARD)
        top_row.pack(fill="x")
        tk.Label(top_row, text=dot, font=("Segoe UI",7),
                 bg=CARD, fg=dot_col).pack(side="left")
        tk.Label(top_row, text=title[:22], font=F_CARD,
                 bg=CARD, fg=TEXT, anchor="w").pack(side="left", padx=2)

        if subtitle:
            tk.Label(info, text=subtitle, font=F_SMALL,
                     bg=CARD, fg=TEXT2, anchor="w").pack(fill="x")

        # Bind clicks
        for w in [self, self.thumb_frame, info] + list(info.winfo_children()) + list(top_row.winfo_children()):
            try:
                w.bind("<Button-1>", lambda e: on_click())
                if on_right_click:
                    w.bind("<Button-3>", on_right_click)
                w.bind("<Enter>", self._hover_on)
                w.bind("<Leave>", self._hover_off)
            except: pass

    def _hover_on(self, e=None):
        self.configure(bg=CARD_H_C)
    def _hover_off(self, e=None):
        self.configure(bg=CARD)

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════
class CineVault:
    def __init__(self, root):
        self.root         = root
        self.cfg          = load_cfg()
        self.db           = load_db()
        self.vlc          = VLCPlayer()
        self.current      = None
        self.mini_mode    = False
        self._poll_id     = None
        self._seek_drag   = False
        self._cur_series  = None   # currently viewed series
        self._cur_season  = None   # currently viewed season
        self._view_stack  = []     # navigation stack

        root.title("CineVault")
        geo = self.cfg.get("geometry", "1300x800")
        root.geometry(geo)
        root.configure(bg=BG)
        root.minsize(960, 640)
        root.bind("<Configure>", self._on_resize)
        root.bind("<KeyPress>",  self._on_key)

        # Try loading VLC from saved path
        vlc_path = self.cfg.get("vlc_path", "")
        if vlc_path:
            ok, msg = self.vlc.load(vlc_path)
            if not ok:
                self.cfg["vlc_status"] = f"⚠ VLC error: {msg}"

        self._build_ui()
        self.root.after(600, self._refresh_library)

    # ══════════════════════════════════════════════════════════════════════════
    #  UI BUILD
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # Sidebar
        self.sidebar = tk.Frame(self.root, bg=SIDEBAR, width=190)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # Main content
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        # Pages dict
        self.pages = {}
        for name in ("home","movies","series","history","settings"):
            f = tk.Frame(self.main, bg=BG)
            self.pages[name] = f

        self._build_home()
        self._build_movies_page()
        self._build_series_page()
        self._build_history_page()
        self._build_settings_page()

        self._current_page = None
        self._show_page("home")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = self.sidebar
        # Logo
        logo = tk.Frame(sb, bg=SIDEBAR, pady=20)
        logo.pack(fill="x")
        tk.Label(logo, text="CINEVAULT",
                 font=("Georgia", 18, "bold"),
                 bg=SIDEBAR, fg=ACCENT).pack()
        tk.Label(logo, text="your media, your way",
                 font=("Segoe UI", 7), bg=SIDEBAR, fg=MUTED).pack()
        tk.Frame(sb, bg=ACCENT, height=1).pack(fill="x", padx=16)

        nav = [("🏠  Home","home"),("🎬  Movies","movies"),
               ("📺  Series","series"),("📋  History","history"),
               ("⚙   Settings","settings")]
        self.nav_btns = {}
        for label, page in nav:
            btn = tk.Button(sb, text=label, font=F_UI_B,
                            bg=SIDEBAR, fg=TEXT2, relief="flat",
                            anchor="w", padx=20, pady=10, cursor="hand2",
                            activebackground=CARD, activeforeground=TEXT,
                            command=lambda p=page: self._show_page(p))
            btn.pack(fill="x")
            self.nav_btns[page] = btn

        tk.Frame(sb, bg=ACCENT, height=1).pack(fill="x", padx=16, side="bottom", pady=(0,4))
        self.now_playing_lbl = tk.Label(sb, text="", font=("Segoe UI",7),
                                         bg=SIDEBAR, fg=MUTED,
                                         wraplength=170, justify="left")
        self.now_playing_lbl.pack(side="bottom", fill="x", padx=8, pady=4)

    # ── Page builder helpers ──────────────────────────────────────────────────
    def _show_page(self, name):
        if self._current_page:
            self._current_page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self._current_page = self.pages[name]
        for n, btn in self.nav_btns.items():
            btn.configure(bg=CARD if n==name else SIDEBAR,
                          fg=TEXT if n==name else TEXT2)
        # Reset series drill-down when leaving series page
        if name != "series":
            self._cur_series = None
            self._cur_season = None

    # ── Home ──────────────────────────────────────────────────────────────────
    def _build_home(self):
        p = self.pages["home"]
        sf = ScrollFrame(p); sf.pack(fill="both", expand=True)
        inner = sf.inner

        tk.Label(inner, text="Continue Watching", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=12).pack(anchor="w", padx=20)
        self.continue_row = tk.Frame(inner, bg=BG)
        self.continue_row.pack(fill="x", padx=20, pady=(0,20))

        tk.Label(inner, text="Recently Added", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=8).pack(anchor="w", padx=20)
        self.recent_row = tk.Frame(inner, bg=BG)
        self.recent_row.pack(fill="x", padx=20)

    # ── Movies page ───────────────────────────────────────────────────────────
    def _build_movies_page(self):
        p = self.pages["movies"]
        # Toolbar
        bar = tk.Frame(p, bg=BG2, pady=8); bar.pack(fill="x")
        tk.Label(bar, text="🎬  Movies", font=F_HEAD,
                 bg=BG2, fg=TEXT).pack(side="left", padx=16)

        self.mov_search = tk.StringVar()
        tk.Entry(bar, textvariable=self.mov_search, bg=CARD, fg=TEXT,
                 relief="flat", font=F_UI, insertbackground=TEXT,
                 width=20).pack(side="left", padx=8, ipady=4)
        self.mov_search.trace_add("write", lambda *_: self._populate_movies())

        self.mov_sort = tk.StringVar(value="Name")
        ttk.Combobox(bar, textvariable=self.mov_sort, width=16,
                     values=["Name","Recently Added","Last Watched"],
                     state="readonly", font=F_UI).pack(side="left", padx=4)
        self.mov_sort.trace_add("write", lambda *_: self._populate_movies())

        self.mov_filter = tk.StringVar(value="All")
        ttk.Combobox(bar, textvariable=self.mov_filter, width=12,
                     values=["All","Unwatched","Watching","Watched"],
                     state="readonly", font=F_UI).pack(side="left", padx=4)
        self.mov_filter.trace_add("write", lambda *_: self._populate_movies())

        tk.Button(bar, text="🌐 TMDB Refresh",
                  command=self._tmdb_refresh_movies,
                  bg=CARD, fg=TEXT2, relief="flat", font=F_UI,
                  cursor="hand2", padx=8).pack(side="right", padx=4)
        tk.Button(bar, text="⟳ Refresh",
                  command=self._refresh_library,
                  bg=CARD, fg=TEXT2, relief="flat", font=F_UI,
                  cursor="hand2", padx=8).pack(side="right", padx=4)

        # Library scroll
        tk.Label(p, text="▸  Library", font=F_UI_B,
                 bg=BG, fg=TEXT2).pack(anchor="w", padx=16, pady=(8,2))
        self.mov_scroll = ScrollFrame(p)
        self.mov_scroll.pack(fill="both", expand=True)

        # Watched section
        tk.Label(p, text="✓  Watched", font=F_UI_B,
                 bg=BG, fg=GREEN).pack(anchor="w", padx=16, pady=(6,2))
        self.mov_watched_scroll = ScrollFrame(p, height=220)
        self.mov_watched_scroll.pack(fill="x")

    # ── Series page (drill-down) ───────────────────────────────────────────────
    def _build_series_page(self):
        p = self.pages["series"]
        # Top bar (changes based on view level)
        self.series_bar = tk.Frame(p, bg=BG2, pady=8)
        self.series_bar.pack(fill="x")
        self._build_series_toolbar()

        # Content area — swapped out per drill-down level
        self.series_content = tk.Frame(p, bg=BG)
        self.series_content.pack(fill="both", expand=True)

    def _build_series_toolbar(self, back=False, title="📺  Series"):
        for w in self.series_bar.winfo_children(): w.destroy()

        if back:
            tk.Button(self.series_bar, text="◀  Back",
                      command=self._series_go_back,
                      bg=CARD, fg=TEXT2, relief="flat", font=F_UI_B,
                      cursor="hand2", padx=10).pack(side="left", padx=8)

        tk.Label(self.series_bar, text=title, font=F_HEAD,
                 bg=BG2, fg=TEXT).pack(side="left", padx=8)

        if not back:
            self.ser_search = tk.StringVar()
            tk.Entry(self.series_bar, textvariable=self.ser_search,
                     bg=CARD, fg=TEXT, relief="flat", font=F_UI,
                     insertbackground=TEXT, width=20).pack(side="left", padx=8, ipady=4)
            self.ser_search.trace_add("write", lambda *_: self._populate_series_list())

            self.ser_sort = tk.StringVar(value="Name")
            ttk.Combobox(self.series_bar, textvariable=self.ser_sort, width=14,
                         values=["Name","Recently Added","Last Watched"],
                         state="readonly", font=F_UI).pack(side="left", padx=4)
            self.ser_sort.trace_add("write", lambda *_: self._populate_series_list())

            tk.Button(self.series_bar, text="🌐 TMDB Refresh",
                      command=self._tmdb_refresh_series,
                      bg=CARD, fg=TEXT2, relief="flat", font=F_UI,
                      cursor="hand2", padx=8).pack(side="right", padx=4)
            tk.Button(self.series_bar, text="⟳ Refresh",
                      command=self._refresh_library,
                      bg=CARD, fg=TEXT2, relief="flat", font=F_UI,
                      cursor="hand2", padx=8).pack(side="right", padx=4)

    def _clear_series_content(self):
        for w in self.series_content.winfo_children(): w.destroy()

    def _series_go_back(self):
        if self._cur_season is not None:
            # Go back to season list
            self._cur_season = None
            self._show_series_seasons(self._cur_series)
        elif self._cur_series is not None:
            # Go back to series list
            self._cur_series = None
            self._build_series_toolbar(back=False)
            self._populate_series_list()

    # ── History ───────────────────────────────────────────────────────────────
    def _build_history_page(self):
        p = self.pages["history"]
        tk.Label(p, text="Watch History", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=16).pack(anchor="w", padx=20)
        tk.Button(p, text="Clear History",
                  command=self._clear_history,
                  bg=CARD, fg=ACCENT, relief="flat", font=F_UI,
                  cursor="hand2", padx=10).pack(anchor="e", padx=20)

        cols = ("Title","Type","Date","Duration","Status")
        style = ttk.Style()
        style.configure("Hist.Treeview", background=CARD,
                        fieldbackground=CARD, foreground=TEXT,
                        rowheight=24, font=F_UI)
        style.configure("Hist.Treeview.Heading",
                        background=CARD_H_C, foreground=TEXT2, font=F_UI_B)
        self.hist_tree = ttk.Treeview(p, columns=cols, show="headings",
                                       style="Hist.Treeview", height=20)
        for c in cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=160 if c=="Title" else 100)
        sb2 = ttk.Scrollbar(p, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb2.set)
        self.hist_tree.pack(side="left", fill="both", expand=True, padx=(20,0), pady=8)
        sb2.pack(side="left", fill="y", pady=8)

    # ── Settings ──────────────────────────────────────────────────────────────
    def _build_settings_page(self):
        p = self.pages["settings"]
        tk.Label(p, text="⚙  Settings", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=16).pack(anchor="w", padx=20)

        rows = [
            ("VLC Path",      "vlc_path",   "Point to vlc.exe in your VLC install folder"),
            ("Movies Folder", "movies_dir", "Your organised movies output folder"),
            ("Series Folder", "series_dir", "Your organised series output folder"),
            ("TMDB API Key",  "tmdb_key",   "Free key from themoviedb.org (optional)"),
        ]
        self._svars = {}
        for label, key, hint in rows:
            row = tk.Frame(p, bg=BG); row.pack(fill="x", padx=20, pady=6)
            tk.Label(row, text=label, font=F_UI_B, bg=BG, fg=TEXT,
                     width=16, anchor="w").pack(side="left")
            var = tk.StringVar(value=self.cfg.get(key,""))
            self._svars[key] = var
            tk.Entry(row, textvariable=var, bg=CARD, fg=TEXT, relief="flat",
                     font=F_UI, insertbackground=TEXT,
                     width=44).pack(side="left", ipady=4, padx=(0,8))
            tk.Button(row, text="Browse",
                      command=lambda k=key, v=var: self._browse_setting(k,v),
                      bg=ACCENT, fg="white", relief="flat", font=F_UI,
                      cursor="hand2", padx=8).pack(side="left")
            tk.Label(p, text=hint, font=F_SMALL,
                     bg=BG, fg=MUTED).pack(anchor="w", padx=(162,0))

        # VLC status label
        self.vlc_status_lbl = tk.Label(p, text=self._vlc_status_text(),
                                        font=F_UI, bg=BG,
                                        fg=GREEN if self.vlc.available else ACCENT)
        self.vlc_status_lbl.pack(anchor="w", padx=20, pady=(4,0))

        tk.Button(p, text="💾  Save Settings",
                  command=self._save_settings,
                  bg=ACCENT, fg="white", relief="flat",
                  font=("Segoe UI Semibold",10), cursor="hand2",
                  padx=16, pady=8).pack(padx=20, pady=16, anchor="w")

    def _vlc_status_text(self):
        if self.vlc.available:
            return "✅  VLC loaded successfully"
        path = self.cfg.get("vlc_path","")
        if not path:
            return "⚠  VLC path not set — go to Settings"
        return self.cfg.get("vlc_status", "⚠  VLC not loaded — check path")

    # ══════════════════════════════════════════════════════════════════════════
    #  PLAYER PANEL
    # ══════════════════════════════════════════════════════════════════════════
    def _ensure_player_panel(self):
        if hasattr(self, "_player_built"): return
        self._player_built = True

        self.player_panel = tk.Frame(self.root, bg="#000")
        self.player_panel.pack(side="right", fill="both", expand=True)
        self.player_panel.pack_forget()

        self.video_canvas = tk.Canvas(self.player_panel, bg="#000",
                                       highlightthickness=0, cursor="hand2")
        self.video_canvas.pack(fill="both", expand=True)
        self.video_canvas.bind("<Double-Button-1>", self._toggle_mini)
        self.video_canvas.bind("<Button-1>",        lambda e: self._toggle_pause())

        ctrl = tk.Frame(self.player_panel, bg="#0d0d0d", pady=6)
        ctrl.pack(fill="x", side="bottom")

        self.seek_var = tk.DoubleVar()
        seek = ttk.Scale(ctrl, from_=0, to=1, variable=self.seek_var,
                         orient="horizontal", command=self._on_seek_move)
        seek.pack(fill="x", padx=10, pady=(0,4))
        seek.bind("<ButtonPress-1>",   lambda e: setattr(self,"_seek_drag",True))
        seek.bind("<ButtonRelease-1>", self._on_seek_release)

        btn_row = tk.Frame(ctrl, bg="#0d0d0d"); btn_row.pack(fill="x", padx=10)
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
        self.now_lbl = tk.Label(btn_row, text="", font=F_UI,
                                 bg="#0d0d0d", fg=MUTED)
        self.now_lbl.pack(side="right", padx=10)
        tk.Label(btn_row, text="🔊", bg="#0d0d0d", fg=TEXT2).pack(side="right")
        self.vol_var = tk.IntVar(value=80)
        ttk.Scale(btn_row, from_=0, to=100, variable=self.vol_var,
                  orient="horizontal", length=80,
                  command=lambda v: self.vlc.set_volume(int(float(v)))
                  ).pack(side="right", padx=4)

        self.mini_list = tk.Frame(self.player_panel, bg=BG2, width=270)
        self.mini_list.pack_forget()

    # ══════════════════════════════════════════════════════════════════════════
    #  PLAYBACK
    # ══════════════════════════════════════════════════════════════════════════
    def _play(self, path, title, media_type, resume=True):
        if not self.vlc.available:
            messagebox.showwarning("VLC not loaded",
                "Please set the VLC path in Settings and save.")
            self._show_page("settings")
            return
        self._ensure_player_panel()
        self.player_panel.pack(side="right", fill="both", expand=True)
        self.root.update()
        self.vlc.set_window(self.video_canvas.winfo_id())

        key   = os.path.basename(path)
        start = 0
        if resume:
            sec   = "movies" if media_type=="movie" else "series"
            info  = self.db.get(sec,{}).get(key,{})
            pos   = info.get("position",0)
            dur   = info.get("duration",0)
            if dur > 0 and pos/dur < WATCHED_MARK:
                start = pos

        self.vlc.play(path, start_pos=start)
        self.vlc.set_volume(self.vol_var.get())

        self.current = {"path":path,"title":title,
                        "type":media_type,"key":key}
        self.now_lbl.configure(text=f"▶  {title[:40]}")
        self.now_playing_lbl.configure(text=f"▶ {title[:22]}")
        self.play_btn.configure(text="⏸")

        self.db["history"].insert(0,{"title":title,"type":media_type,
            "date":time.strftime("%Y-%m-%d %H:%M"),"path":path,"status":"watching"})
        save_db(self.db)
        self._poll_playback()
        self._build_mini_list(path, media_type)

    def _poll_playback(self):
        if not self.current or not self.vlc: return
        pos = self.vlc.get_position()
        cur = self.vlc.get_time()
        dur = self.vlc.get_duration()

        if not self._seek_drag and dur > 0:
            self.seek_var.set(pos)
        if dur > 0:
            self.time_lbl.configure(
                text=f"{fmt_duration(cur)} / {fmt_duration(dur)}")

        key = self.current["key"]
        sec = "movies" if self.current["type"]=="movie" else "series"
        self.db.setdefault(sec,{})[key] = {
            "position":cur,"duration":dur,"progress":pos,
            "title":self.current["title"],
            "last_watched":time.strftime("%Y-%m-%d %H:%M"),
        }

        if pos >= WATCHED_MARK:
            self.db[sec][key]["watched"]  = True
            self.db[sec][key]["progress"] = 1.0
            self.db[sec][key]["position"] = 0
            save_db(self.db)
            self._refresh_library()

        save_db(self.db)
        if self.vlc.is_ended():
            self._on_ended(); return
        self._poll_id = self.root.after(1000, self._poll_playback)

    def _on_ended(self):
        if not self.current: return
        key = self.current["key"]
        sec = "movies" if self.current["type"]=="movie" else "series"
        self.db.setdefault(sec,{}).setdefault(key,{}).update(
            {"watched":True,"progress":1.0,"position":0})
        for h in self.db["history"]:
            if h.get("path") == self.current["path"]:
                h["status"] = "watched"; break
        save_db(self.db); self._refresh_library()
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

    def _on_seek_move(self, val): pass
    def _on_seek_release(self, e):
        self._seek_drag = False
        if self.vlc: self.vlc.seek(self.seek_var.get())

    # ── Mini player ───────────────────────────────────────────────────────────
    def _toggle_mini(self, e=None):
        if not hasattr(self,"player_panel"): return
        self._exit_mini() if self.mini_mode else self._enter_mini()

    def _enter_mini(self):
        self.mini_mode = True
        self.video_canvas.configure(width=360, height=202)
        self.player_panel.pack_configure(expand=False, fill="none")
        self.mini_list.pack(side="right", fill="both", expand=True)

    def _exit_mini(self):
        self.mini_mode = False
        self.mini_list.pack_forget()
        self.video_canvas.configure(width=0, height=0)
        self.player_panel.pack_configure(expand=True, fill="both")

    def _build_mini_list(self, current_path, media_type):
        if not hasattr(self,"mini_list"): return
        for w in self.mini_list.winfo_children(): w.destroy()
        tk.Label(self.mini_list,
                 text="Up Next" if media_type=="series" else "More Movies",
                 font=F_UI_B, bg=BG2, fg=TEXT, pady=8).pack(fill="x", padx=10)
        sf = ScrollFrame(self.mini_list); sf.pack(fill="both", expand=True)
        inner = sf.inner

        if media_type == "movie":
            for m in scan_movies(self.cfg.get("movies_dir","")):
                if m["path"] == current_path: continue
                self._mini_row(inner, f"{m['title']} {m.get('year','')}", m["path"], "movie")
        else:
            for show in scan_series(self.cfg.get("series_dir","")):
                for snum in sorted(show["seasons"]):
                    for ep in show["seasons"][snum]:
                        if ep["path"] == current_path: continue
                        lbl = f"{show['show']} S{snum:02d}E{ep['ep']:02d}"
                        self._mini_row(inner, lbl, ep["path"], "series")

    def _mini_row(self, parent, label, path, mtype):
        f = tk.Frame(parent, bg=BG2, cursor="hand2")
        f.pack(fill="x", padx=6, pady=2)
        tk.Label(f, text=label, font=F_UI, bg=BG2, fg=TEXT2,
                 wraplength=230, justify="left").pack(side="left", padx=8, pady=6)
        f.bind("<Button-1>",        lambda e: setattr(self,"_queued",(path,label,mtype)))
        f.bind("<Double-Button-1>", lambda e: self._play(path,label,mtype))
        f.bind("<Enter>",           lambda e: f.configure(bg=CARD_H_C))
        f.bind("<Leave>",           lambda e: f.configure(bg=BG2))

    # ══════════════════════════════════════════════════════════════════════════
    #  LIBRARY POPULATION
    # ══════════════════════════════════════════════════════════════════════════
    def _db_key(self, path):   return os.path.basename(path)
    def _get_progress(self, path):
        key = self._db_key(path)
        for sec in ("movies","series"):
            p = self.db.get(sec,{}).get(key,{}).get("progress",0)
            if p > 0: return p
        return 0.0
    def _get_status(self, path):
        key = self._db_key(path)
        for sec in ("movies","series"):
            info = self.db.get(sec,{}).get(key,{})
            if info.get("watched"): return "watched"
            if info.get("progress",0) > 0.01: return "watching"
        return "unwatched"

    def _get_thumb(self, path, title=""):
        """Return thumb path, extracting from video if needed."""
        tp = str(thumb_path(path))
        if os.path.isfile(tp): return tp
        # Extract in background
        def _ex():
            result = extract_thumb(path)
        threading.Thread(target=_ex, daemon=True).start()
        return None

    def _card_row(self, parent):
        """Start a new horizontal card row."""
        row = tk.Frame(parent, bg=BG)
        row.pack(anchor="w", padx=16, pady=6)
        return row

    def _populate_movies(self):
        for w in self.mov_scroll.inner.winfo_children(): w.destroy()
        for w in self.mov_watched_scroll.inner.winfo_children(): w.destroy()

        search = self.mov_search.get().lower()
        sort   = self.mov_sort.get()
        filt   = self.mov_filter.get()
        movies = scan_movies(self.cfg.get("movies_dir",""))

        if sort == "Name":            movies.sort(key=lambda m: m["title"])
        elif sort == "Recently Added": movies = list(reversed(movies))
        elif sort == "Last Watched":
            movies.sort(key=lambda m: self.db.get("movies",{}).get(
                self._db_key(m["path"]),{}).get("last_watched",""), reverse=True)

        lib_row = self._card_row(self.mov_scroll.inner)
        wat_row = self._card_row(self.mov_watched_scroll.inner)
        lib_count = wat_count = 0
        COLS = 5

        for m in movies:
            if search and search not in m["title"].lower(): continue
            status   = self._get_status(m["path"])
            progress = self._get_progress(m["path"])
            if filt != "All" and status != filt.lower(): continue

            sub  = m["year"] if m["year"] else ""
            tp   = self._get_thumb(m["path"], m["title"])
            dest = self.mov_scroll.inner if status != "watched" else self.mov_watched_scroll.inner

            if status != "watched":
                if lib_count % COLS == 0 and lib_count > 0:
                    lib_row = self._card_row(self.mov_scroll.inner)
                row = lib_row; lib_count += 1
            else:
                if wat_count % COLS == 0 and wat_count > 0:
                    wat_row = self._card_row(self.mov_watched_scroll.inner)
                row = wat_row; wat_count += 1

            p = m["path"]; t = m["title"]
            MediaCard(row, t, sub, tp, progress, status,
                      on_click=lambda p=p,t=t: self._play(p,t,"movie"),
                      on_right_click=lambda e,p=p,t=t: self._right_click(e,p,t,"movie")
                      ).pack(side="left", padx=4)

    def _populate_series_list(self):
        """Show top-level series cards."""
        self._clear_series_content()
        self._build_series_toolbar(back=False)
        self._cur_series = None; self._cur_season = None

        search = self.ser_search.get().lower() if hasattr(self,"ser_search") else ""
        sort   = self.ser_sort.get() if hasattr(self,"ser_sort") else "Name"
        series = scan_series(self.cfg.get("series_dir",""))
        if sort == "Name": series.sort(key=lambda s: s["show"])

        sf = ScrollFrame(self.series_content)
        sf.pack(fill="both", expand=True)
        inner = sf.inner

        row = self._card_row(inner)
        count = 0; COLS = 5
        for show in series:
            if search and search not in show["show"].lower(): continue

            # Overall show status
            all_eps  = [ep for sn in show["seasons"].values() for ep in sn]
            watched  = sum(1 for ep in all_eps if self._get_status(ep["path"])=="watched")
            progress = watched / len(all_eps) if all_eps else 0
            status   = "watched" if progress >= 1.0 else ("watching" if progress > 0 else "unwatched")

            # Show poster — use first episode thumb or show folder art
            first_ep = all_eps[0]["path"] if all_eps else ""
            tp = self._get_thumb(first_ep, show["show"]) if first_ep else None

            if count % COLS == 0 and count > 0:
                row = self._card_row(inner)

            s = show
            MediaCard(row, show["show"],
                      f"{len(show['seasons'])} Season(s) · {len(all_eps)} Episodes",
                      tp, progress, status,
                      on_click=lambda s=s: self._show_series_seasons(s),
                      on_right_click=None
                      ).pack(side="left", padx=4)
            count += 1

    def _show_series_seasons(self, show):
        """Show season cards for a selected series."""
        self._cur_series = show
        self._cur_season = None
        self._clear_series_content()
        self._build_series_toolbar(back=True, title=f"📺  {show['show']}")

        sf = ScrollFrame(self.series_content)
        sf.pack(fill="both", expand=True)
        inner = sf.inner

        tk.Label(inner, text="Seasons", font=F_HEAD,
                 bg=BG, fg=TEXT, pady=10).pack(anchor="w", padx=16)

        row = self._card_row(inner)
        COLS = 5
        for i, snum in enumerate(sorted(show["seasons"])):
            eps      = show["seasons"][snum]
            watched  = sum(1 for ep in eps if self._get_status(ep["path"])=="watched")
            progress = watched / len(eps) if eps else 0
            status   = "watched" if progress >= 1.0 else ("watching" if progress > 0 else "unwatched")
            first_ep = eps[0]["path"] if eps else ""
            tp = self._get_thumb(first_ep, show["show"]) if first_ep else None

            if i % COLS == 0 and i > 0:
                row = self._card_row(inner)

            sn = snum
            MediaCard(row, f"Season {snum}",
                      f"{len(eps)} Episodes · {int(progress*100)}% watched",
                      tp, progress, status,
                      on_click=lambda sn=sn: self._show_season_episodes(show, sn)
                      ).pack(side="left", padx=4)

    def _show_season_episodes(self, show, snum):
        """Show episode cards for a selected season."""
        self._cur_season = snum
        self._clear_series_content()
        self._build_series_toolbar(
            back=True,
            title=f"📺  {show['show']}  ›  Season {snum}")

        sf = ScrollFrame(self.series_content)
        sf.pack(fill="both", expand=True)
        inner = sf.inner

        eps = show["seasons"][snum]
        row = self._card_row(inner)
        COLS = 5
        for i, ep in enumerate(eps):
            progress = self._get_progress(ep["path"])
            status   = self._get_status(ep["path"])
            tp       = self._get_thumb(ep["path"])
            ep_label = f"E{ep['ep']:02d}"
            ep_title = f"{show['show']} S{snum:02d}E{ep['ep']:02d}"

            if i % COLS == 0 and i > 0:
                row = self._card_row(inner)

            p = ep["path"]; t = ep_title
            MediaCard(row, ep_label, ep_title,
                      tp, progress, status,
                      on_click=lambda p=p,t=t: self._play(p,t,"series"),
                      on_right_click=lambda e,p=p,t=t: self._right_click(e,p,t,"series")
                      ).pack(side="left", padx=4)

    def _populate_home(self):
        for w in self.continue_row.winfo_children(): w.destroy()
        for w in self.recent_row.winfo_children(): w.destroy()

        # Continue watching
        seen = set()
        for h in self.db.get("history",[])[:20]:
            p = h.get("path","")
            if not p or p in seen: continue
            seen.add(p)
            prog   = self._get_progress(p)
            status = self._get_status(p)
            if status == "watching" and prog < WATCHED_MARK:
                tp = self._get_thumb(p, h["title"])
                t  = h["title"]; mt = h["type"]
                MediaCard(self.continue_row, t, h.get("date",""),
                          tp, prog, status,
                          on_click=lambda p=p,t=t,mt=mt: self._play(p,t,mt)
                          ).pack(side="left", padx=6)

        # Recently added movies
        for m in list(reversed(scan_movies(self.cfg.get("movies_dir",""))))[:6]:
            prog   = self._get_progress(m["path"])
            status = self._get_status(m["path"])
            tp     = self._get_thumb(m["path"], m["title"])
            p = m["path"]; t = m["title"]
            MediaCard(self.recent_row, t, m.get("year",""),
                      tp, prog, status,
                      on_click=lambda p=p,t=t: self._play(p,t,"movie")
                      ).pack(side="left", padx=6)

    def _populate_history(self):
        self.hist_tree.delete(*self.hist_tree.get_children())
        for h in self.db.get("history",[]):
            key = self._db_key(h.get("path",""))
            dur = ""
            for sec in ("movies","series"):
                info = self.db.get(sec,{}).get(key,{})
                if info.get("duration"):
                    dur = fmt_duration(info["duration"]); break
            self.hist_tree.insert("","end", values=(
                h.get("title",""),h.get("type",""),
                h.get("date",""),dur,h.get("status","")))

    def _refresh_library(self):
        self._populate_movies()
        self._populate_home()
        self._populate_history()
        # Refresh whichever series view is active
        if self._cur_season is not None and self._cur_series is not None:
            self._show_season_episodes(self._cur_series, self._cur_season)
        elif self._cur_series is not None:
            self._show_series_seasons(self._cur_series)
        else:
            self._populate_series_list()

    # ══════════════════════════════════════════════════════════════════════════
    #  TMDB REFRESH (manual)
    # ══════════════════════════════════════════════════════════════════════════
    def _tmdb_refresh_movies(self):
        key = self.cfg.get("tmdb_key","")
        if not key:
            messagebox.showwarning("No TMDB Key",
                "Add your TMDB API key in Settings first."); return
        movies = scan_movies(self.cfg.get("movies_dir",""))
        def _run():
            for m in movies:
                tp   = str(thumb_path(m["path"]))
                url  = tmdb_poster(m["title"], m.get("year"), key)
                if url: download_image(url, tp)
            self.root.after(0, self._populate_movies)
        threading.Thread(target=_run, daemon=True).start()
        messagebox.showinfo("TMDB Refresh","Fetching posters in background…")

    def _tmdb_refresh_series(self):
        key = self.cfg.get("tmdb_key","")
        if not key:
            messagebox.showwarning("No TMDB Key",
                "Add your TMDB API key in Settings first."); return
        series = scan_series(self.cfg.get("series_dir",""))
        def _run():
            for show in series:
                # Use show poster for all episodes in the show
                url = tmdb_tv_poster(show["show"], key)
                if url:
                    for snum in show["seasons"]:
                        for ep in show["seasons"][snum]:
                            tp = str(thumb_path(ep["path"]))
                            if not os.path.isfile(tp):
                                download_image(url, tp)
            self.root.after(0, self._populate_series_list)
        threading.Thread(target=_run, daemon=True).start()
        messagebox.showinfo("TMDB Refresh","Fetching series posters in background…")

    # ══════════════════════════════════════════════════════════════════════════
    #  RIGHT-CLICK MENU
    # ══════════════════════════════════════════════════════════════════════════
    def _right_click(self, event, path, title, mtype):
        menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       font=F_UI, relief="flat")
        key  = self._db_key(path)
        sec  = "movies" if mtype=="movie" else "series"
        info = self.db.get(sec,{}).get(key,{})
        pos  = info.get("position",0)

        menu.add_command(label="▶  Play",
                         command=lambda: self._play(path,title,mtype))
        menu.add_command(label="✓  Mark as Watched",
                         command=lambda: self._mark_watched(path,mtype))
        if pos > 0:
            menu.add_command(label=f"↩  Resume from {fmt_duration(pos)}",
                             command=lambda: self._play(path,title,mtype,resume=True))
            menu.add_command(label="✕  Clear Resume Point",
                             command=lambda: self._clear_resume(path,mtype))
        menu.add_separator()
        menu.add_command(label="📁  Open Folder",
                         command=lambda: subprocess.Popen(
                             f'explorer /select,"{path}"'))
        menu.add_separator()
        menu.add_command(label="🗑  Remove from Library",
                         command=lambda: self._remove(path,mtype))
        menu.add_command(label="❌  Delete File",
                         command=lambda: self._delete_file(path,mtype))
        menu.tk_popup(event.x_root, event.y_root)

    def _mark_watched(self, path, mtype):
        key = self._db_key(path)
        sec = "movies" if mtype=="movie" else "series"
        self.db.setdefault(sec,{}).setdefault(key,{}).update(
            {"watched":True,"progress":1.0,"position":0})
        save_db(self.db); self._refresh_library()

    def _clear_resume(self, path, mtype):
        key = self._db_key(path)
        sec = "movies" if mtype=="movie" else "series"
        self.db.setdefault(sec,{}).setdefault(key,{})["position"] = 0
        save_db(self.db)

    def _remove(self, path, mtype):
        key = self._db_key(path)
        sec = "movies" if mtype=="movie" else "series"
        self.db.get(sec,{}).pop(key, None)
        save_db(self.db); self._refresh_library()

    def _delete_file(self, path, mtype):
        if messagebox.askyesno("Delete File",
            f"Permanently delete:\n{os.path.basename(path)}\n\nThis cannot be undone.",
            icon="warning"):
            try:
                os.remove(path); self._remove(path, mtype)
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _clear_history(self):
        if messagebox.askyesno("Clear History","Clear all watch history?"):
            self.db["history"] = []
            save_db(self.db); self._populate_history()

    # ══════════════════════════════════════════════════════════════════════════
    #  SETTINGS
    # ══════════════════════════════════════════════════════════════════════════
    def _browse_setting(self, key, var):
        if key in ("vlc_path",):
            p = filedialog.askopenfilename(
                title="Select vlc.exe",
                filetypes=[("VLC","vlc.exe *.exe *.dll"),("All","*.*")])
        else:
            p = filedialog.askdirectory(title=f"Select {key}")
        if p: var.set(p)

    def _save_settings(self):
        for k,v in self._svars.items():
            self.cfg[k] = v.get()
        save_cfg(self.cfg)
        # Reload VLC
        vlc_path = self.cfg.get("vlc_path","")
        if vlc_path:
            ok, msg = self.vlc.load(vlc_path)
            self.cfg["vlc_status"] = "" if ok else f"⚠ {msg}"
            save_cfg(self.cfg)
        self.vlc_status_lbl.configure(
            text=self._vlc_status_text(),
            fg=GREEN if self.vlc.available else ACCENT)
        self._refresh_library()
        messagebox.showinfo("Saved","Settings saved!")

    # ══════════════════════════════════════════════════════════════════════════
    #  KEYBOARD & WINDOW
    # ══════════════════════════════════════════════════════════════════════════
    def _on_key(self, e):
        k = e.keysym
        if k == "space":                    self._toggle_pause()
        elif k == "Escape":
            if self.mini_mode:              self._exit_mini()
        elif k in ("f","F"):                self._toggle_fs()
        elif k == "Right" and self.vlc:     self.vlc.seek(min(1.0,self.vlc.get_position()+0.02))
        elif k == "Left"  and self.vlc:     self.vlc.seek(max(0.0,self.vlc.get_position()-0.02))

    def _toggle_fs(self):
        self.root.attributes("-fullscreen",
            not self.root.attributes("-fullscreen"))

    def _toggle_mini(self, e=None):
        if not hasattr(self,"player_panel"): return
        self._exit_mini() if self.mini_mode else self._enter_mini()

    def _on_resize(self, e):
        if e.widget == self.root:
            self.cfg["geometry"] = self.root.geometry()

    def on_close(self):
        if self.vlc: self.vlc.stop()
        save_cfg(self.cfg); save_db(self.db)
        self.root.destroy()

# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
def launch_main():
    root = tk.Tk()
    app  = CineVault(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    splash = tk.Tk()
    SplashScreen(splash, on_done=launch_main)
    splash.mainloop()
