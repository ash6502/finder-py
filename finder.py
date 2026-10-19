#!/usr/bin/env python3
"""
finder-py - a recreation of the classic Mac OS System 7 Finder.

Written in pure Python with Tkinter (no third-party packages required).

    python3 finder.py [directory]

Press F1 inside the program for help and a list of keyboard shortcuts.
"""

from __future__ import annotations

import datetime
import json
import math
import mimetypes
import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:  # pragma: no cover - depends on the system
    sys.stderr.write(
        "finder-py needs Tkinter.\n"
        "  Debian/Ubuntu:  sudo apt install python3-tk\n"
        "  Fedora:         sudo dnf install python3-tkinter\n"
        "  macOS/Windows:  use the python.org installer (Tkinter is included)\n"
    )
    sys.exit(1)


IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC

# Modifier keys the shortcuts listen on. On macOS both Ctrl and Cmd work.
MODIFIERS = ["Control", "Command"] if IS_MAC else ["Control"]

BLACK = "#000000"
WHITE = "#ffffff"
GRAY = "#808080"
LIGHT = "#dddddd"
FOLDER_A = "#ccccff"
FOLDER_B = "#b4b4e8"
SHADOW = "#8c8cc8"

CELL_MIN_W = 112   # icon view: minimum column width
CELL_H = 64        # icon view: row height
ROW_H = 18         # list view: row height
INFO_H = 19        # header: height of the "9 items / in disk / available" row
COLS_H = 17        # header: height of the list view column titles
POLL_MS = 1500     # how often the folder is checked for outside changes

TEXT_EXTS = {
    ".txt", ".md", ".rst", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml",
    ".json", ".xml", ".csv", ".tsv", ".py", ".pyw", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".html", ".htm", ".sh", ".bash",
    ".zsh", ".fish", ".rs", ".go", ".java", ".kt", ".rb", ".pl", ".php", ".lua", ".sql",
    ".bat", ".ps1", ".tex", ".diff", ".patch", ".s", ".asm", ".swift", ".cs", ".vb",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg",
              ".ico", ".pict", ".pct", ".heic", ".avif", ".xpm", ".ppm", ".pgm"}
WIN_EXEC_EXTS = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1"}
TEXTISH_MIMES = {"application/json", "application/xml", "application/javascript",
                 "application/x-sh", "application/x-shellscript", "application/x-python",
                 "application/toml", "application/x-yaml", "application/sql"}

FONTS: dict[str, tkfont.Font] = {}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def display_name(path: str) -> str:
    """The name shown in the window title for a directory."""
    name = os.path.basename(os.path.normpath(path))
    return name or path


def split_ext(name: str) -> tuple[str, str]:
    """Split a name into stem and extension, treating dotfiles as all stem."""
    stem, ext = os.path.splitext(name)
    if not stem:
        return name, ""
    return stem, ext


def unique_name(directory: str, stem: str, ext: str = "", first_suffix: str = "") -> str:
    """Return a name in `directory` that does not exist yet ("untitled 2", ...)."""
    candidate = f"{stem}{first_suffix}{ext}"
    n = 2
    while os.path.lexists(os.path.join(directory, candidate)):
        candidate = f"{stem}{first_suffix} {n}{ext}"
        n += 1
    return candidate


def copy_name(directory: str, name: str) -> str:
    """System 7 style duplicate name: "file copy", "file copy 2", ..."""
    stem, ext = split_ext(name)
    if os.path.isdir(os.path.join(directory, name)):
        stem, ext = name, ""
    return unique_name(directory, stem, ext, " copy")


def fmt_disk(n: float) -> str:
    """Sizes as shown in the window header: 705.1 MB, 1.2 GB, 512K."""
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.1f} TB"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{math.ceil(n / 1024)}K"


def fmt_list_size(n: int) -> str:
    """Sizes as shown in list view. System 7 really did say "zero K"."""
    if n == 0:
        return "zero K"
    if n < 1024 ** 2:
        return f"{math.ceil(n / 1024)}K"
    return fmt_disk(n)


def fmt_date(ts: float) -> str:
    d = datetime.datetime.fromtimestamp(ts)
    hour = d.hour % 12 or 12
    return f"{d:%a}, {d:%b} {d.day}, {d.year}, {hour}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        if b"\0" not in chunk:
            chunk.decode("utf-8")
            return "text/plain"
    except (OSError, UnicodeDecodeError):
        pass
    return "application/octet-stream"


def is_textish(mime: str) -> bool:
    return (mime.startswith("text/") or mime in TEXTISH_MIMES
            or mime.endswith("+xml") or mime.endswith("+json"))


def spawn(argv, cwd=None, console=False):
    """Start a program detached from the Finder. Returns the Popen object."""
    kw = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL)
    if IS_WIN:
        if console:
            kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        else:
            kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(argv, **kw)


def pick_family(root, candidates, fallback):
    families = {f.lower(): f for f in tkfont.families(root)}
    for c in candidates:
        if c.lower() in families:
            return families[c.lower()]
    return fallback


def setup_fonts(root):
    chicago = pick_family(root, ["ChicagoFLF", "Chicago", "ChiKareGo2", "ChiKareGo",
                                 "Charcoal", "Chikarego"], None)
    geneva = pick_family(root, ["Geneva", "Geneva-Normal", "Monaco"], None)
    sans = pick_family(root, ["Helvetica", "Arial", "Liberation Sans", "DejaVu Sans",
                              "FreeSans", "Segoe UI"], "TkDefaultFont")
    if chicago:
        FONTS["system"] = tkfont.Font(root, family=chicago, size=-12)
        FONTS["system_u"] = tkfont.Font(root, family=chicago, size=-12, underline=True)
    else:
        FONTS["system"] = tkfont.Font(root, family=sans, size=-12, weight="bold")
        FONTS["system_u"] = tkfont.Font(root, family=sans, size=-12, weight="bold", underline=True)
    fam = geneva or sans
    FONTS["label"] = tkfont.Font(root, family=fam, size=-11)
    FONTS["label_i"] = tkfont.Font(root, family=fam, size=-11, slant="italic")
    FONTS["label_b"] = tkfont.Font(root, family=fam, size=-11, weight="bold")
    FONTS["label_u"] = tkfont.Font(root, family=fam, size=-11, underline=True)


# --------------------------------------------------------------------------
# Configuration (remembered between runs)
# --------------------------------------------------------------------------

def config_dir() -> str:
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "finder-py")


class Config:
    DEFAULTS = {
        "view": "icon",          # "icon" or "list"
        "show_hidden": False,
        "sort": "name",          # list view sort column
        "sort_reverse": False,
        "geometry": "560x380",
        "terminal": "",          # custom terminal command, e.g. "kitty"
        "open_with": {},         # extension -> last program chosen
        "positions": {},         # directory -> {name: [x, y]} for moved icons
    }

    def __init__(self):
        self.path = os.path.join(config_dir(), "config.json")
        self.data = json.loads(json.dumps(self.DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Icons (drawn pixel by pixel, in the style of System 7)
# --------------------------------------------------------------------------

def darken(color: str, factor: float = 0.5) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


class Pixmap:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[None] * w for _ in range(h)]

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = c

    def hline(self, x0, x1, y, c):
        for x in range(x0, x1 + 1):
            self.set(x, y, c)

    def vline(self, x, y0, y1, c):
        for y in range(y0, y1 + 1):
            self.set(x, y, c)

    def fill(self, x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, c(x, y) if callable(c) else c)

    def photo(self, master, dark=False):
        img = tk.PhotoImage(master=master, width=self.w, height=self.h)
        for y, row in enumerate(self.px):
            x = 0
            while x < self.w:
                if row[x] is None:
                    x += 1
                    continue
                start, run = x, []
                while x < self.w and row[x] is not None:
                    run.append(darken(row[x]) if dark else row[x])
                    x += 1
                img.put("{" + " ".join(run) + "}", to=(start, y))
        return img


def folder_fill(x, y):
    return FOLDER_A if (x + y) % 2 == 0 else FOLDER_B


def draw_folder32():
    p = Pixmap(32, 32)
    p.fill(4, 7, 11, 7, folder_fill)
    p.fill(3, 8, 12, 8, folder_fill)
    p.fill(2, 9, 29, 25, folder_fill)
    p.hline(4, 11, 7, WHITE)
    p.hline(2, 29, 13, WHITE)
    p.vline(2, 13, 24, WHITE)
    p.hline(3, 29, 25, SHADOW)
    p.vline(29, 14, 25, SHADOW)
    p.hline(4, 11, 6, BLACK)
    p.set(3, 7, BLACK)
    p.set(12, 7, BLACK)
    p.set(2, 8, BLACK)
    p.set(13, 8, BLACK)
    p.hline(14, 29, 9, BLACK)
    p.vline(1, 9, 25, BLACK)
    p.vline(30, 10, 25, BLACK)
    p.hline(2, 29, 26, BLACK)
    p.hline(2, 29, 12, BLACK)
    return p


def draw_folder16():
    p = Pixmap(16, 16)
    p.fill(2, 3, 6, 3, folder_fill)
    p.fill(2, 4, 13, 12, folder_fill)
    p.hline(2, 13, 7, WHITE)
    p.hline(2, 13, 12, SHADOW)
    p.hline(2, 6, 2, BLACK)
    p.set(1, 3, BLACK)
    p.set(7, 3, BLACK)
    p.hline(8, 13, 4, BLACK)
    p.vline(1, 4, 12, BLACK)
    p.vline(14, 5, 12, BLACK)
    p.hline(2, 13, 13, BLACK)
    p.hline(2, 13, 6, BLACK)
    return p


def draw_doc(size, kind):
    if size == 32:
        L, R, T, B, E = 6, 25, 1, 30, 7
    else:
        L, R, T, B, E = 2, 13, 0, 15, 4
    p = Pixmap(size, size)
    for y in range(T + 1, B):
        for x in range(L + 1, R):
            if x - (R - E) <= y - T:
                p.set(x, y, WHITE)
            elif x > R - E and y < T + E:
                p.set(x, y, None)
    # the folded corner
    for y in range(T + 1, T + E):
        for x in range(R - E + 1, R):
            if x - (R - E) < y - T:
                p.set(x, y, LIGHT)
    p.hline(L, R - E, T, BLACK)
    p.vline(L, T, B, BLACK)
    p.hline(L, R, B, BLACK)
    p.vline(R, T + E, B, BLACK)
    for i in range(E + 1):
        p.set(R - E + i, T + i, BLACK)
    p.vline(R - E, T, T + E, BLACK)
    p.hline(R - E, R, T + E, BLACK)

    if kind == "text":
        if size == 32:
            for y, n in zip(range(11, 28, 3), (11, 14, 9, 14, 12, 7)):
                p.hline(L + 3, L + 3 + n, y, GRAY)
        else:
            for y, n in zip((6, 8, 10, 12), (6, 8, 5, 7)):
                p.hline(L + 2, L + 2 + n, y, GRAY)
    elif kind == "image":
        if size == 32:
            x0, y0, x1, y1 = L + 3, 11, R - 3, 25
        else:
            x0, y0, x1, y1 = L + 2, 6, R - 2, 13
        p.fill(x0, y0, x1, y1, "#88bbff")
        mid = (x0 + x1) // 2
        for x in range(x0, x1 + 1):
            peak = y1 - (y1 - y0) * 0.55 + abs(x - mid + 2) * 0.7
            for y in range(int(peak), y1 + 1):
                p.set(x, y, "#339944")
        p.set(x1 - 2, y0 + 2, "#ffcc00")
        p.set(x1 - 3, y0 + 2, "#ffcc00")
        p.set(x1 - 2, y0 + 3, "#ffcc00")
        p.set(x1 - 3, y0 + 3, "#ffcc00")
        for x in range(x0, x1 + 1):
            p.set(x, y0, BLACK)
            p.set(x, y1, BLACK)
        for y in range(y0, y1 + 1):
            p.set(x0, y, BLACK)
            p.set(x1, y, BLACK)
    return p


def draw_app(size):
    p = Pixmap(size, size)
    c = size // 2 - 1
    r = c - 1 if size == 32 else c
    inner = r * 4 // 7
    for y in range(size):
        for x in range(size):
            d = abs(x - c) + abs(y - c)
            if d == r or d == inner:
                p.set(x, y, BLACK)
            elif d == r - 1 and x <= c and y <= c:
                p.set(x, y, WHITE)
            elif d < inner:
                p.set(x, y, "#9999dd")
            elif d < r:
                p.set(x, y, folder_fill(x, y))
    return p


class Icons:
    KINDS = ("folder", "doc", "text", "image", "app")

    def __init__(self, master):
        self.images = {}
        for size in (32, 16):
            for kind in self.KINDS:
                if kind == "folder":
                    pm = draw_folder32() if size == 32 else draw_folder16()
                elif kind == "app":
                    pm = draw_app(size)
                else:
                    pm = draw_doc(size, kind)
                self.images[(kind, size, False)] = pm.photo(master)
                self.images[(kind, size, True)] = pm.photo(master, dark=True)

    def get(self, kind, size=32, selected=False):
        return self.images[(kind, size, selected)]


# --------------------------------------------------------------------------
# Directory items
# --------------------------------------------------------------------------

class Item:
    __slots__ = ("name", "path", "is_dir", "is_link", "size", "mtime", "ctime",
                 "icon", "kind", "executable")

    def __init__(self, directory, entry):
        self.name = entry.name
        self.path = os.path.join(directory, entry.name)
        try:
            self.is_dir = entry.is_dir()
        except OSError:
            self.is_dir = False
        try:
            self.is_link = entry.is_symlink()
        except OSError:
            self.is_link = False
        st = None
        try:
            st = entry.stat()
        except OSError:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                pass
        self.size = st.st_size if st and not self.is_dir else 0
        self.mtime = st.st_mtime if st else 0
        self.ctime = getattr(st, "st_birthtime", None) or (st.st_ctime if st else 0)
        ext = os.path.splitext(self.name)[1].lower()
        if IS_WIN:
            self.executable = ext in WIN_EXEC_EXTS
        else:
            self.executable = (not self.is_dir and st is not None
                               and stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o111))
        mime = mimetypes.guess_type(self.name)[0] or ""
        if self.is_dir:
            self.icon, self.kind = "folder", "folder"
        elif self.executable:
            self.icon, self.kind = "app", "application program"
        elif ext in IMAGE_EXTS or mime.startswith("image/"):
            self.icon, self.kind = "image", f"{ext[1:].upper()} image" if ext else "image"
        elif ext in TEXT_EXTS or (mime and is_textish(mime)):
            self.icon, self.kind = "text", f"{ext[1:].upper()} text document" if ext else "text document"
        else:
            self.icon, self.kind = "doc", f"{ext[1:].upper()} document" if ext else "document"
        if self.is_link:
            self.kind = "alias"


def is_hidden(entry) -> bool:
    if entry.name.startswith("."):
        return True
    if IS_WIN:
        try:
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & 2)
        except (OSError, AttributeError):
            return False
    return False


# --------------------------------------------------------------------------
# Programs: "Open With", terminals and the Trash
# --------------------------------------------------------------------------

# name -> (arguments that set the working directory, arguments that run a command)
TERMINALS = {
    "gnome-terminal": (lambda d: [f"--working-directory={d}"], lambda c: ["--"] + c),
    "kgx": (lambda d: [f"--working-directory={d}"], lambda c: ["--"] + c),
    "ptyxis": (lambda d: [f"--working-directory={d}"], lambda c: ["--"] + c),
    "konsole": (lambda d: ["--workdir", d], lambda c: ["-e"] + c),
    "xfce4-terminal": (lambda d: [f"--working-directory={d}"], lambda c: ["-x"] + c),
    "mate-terminal": (lambda d: [f"--working-directory={d}"], lambda c: ["-x"] + c),
    "tilix": (lambda d: [f"--working-directory={d}"], lambda c: ["-e", shlex.join(c)]),
    "terminator": (lambda d: [f"--working-directory={d}"], lambda c: ["-x"] + c),
    "kitty": (lambda d: ["--directory", d], lambda c: c),
    "alacritty": (lambda d: ["--working-directory", d], lambda c: ["-e"] + c),
    "wezterm": (lambda d: ["start", "--cwd", d], lambda c: ["--"] + c),
    "foot": (lambda d: [f"--working-directory={d}"], lambda c: c),
    "lxterminal": (lambda d: [f"--working-directory={d}"], lambda c: ["-e", shlex.join(c)]),
    "x-terminal-emulator": (lambda d: [], lambda c: ["-e"] + c),
    "urxvt": (lambda d: [], lambda c: ["-e"] + c),
    "st": (lambda d: [], lambda c: ["-e"] + c),
    "xterm": (lambda d: [], lambda c: ["-e"] + c),
}
TERMINAL_ORDER = ["x-terminal-emulator", "gnome-terminal", "kgx", "ptyxis", "konsole",
                  "xfce4-terminal", "mate-terminal", "tilix", "terminator", "kitty",
                  "alacritty", "wezterm", "foot", "lxterminal", "urxvt", "st", "xterm"]


class Launcher:
    def __init__(self, config: Config):
        self.config = config
        self._apps = None
        self.children = []

    # ----- discovering programs -----
    def apps(self):
        if self._apps is None:
            try:
                if IS_MAC:
                    self._apps = self._mac_apps()
                elif IS_WIN:
                    self._apps = self._windows_apps()
                else:
                    self._apps = self._desktop_apps()
            except Exception:
                self._apps = []
        return self._apps

    def _desktop_apps(self):
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        dirs = [data_home] + data_dirs + ["/var/lib/flatpak/exports/share",
                                          os.path.expanduser("~/.local/share/flatpak/exports/share"),
                                          "/var/lib/snapd/desktop"]
        seen, apps = set(), []
        for base in dirs:
            appdir = os.path.join(base, "applications")
            if not os.path.isdir(appdir):
                continue
            for dirpath, _dirs, files in os.walk(appdir):
                for fn in sorted(files):
                    if not fn.endswith(".desktop"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, fn), appdir).replace(os.sep, "-")
                    if rel in seen:
                        continue
                    seen.add(rel)
                    entry = self._parse_desktop(os.path.join(dirpath, fn))
                    if entry:
                        apps.append(entry)
        apps.sort(key=lambda a: a["name"].lower())
        return apps

    @staticmethod
    def _parse_desktop(path):
        values, section = {}, None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("["):
                        section = line
                        continue
                    if section == "[Desktop Entry]" and "=" in line:
                        k, v = line.split("=", 1)
                        values.setdefault(k.strip(), v.strip())
        except OSError:
            return None
        if values.get("Type") != "Application" or "Exec" not in values or "Name" not in values:
            return None
        if values.get("NoDisplay", "").lower() == "true" or values.get("Hidden", "").lower() == "true":
            return None
        try_exec = values.get("TryExec")
        if try_exec and not shutil.which(try_exec):
            return None
        return {
            "type": "desktop",
            "name": values["Name"],
            "exec": values["Exec"],
            "terminal": values.get("Terminal", "").lower() == "true",
            "file": path,
            "mimes": [m for m in values.get("MimeType", "").split(";") if m],
        }

    @staticmethod
    def _mac_apps():
        apps = []
        for base in ("/Applications", "/Applications/Utilities", "/System/Applications",
                     "/System/Applications/Utilities", os.path.expanduser("~/Applications")):
            try:
                names = os.listdir(base)
            except OSError:
                continue
            for n in names:
                if n.endswith(".app"):
                    apps.append({"type": "macapp", "name": n[:-4], "path": os.path.join(base, n),
                                 "mimes": ["text/plain"] if n == "TextEdit.app" else []})
        apps.sort(key=lambda a: a["name"].lower())
        return apps

    @staticmethod
    def _windows_apps():
        apps = [
            {"type": "argv", "name": "Notepad", "argv": ["notepad.exe"], "mimes": ["text/plain"]},
            {"type": "argv", "name": "Paint", "argv": ["mspaint.exe"], "mimes": ["image/*"]},
            {"type": "argv", "name": "WordPad", "argv": ["write.exe"], "mimes": ["text/rtf"]},
        ]
        for name, exe in (("Visual Studio Code", "code"), ("Notepad++", "notepad++")):
            if shutil.which(exe):
                apps.append({"type": "argv", "name": name, "argv": [shutil.which(exe)],
                             "mimes": ["text/plain"]})
        return apps

    def split(self, mime):
        """Programs that handle `mime`, and all the others."""
        major = mime.split("/")[0] + "/*"
        textish = is_textish(mime)
        rec, others = [], []
        for app in self.apps():
            mimes = app.get("mimes", [])
            if mime in mimes or major in mimes or (textish and "text/plain" in mimes):
                rec.append(app)
            else:
                others.append(app)
        return rec, others

    # ----- launching -----
    def _track(self, proc):
        self.children.append(proc)

    def reap(self):
        self.children = [p for p in self.children if p.poll() is None]

    def default_open(self, path):
        if IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
        elif IS_MAC:
            self._track(spawn(["open", path]))
        else:
            for tool in (["xdg-open"], ["gio", "open"], ["kde-open"], ["exo-open"]):
                if shutil.which(tool[0]):
                    self._track(spawn(tool + [path], cwd=os.path.dirname(path)))
                    return
            raise OSError("No default application handler (xdg-open) was found.")

    @staticmethod
    def expand_exec(exec_str, path, name, desktop_file):
        try:
            parts = shlex.split(exec_str)
        except ValueError:
            parts = exec_str.split()
        out, used = [], False
        for part in parts:
            if part in ("%f", "%F", "%u", "%U"):
                out.append(path)
                used = True
                continue
            if part == "%c":
                out.append(name)
                continue
            if part == "%k":
                out.append(desktop_file)
                continue
            q = part.replace("%%", "\0")
            for code in ("%f", "%F", "%u", "%U"):
                if code in q:
                    q = q.replace(code, path)
                    used = True
            for code in ("%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"):
                q = q.replace(code, "")
            q = q.replace("\0", "%")
            if q:
                out.append(q)
        if not used:
            out.append(path)
        return out

    @staticmethod
    def command_argv(cmd, path):
        posix = not IS_WIN
        if "{}" in cmd:
            return shlex.split(cmd.replace("{}", shlex.quote(path) if posix else f'"{path}"'), posix=posix)
        return shlex.split(cmd, posix=posix) + [path]

    def launch(self, choice, paths):
        kind = choice.get("type")
        for path in paths:
            cwd = os.path.dirname(path)
            if kind == "default":
                self.default_open(path)
            elif kind == "run":
                self._track(spawn([path], cwd=cwd, console=IS_WIN and path.lower().endswith((".bat", ".cmd"))))
            elif kind == "desktop":
                argv = self.expand_exec(choice["exec"], path, choice["name"], choice.get("file", ""))
                if choice.get("terminal"):
                    self.open_terminal(cwd, argv)
                else:
                    self._track(spawn(argv, cwd=cwd))
            elif kind == "macapp":
                self._track(spawn(["open", "-a", choice["path"], path]))
            elif kind == "argv":
                self._track(spawn(list(choice["argv"]) + [path], cwd=cwd))
            elif kind == "command":
                self._track(spawn(self.command_argv(choice["cmd"], path), cwd=cwd))
            else:
                raise OSError(f"Unknown program type {kind!r}")

    def find_terminal(self):
        custom = (self.config["terminal"] or "").strip()
        if custom:
            return custom
        env = os.environ.get("TERMINAL", "").strip()
        if env and shutil.which(shlex.split(env)[0]):
            return env
        for name in TERMINAL_ORDER:
            if shutil.which(name):
                return name
        return None

    def open_terminal(self, directory, command=None):
        if IS_WIN:
            if command is None and shutil.which("wt"):
                self._track(spawn(["wt", "-d", directory], cwd=directory))
            else:
                argv = ["cmd.exe"] + (["/K"] + command if command else [])
                self._track(spawn(argv, cwd=directory, console=True))
            return
        if IS_MAC and not self.config["terminal"]:
            if command:
                script = f'tell application "Terminal" to do script "cd {shlex.quote(directory)}; {shlex.join(command)}"'
                self._track(spawn(["osascript", "-e", script]))
            else:
                self._track(spawn(["open", "-a", "Terminal", directory]))
            return
        term = self.find_terminal()
        if not term:
            raise OSError("No terminal emulator was found. Set one with the TERMINAL "
                          "environment variable or the \"terminal\" key in "
                          f"{self.config.path}.")
        argv = shlex.split(term)
        base = os.path.basename(argv[0])
        dir_args, exec_args = TERMINALS.get(base, (lambda d: [], lambda c: ["-e"] + c))
        argv += dir_args(directory)
        if command:
            argv += exec_args(command)
        self._track(spawn(argv, cwd=directory))


def trash_dir():
    if IS_MAC:
        return os.path.expanduser("~/.Trash")
    if IS_WIN:
        return None
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Trash")


def move_to_trash(path):
    """Move `path` to the Trash. Returns False if no Trash is available."""
    if IS_WIN:
        try:
            from send2trash import send2trash  # optional package
        except ImportError:
            return False
        send2trash(path)
        return True
    root = trash_dir()
    if IS_MAC:
        os.makedirs(root, exist_ok=True)
        name = os.path.basename(path)
        stem, ext = split_ext(name)
        dest = name if not os.path.lexists(os.path.join(root, name)) else unique_name(root, stem, ext)
        shutil.move(path, os.path.join(root, dest))
        return True
    files, info = os.path.join(root, "files"), os.path.join(root, "info")
    os.makedirs(files, exist_ok=True)
    os.makedirs(info, exist_ok=True)
    name = os.path.basename(path)
    candidate, n = name, 2
    while True:
        info_path = os.path.join(info, candidate + ".trashinfo")
        if not os.path.lexists(os.path.join(files, candidate)):
            try:
                fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                pass
        stem, ext = split_ext(name)
        candidate = f"{stem}.{n}{ext}"
        n += 1
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("[Trash Info]\n")
        f.write(f"Path={urllib.parse.quote(os.path.abspath(path))}\n")
        f.write(f"DeletionDate={datetime.datetime.now():%Y-%m-%dT%H:%M:%S}\n")
    try:
        shutil.move(path, os.path.join(files, candidate))
    except BaseException:
        os.remove(info_path)
        raise
    return True


def trash_contents():
    root = trash_dir()
    if not root:
        return None, []
    files = root if IS_MAC else os.path.join(root, "files")
    try:
        return root, [os.path.join(files, n) for n in os.listdir(files)]
    except OSError:
        return root, []


def remove_path(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


# --------------------------------------------------------------------------
# Widgets drawn in the System 7 style
# --------------------------------------------------------------------------

def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


def is_x11(widget):
    try:
        return widget.tk.call("tk", "windowingsystem") == "x11"
    except tk.TclError:
        return False


class MacScrollbar(tk.Canvas):
    """A scroll bar with arrow boxes, a patterned track and a square thumb."""
    S = 16

    def __init__(self, master, orient, command=None):
        self.vertical = orient == "vertical"
        size = dict(width=self.S, height=40) if self.vertical else dict(width=40, height=self.S)
        super().__init__(master, bg=WHITE, highlightthickness=0, bd=0, **size)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.pressed = None
        self.repeat_job = None
        self.drag_offset = None
        self.stipple = is_x11(self)
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_motion)
        self.bind("<ButtonRelease-1>", self.on_release)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.redraw()

    @property
    def active(self):
        return (self.last - self.first) < 0.9999

    def length(self):
        return self.winfo_height() if self.vertical else self.winfo_width()

    def _xy(self, a, c):
        """Map (along, across) to canvas (x, y)."""
        return (c, a) if self.vertical else (a, c)

    def _fill(self, a0, a1, fill, **kw):
        x0, y0 = self._xy(a0, 0)
        x1, y1 = self._xy(a1 + 1, self.S)
        if self.vertical:
            x0 = 1
        else:
            y0 = 1
        self.create_rectangle(x0, y0, x1, y1, outline="", fill=fill, **kw)

    def _cross_line(self, a):
        x0, y0 = self._xy(a, 0)
        x1, y1 = self._xy(a, self.S)
        self.create_line(x0, y0, x1, y1, fill=BLACK)

    def thumb_pos(self):
        L, S = self.length(), self.S
        span = max(1e-9, 1.0 - (self.last - self.first))
        frac = min(1.0, max(0.0, self.first / span))
        return int(S + frac * (L - 3 * S))

    def redraw(self):
        self.delete("all")
        L, S = self.length(), self.S
        if L < 2:
            return
        if self.active and L > 3 * S:
            self._fill(S, L - S - 1, WHITE)
            if self.stipple:
                self._fill(S, L - S - 1, GRAY, stipple="gray50")
            else:
                self._fill(S, L - S - 1, "#bbbbbb")
        # the edge next to the window contents
        if self.vertical:
            self.create_line(0, 0, 0, L, fill=BLACK)
        else:
            self.create_line(0, 0, L, 0, fill=BLACK)
        for which, a0 in (("dec", 0), ("inc", L - S)):
            self._fill(a0, a0 + S - 1, WHITE)
            self._draw_arrow(which, a0)
        self._cross_line(S - 1)
        self._cross_line(L - S)
        self._cross_line(L - 1)
        if self.active and L > 3 * S:
            t = self.thumb_pos()
            self._fill(t, t + S - 1, WHITE)
            self._cross_line(t)
            self._cross_line(t + S - 1)

    def _draw_arrow(self, which, a0):
        shape = [(8, 3), (14, 9), (11, 9), (11, 13), (5, 13), (5, 9), (2, 9)]
        pts = []
        for c, a in shape:
            if which == "inc":
                a = 15 - a
            pts.extend(self._xy(a0 + a, c))
        fill = BLACK if self.pressed == which else WHITE
        self.create_polygon(pts, outline=BLACK, fill=fill)

    def _act(self, what):
        if not self.command:
            return
        if what == "dec":
            self.command("scroll", -1, "units")
        elif what == "inc":
            self.command("scroll", 1, "units")
        elif what == "pgup":
            self.command("scroll", -1, "pages")
        elif what == "pgdn":
            self.command("scroll", 1, "pages")

    def _repeat(self, what, delay):
        self._act(what)
        self.repeat_job = self.after(delay, self._repeat, what, 50)

    def on_press(self, e):
        a = e.y if self.vertical else e.x
        L, S = self.length(), self.S
        if a < S:
            what = "dec"
        elif a >= L - S:
            what = "inc"
        elif not self.active:
            return
        else:
            t = self.thumb_pos()
            if t <= a < t + S:
                self.drag_offset = a - t
                return
            what = "pgup" if a < t else "pgdn"
        self.pressed = what if what in ("dec", "inc") else None
        self.redraw()
        self._repeat(what, 350)

    def on_motion(self, e):
        if self.drag_offset is None or not self.command:
            return
        a = (e.y if self.vertical else e.x) - self.drag_offset
        L, S = self.length(), self.S
        rng = max(1, L - 3 * S)
        frac = min(1.0, max(0.0, (a - S) / rng))
        self.command("moveto", frac * (1.0 - (self.last - self.first)))

    def on_release(self, _e):
        if self.repeat_job:
            self.after_cancel(self.repeat_job)
            self.repeat_job = None
        self.drag_offset = None
        if self.pressed:
            self.pressed = None
            self.redraw()


class GrowBox(tk.Canvas):
    """The size box in the bottom-right corner of the window."""

    def __init__(self, master, window):
        super().__init__(master, width=16, height=16, bg=WHITE, highlightthickness=0, bd=0,
                         cursor="bottom_right_corner" if not IS_MAC else "")
        self.window = window
        self.start = None
        self.create_rectangle(6, 6, 14, 14, outline=BLACK, fill=WHITE)
        self.create_rectangle(3, 3, 10, 10, outline=BLACK, fill=WHITE)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_motion)

    def on_press(self, e):
        self.start = (e.x_root, e.y_root, self.window.winfo_width(), self.window.winfo_height())

    def on_motion(self, e):
        if not self.start:
            return
        x, y, w, h = self.start
        nw = max(260, w + e.x_root - x)
        nh = max(160, h + e.y_root - y)
        self.window.geometry(f"{nw}x{nh}")


class MacButton(tk.Canvas):
    """A rounded push button. The default button gets the thick outer ring."""

    def __init__(self, master, text, command, default=False, minwidth=60):
        font = FONTS["system"]
        self.w = max(minwidth, font.measure(text) + 22)
        self.h = 20
        self.pad = 4 if default else 0
        super().__init__(master, width=self.w + 2 * self.pad + 2, height=self.h + 2 * self.pad + 2,
                         bg=WHITE, highlightthickness=0, bd=0)
        self.text, self.command, self.default = text, command, default
        self.pressed = False
        self.draw()
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_motion)
        self.bind("<ButtonRelease-1>", self.on_release)

    def draw(self):
        self.delete("all")
        p = self.pad
        if self.default:
            round_rect(self, 2, 2, self.w + 2 * p, self.h + 2 * p, 11, outline=BLACK, width=3, fill="")
        round_rect(self, p + 1, p + 1, p + self.w, p + self.h, 8, outline=BLACK,
                   fill=BLACK if self.pressed else WHITE)
        self.create_text(p + 1 + self.w / 2, p + 1 + self.h / 2, text=self.text, font=FONTS["system"],
                         fill=WHITE if self.pressed else BLACK)

    def _inside(self, e):
        return 0 <= e.x < int(self["width"]) and 0 <= e.y < int(self["height"])

    def on_press(self, _e):
        self.pressed = True
        self.draw()

    def on_motion(self, e):
        inside = self._inside(e)
        if inside != self.pressed:
            self.pressed = inside
            self.draw()

    def on_release(self, e):
        was = self.pressed and self._inside(e)
        self.pressed = False
        self.draw()
        if was:
            self.command()

    def invoke(self):
        """Flash the button like a click, then run its command."""
        self.pressed = True
        self.draw()
        self.update_idletasks()
        self.after(90, self._finish_invoke)

    def _finish_invoke(self):
        self.pressed = False
        try:
            self.draw()
        except tk.TclError:
            pass
        self.command()


def draw_alert_icon(canvas, kind):
    if kind == "stop":
        pts = [10, 1, 22, 1, 31, 10, 31, 22, 22, 31, 10, 31, 1, 22, 1, 10]
        canvas.create_polygon(pts, outline=BLACK, fill=WHITE, width=2)
        canvas.create_text(16, 16, text="!", font=("Helvetica", 18, "bold"), fill=BLACK)
    elif kind == "note":
        round_rect(canvas, 2, 2, 31, 24, 8, outline=BLACK, fill=WHITE, width=2)
        canvas.create_polygon(8, 22, 8, 31, 15, 23, outline=BLACK, fill=WHITE, width=2)
        canvas.create_line(9, 23, 14, 23, fill=WHITE, width=2)
        canvas.create_text(16, 13, text="i", font=("Times", 15, "bold italic"), fill=BLACK)
    else:  # caution
        canvas.create_polygon(16, 2, 31, 30, 1, 30, outline=BLACK, fill=WHITE, width=2)
        canvas.create_text(16, 19, text="!", font=("Helvetica", 16, "bold"), fill=BLACK)


def double_rule(parent):
    c = tk.Canvas(parent, height=3, bg=WHITE, highlightthickness=0, bd=0)
    c.bind("<Configure>", lambda e: (c.delete("all"),
                                     c.create_line(0, 0, e.width, 0, fill=BLACK),
                                     c.create_line(0, 2, e.width, 2, fill=BLACK)))
    return c


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------

class MacDialog(tk.Toplevel):
    """A modal dialog with the System 7 double border."""

    def __init__(self, app, title="Finder"):
        super().__init__(app.root, bg=BLACK)
        self.app = app
        self.result = None
        self.cancel_value = None
        self.default_button = None
        self.cancel_button = None
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(app.root)
        ring = tk.Frame(self, bg=WHITE)
        ring.pack(fill="both", expand=True, padx=1, pady=1)
        ring2 = tk.Frame(ring, bg=BLACK)
        ring2.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(ring2, bg=WHITE, padx=14, pady=12)
        self.body.pack(fill="both", expand=True, padx=3, pady=3)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", self._on_return)
        self.bind("<KP_Enter>", self._on_return)

    def _on_return(self, _e=None):
        if self.default_button:
            self.default_button.invoke()
        return "break"

    def button_row(self, specs, parent=None):
        """specs: (label, value, is_default) tuples, left to right."""
        row = tk.Frame(parent or self.body, bg=WHITE)
        for label, value, is_default in specs:
            b = MacButton(row, label, lambda v=value: self.done(v), default=is_default)
            b.pack(side="left", padx=(10, 0), anchor="s")
            if is_default:
                self.default_button = b
        return row

    def done(self, value):
        self.result = value
        self.close()

    def cancel(self):
        self.result = self.cancel_value
        self.close()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self, focus=None):
        self.update_idletasks()
        root = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            x = root.winfo_rootx() + (root.winfo_width() - w) // 2
            y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
        except tk.TclError:
            x, y = 100, 100
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.lift()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        (focus or self).focus_force()
        root.wait_window(self)
        return self.result


def alert(app, message, icon="caution", buttons=(("OK", True, True),), cancel_value=None,
          title="Finder"):
    d = MacDialog(app, title)
    d.cancel_value = cancel_value
    row = tk.Frame(d.body, bg=WHITE)
    row.pack(fill="x")
    ic = tk.Canvas(row, width=34, height=34, bg=WHITE, highlightthickness=0)
    draw_alert_icon(ic, icon)
    ic.pack(side="left", anchor="n")
    tk.Label(row, text=message, font=FONTS["system"], bg=WHITE, fg=BLACK, justify="left",
             wraplength=320, anchor="w").pack(side="left", padx=(14, 0), fill="x")
    d.button_row(buttons).pack(side="bottom", anchor="e", pady=(16, 0))
    return d.show()


def mac_entry(parent, var, width=40):
    return tk.Entry(parent, textvariable=var, width=width, font=FONTS["label"], relief="flat", bd=2,
                    bg=WHITE, fg=BLACK, highlightthickness=1, highlightbackground=BLACK,
                    highlightcolor=BLACK, insertbackground=BLACK, selectbackground=BLACK,
                    selectforeground=WHITE)


def ask_string(app, prompt, initial="", ok="OK", title="Finder"):
    d = MacDialog(app, title)
    tk.Label(d.body, text=prompt, font=FONTS["system"], bg=WHITE, anchor="w",
             justify="left").pack(fill="x")
    var = tk.StringVar(value=initial)
    entry = mac_entry(d.body, var, 44)
    entry.pack(fill="x", pady=(8, 0))
    entry.select_range(0, "end")
    entry.icursor("end")
    d.button_row([("Cancel", None, False), (ok, "ok", True)]).pack(anchor="e", pady=(14, 0))
    if d.show(focus=entry) == "ok":
        return var.get()
    return None


def choice_key(choice):
    if not choice:
        return None
    return (choice.get("type"), choice.get("name"), choice.get("cmd") or choice.get("exec")
            or choice.get("path"))


class OpenWithDialog(MacDialog):
    """Asks which program should open one or more files."""

    def __init__(self, app, files):
        super().__init__(app, "Open With")
        self.files = files
        first = files[0]
        mime = guess_mime(first.path)
        what = f"“{first.name}”" if len(files) == 1 else f"{len(files)} items"

        top = tk.Frame(self.body, bg=WHITE)
        top.pack(fill="x")
        tk.Label(top, image=app.icons.get(first.icon), bg=WHITE).pack(side="left")
        tk.Label(top, text=f"Open {what} with:", font=FONTS["system"], bg=WHITE,
                 anchor="w", justify="left", wraplength=300).pack(side="left", padx=(10, 0))

        self.entries = [("Default Application", {"type": "default", "name": "Default Application"})]
        if any(f.executable for f in files):
            self.entries.append(("Run as Program", {"type": "run", "name": "Run as Program"}))
        rec, others = app.launcher.split(mime)
        for a in rec:
            self.entries.append((a["name"], a))
        if others:
            self.entries.append(("──── All Applications ────", None))
            for a in others:
                self.entries.append((a["name"], a))

        box = tk.Frame(self.body, bg=BLACK)
        box.pack(fill="both", expand=True, pady=(10, 0))
        inner = tk.Frame(box, bg=WHITE)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.listbox = tk.Listbox(inner, height=10, width=40, font=FONTS["label"], bg=WHITE, fg=BLACK,
                                  bd=0, highlightthickness=0, relief="flat", activestyle="none",
                                  selectbackground=BLACK, selectforeground=WHITE,
                                  exportselection=False)
        sb = MacScrollbar(inner, "vertical", self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=2)
        for i, (label, choice) in enumerate(self.entries):
            self.listbox.insert("end", label)
            if choice is None:
                self.listbox.itemconfigure(i, foreground=GRAY, selectbackground=WHITE,
                                           selectforeground=GRAY)

        remembered = choice_key(app.config["open_with"].get(app.ext_key(first.name)))
        start = 0
        for i, (_label, choice) in enumerate(self.entries):
            if choice is not None and choice_key(choice) == remembered:
                start = i
        self.listbox.selection_set(start)
        self.listbox.see(start)
        self.listbox.bind("<<ListboxSelect>>", self._skip_separator)
        self.listbox.bind("<Double-Button-1>", lambda e: self.choose())

        row = tk.Frame(self.body, bg=WHITE)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="Command:", font=FONTS["system"], bg=WHITE).pack(side="left")
        self.cmd_var = tk.StringVar()
        if remembered and remembered[0] == "command":
            self.cmd_var.set(remembered[2])
        self.cmd_entry = mac_entry(row, self.cmd_var, 30)
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        tk.Label(self.body, text="Leave the command empty to use the selected program.\n"
                                 "In a command, {} is replaced by the file's path.",
                 font=FONTS["label"], bg=WHITE, fg=GRAY, justify="left", anchor="w").pack(fill="x", pady=(4, 0))
        self.button_row([("Cancel", None, False), ("Open", "open", True)]).pack(anchor="e", pady=(12, 0))
        self.default_button.command = self.choose

    def _skip_separator(self, _e=None):
        sel = self.listbox.curselection()
        if sel and self.entries[sel[0]][1] is None:
            i = min(sel[0] + 1, len(self.entries) - 1)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(i)

    def choose(self):
        cmd = self.cmd_var.get().strip()
        if cmd:
            self.done({"type": "command", "name": cmd, "cmd": cmd})
            return
        sel = self.listbox.curselection()
        if not sel or self.entries[sel[0]][1] is None:
            self.bell()
            return
        self.done(self.entries[sel[0]][1])

    def show(self, focus=None):
        return super().show(focus=self.listbox)


HELP_TEXT = [
    ("h", "Using the Finder"),
    ("p", "The window shows the contents of one folder; its name is shown in the "
          "window's title. Click an item to select it. Ctrl-click (or Shift-click) adds "
          "or removes items from the selection, and dragging across empty space selects "
          "everything inside the dotted rectangle."),
    ("p", "Double-click a folder to open it. Double-click a file and you will be asked "
          "which program should open it. The program you pick is remembered and "
          "pre-selected next time. You can also type any command line; {} stands for "
          "the file."),
    ("p", "Drag icons to arrange them in the window, or drop them onto a folder to "
          "move them into it. Special ▸ Clean Up Window puts every icon back on the grid."),
    ("p", "Right-click an item or the window background for a menu of common commands. "
          "The box in the bottom-right corner resizes the window."),
    ("h", "Files and folders"),
    ("k", "Ctrl+N", "New file (then type its name)"),
    ("k", "Ctrl+Shift+N", "New folder (then type its name)"),
    ("k", "F2", "Rename the selected item"),
    ("k", "Ctrl+D", "Duplicate"),
    ("k", "Ctrl+C / Ctrl+X", "Copy / Cut"),
    ("k", "Ctrl+V", "Paste into this folder"),
    ("k", "Delete", "Move to Trash"),
    ("k", "Shift+Delete", "Delete permanently (asks first)"),
    ("k", "Ctrl+I", "Get Info"),
    ("h", "Opening"),
    ("k", "Double-click", "Open (files ask for a program)"),
    ("k", "Return / Ctrl+O", "Open the selection"),
    ("k", "Ctrl+T", "Open this folder in a terminal"),
    ("h", "Moving around"),
    ("k", "Arrow keys", "Select the next item"),
    ("k", "Type a name", "Select the item starting with it"),
    ("k", "Backspace", "Go to the enclosing folder"),
    ("k", "Ctrl+Up / Ctrl+Down", "Enclosing folder / open selection"),
    ("k", "Alt+Left / Alt+Right", "Back / Forward"),
    ("k", "Ctrl+L", "Go to a folder by typing its path"),
    ("k", "Ctrl+Shift+H", "Go to your home folder"),
    ("h", "Window"),
    ("k", "Ctrl+1 / Ctrl+2", "View by Icon / by Name"),
    ("k", "Ctrl+H", "Show or hide hidden files"),
    ("k", "Ctrl+A", "Select all"),
    ("k", "Escape", "Deselect all"),
    ("k", "F5 / Ctrl+R", "Refresh"),
    ("k", "F1", "This help"),
    ("k", "Ctrl+W / Ctrl+Q", "Quit"),
    ("h", "While renaming"),
    ("k", "Return", "Keep the new name"),
    ("k", "Escape", "Keep the old name"),
    ("p", ""),
    ("p", "On macOS the Cmd key works wherever Ctrl is listed. Settings are kept in "
          "{config}."),
]


def show_help(app):
    d = MacDialog(app, "Finder Help")
    top = tk.Frame(d.body, bg=WHITE)
    top.pack(fill="x")
    tk.Label(top, image=app.icons.get("folder"), bg=WHITE).pack(side="left")
    tk.Label(top, text="Finder Help", font=FONTS["system"], bg=WHITE).pack(side="left", padx=(10, 0))
    double_rule(d.body).pack(fill="x", pady=(8, 8))

    box = tk.Frame(d.body, bg=BLACK)
    box.pack(fill="both", expand=True)
    inner = tk.Frame(box, bg=WHITE)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    text = tk.Text(inner, width=62, height=22, wrap="word", font=FONTS["label"], bg=WHITE, fg=BLACK,
                   bd=0, highlightthickness=0, padx=8, pady=6, cursor="arrow",
                   tabs=(tkfont.Font(font=FONTS["label_b"]).measure("Alt+Left / Alt+Right") + 24,))
    sb = MacScrollbar(inner, "vertical", text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.tag_configure("h", font=FONTS["system"], spacing1=10, spacing3=4)
    text.tag_configure("k", font=FONTS["label_b"])
    text.tag_configure("p", spacing3=6)
    for entry in HELP_TEXT:
        if entry[0] == "h":
            text.insert("end", entry[1] + "\n", "h")
        elif entry[0] == "k":
            text.insert("end", entry[1], "k")
            text.insert("end", "\t" + entry[2] + "\n")
        else:
            text.insert("end", entry[1].replace("{config}", app.config.path) + "\n", "p")
    text.configure(state="disabled")
    text.bind("<MouseWheel>", lambda e: text.yview_scroll(-1 if e.delta > 0 else 1, "units"))
    d.button_row([("OK", True, True)]).pack(anchor="e", pady=(12, 0))
    d.bind("<F1>", lambda e: d.cancel())
    d.show()


class InfoWindow(tk.Toplevel):
    """The Get Info window."""

    def __init__(self, app, item):
        super().__init__(app.root, bg=WHITE)
        self.title(f"{item.name} Info")
        self.resizable(False, False)
        self.cancelled = False
        body = tk.Frame(self, bg=WHITE, padx=14, pady=10)
        body.pack(fill="both", expand=True)
        top = tk.Frame(body, bg=WHITE)
        top.pack(fill="x")
        tk.Label(top, image=app.icons.get(item.icon), bg=WHITE).pack(side="left")
        tk.Label(top, text=item.name, font=FONTS["system"], bg=WHITE, wraplength=280,
                 justify="left").pack(side="left", padx=(10, 0))
        double_rule(body).pack(fill="x", pady=(8, 8))
        grid = tk.Frame(body, bg=WHITE)
        grid.pack(fill="x")

        try:
            st = os.lstat(item.path) if item.is_link else os.stat(item.path)
        except OSError:
            st = None
        self.size_var = tk.StringVar(value="Calculating…" if item.is_dir else
                                     f"{fmt_disk(item.size)} ({item.size:,} bytes)")
        rows = [("Kind:", item.kind), ("Size:", self.size_var),
                ("Where:", os.path.dirname(item.path))]
        if st:
            created = getattr(st, "st_birthtime", None)
            if created:
                rows.append(("Created:", fmt_date(created)))
            rows.append(("Modified:", fmt_date(st.st_mtime)))
            if not IS_WIN:
                rows.append(("Permissions:", stat.filemode(st.st_mode)))
        if item.is_link:
            try:
                rows.append(("Original:", os.readlink(item.path)))
            except OSError:
                pass
        for r, (label, value) in enumerate(rows):
            tk.Label(grid, text=label, font=FONTS["label_b"], bg=WHITE, anchor="e").grid(
                row=r, column=0, sticky="ne", pady=1)
            kw = {"textvariable": value} if isinstance(value, tk.StringVar) else {"text": value}
            tk.Label(grid, font=FONTS["label"], bg=WHITE, anchor="w", justify="left",
                     wraplength=280, **kw).grid(row=r, column=1, sticky="nw", padx=(8, 0), pady=1)
        self.bind("<Escape>", lambda e: self.close())
        for mod in MODIFIERS:
            self.bind(f"<{mod}-w>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(app.root)
        self.geometry(f"+{app.root.winfo_rootx() + 40}+{app.root.winfo_rooty() + 40}")

        if item.is_dir:
            self.result = None
            threading.Thread(target=self._measure, args=(item.path,), daemon=True).start()
            self.after(150, self._check)

    def _measure(self, path):
        total, count, stack = 0, 0, [path]
        while stack and not self.cancelled:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        count += 1
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except OSError:
                pass
        self.result = (total, count)

    def _check(self):
        if self.cancelled:
            return
        if self.result is None:
            self.after(150, self._check)
            return
        total, count = self.result
        self.size_var.set(f"{fmt_disk(total)} ({total:,} bytes), for {count:,} item"
                          + ("" if count == 1 else "s"))

    def close(self):
        self.cancelled = True
        self.destroy()


# --------------------------------------------------------------------------
# The Finder window
# --------------------------------------------------------------------------

class Finder:
    def __init__(self, root, start):
        self.root = root
        self.config = Config()
        self.launcher = Launcher(self.config)
        setup_fonts(root)
        self.icons = Icons(root)

        self.cwd = None
        self.items: list[Item] = []
        self.index: dict[str, int] = {}
        self.selected: set[str] = set()
        self.anchor = None
        self.history: list[str] = []
        self.future: list[str] = []
        self.clipboard: tuple[list[str], str] = ([], "copy")
        self.edit = None
        self.press = None
        self.drag = None
        self.band = None
        self.typed = ("", 0.0)
        self.vis: dict[int, tuple] = {}
        self.id2idx: dict[int, int] = {}
        self.cols = 1
        self.region = (0, 0)
        self.sig = None
        self.disk = None
        self.drop_target = None
        self.trunc_cache: dict = {}
        self.render_job = None

        self.view = tk.StringVar(value=self.config["view"] if self.config["view"] in ("icon", "list") else "icon")
        self.show_hidden = tk.BooleanVar(value=bool(self.config["show_hidden"]))
        self.sort_col = self.config["sort"]
        self.sort_rev = bool(self.config["sort_reverse"])

        self.build_ui()
        self.build_menus()
        self.bind_keys()
        try:
            self.root.iconphoto(True, self.icons.get("folder"))
        except tk.TclError:
            pass
        if not self.navigate(start, record=False):
            self.navigate(os.path.expanduser("~"), record=False)
        self.root.after(POLL_MS, self.poll)

    # ----- building the window -----
    def build_ui(self):
        root = self.root
        root.configure(bg=BLACK)
        root.minsize(260, 160)
        root.geometry(self.config["geometry"])
        root.protocol("WM_DELETE_WINDOW", self.quit)
        frame = tk.Frame(root, bg=WHITE)
        frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.header = tk.Canvas(frame, height=INFO_H + 3, bg=WHITE, highlightthickness=0, bd=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", lambda e: self.draw_header())
        self.header.bind("<ButtonPress-1>", self.on_header_click)

        body = tk.Frame(frame, bg=WHITE)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=WHITE, highlightthickness=0, bd=0, width=520, height=300,
                                xscrollincrement=16, yscrollincrement=16, takefocus=1)
        self.vbar = MacScrollbar(body, "vertical", self.canvas.yview)
        self.hbar = MacScrollbar(body, "horizontal", self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.on_xscroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        GrowBox(body, root).grid(row=1, column=1)

        c = self.canvas
        c.bind("<Configure>", self.on_resize)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Button-3>", self.on_context)
        if IS_MAC:
            c.bind("<Button-2>", self.on_context)
            c.bind("<Control-Button-1>", self.on_context)
        for w in (c, self.vbar, self.hbar, self.header):
            w.bind("<MouseWheel>", self.on_wheel)
            w.bind("<Shift-MouseWheel>", lambda e: self.on_wheel(e, horizontal=True))
            w.bind("<Button-4>", self.on_wheel)
            w.bind("<Button-5>", self.on_wheel)
        c.focus_set()

    def build_menus(self):
        style = dict(tearoff=0, font=FONTS["system"], bg=WHITE, fg=BLACK, activebackground=BLACK,
                     activeforeground=WHITE, relief="solid", bd=1, activeborderwidth=0)
        bar = tk.Menu(self.root, tearoff=0, font=FONTS["system"], bg=WHITE, fg=BLACK,
                      activebackground=BLACK, activeforeground=WHITE, relief="flat", bd=0)
        self.root.configure(menu=bar)

        def menu(label):
            m = tk.Menu(bar, **style)
            bar.add_cascade(label=label, menu=m)
            return m

        m = menu("File")
        m.add_command(label="New File", accelerator="Ctrl+N", command=lambda: self.new_item(False))
        m.add_command(label="New Folder", accelerator="Ctrl+Shift+N", command=lambda: self.new_item(True))
        m.add_command(label="Open", accelerator="Ctrl+O", command=self.open_selection)
        m.add_command(label="Open Terminal Here", accelerator="Ctrl+T", command=self.open_terminal)
        m.add_separator()
        m.add_command(label="Get Info", accelerator="Ctrl+I", command=self.get_info)
        m.add_command(label="Rename", accelerator="F2", command=self.begin_rename)
        m.add_command(label="Duplicate", accelerator="Ctrl+D", command=self.duplicate)
        m.add_separator()
        m.add_command(label="Move to Trash", accelerator="Delete", command=self.trash_selection)
        m.add_command(label="Delete Permanently…", accelerator="Shift+Delete", command=self.delete_selection)
        m.add_separator()
        m.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit)

        m = menu("Edit")
        m.add_command(label="Cut", accelerator="Ctrl+X", command=lambda: self.copy(cut=True))
        m.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy)
        m.add_command(label="Paste", accelerator="Ctrl+V", command=self.paste)
        m.add_separator()
        m.add_command(label="Select All", accelerator="Ctrl+A", command=self.select_all)

        m = menu("View")
        m.add_radiobutton(label="by Icon", accelerator="Ctrl+1", variable=self.view, value="icon",
                          command=self.on_view_change)
        m.add_radiobutton(label="by Name", accelerator="Ctrl+2", variable=self.view, value="list",
                          command=self.on_view_change)
        m.add_separator()
        m.add_checkbutton(label="Show Hidden Files", accelerator="Ctrl+H", variable=self.show_hidden,
                          command=self.on_hidden_change)
        m.add_command(label="Refresh", accelerator="F5", command=self.reload)

        m = menu("Special")
        m.add_command(label="Clean Up Window", command=self.clean_up)
        m.add_command(label="Empty Trash…", command=self.empty_trash)
        m.add_separator()
        m.add_command(label="Enclosing Folder", accelerator="Ctrl+Up", command=self.go_up)
        m.add_command(label="Back", accelerator="Alt+Left", command=self.go_back)
        m.add_command(label="Forward", accelerator="Alt+Right", command=self.go_forward)
        m.add_command(label="Home", accelerator="Ctrl+Shift+H", command=self.go_home)
        m.add_command(label="Go to Folder…", accelerator="Ctrl+L", command=self.go_to_folder)

        m = menu("Help")
        m.add_command(label="Finder Help", accelerator="F1", command=lambda: show_help(self))
        m.add_command(label="About the Finder…", command=self.about)

        self.context = tk.Menu(self.root, **style)

    def bind_keys(self):
        r = self.root

        def key(seq, func, shifted=None):
            def handler(e):
                if self.edit:
                    return None
                if shifted is not None and e.state & 0x1:
                    shifted()
                else:
                    func()
                return "break"
            return handler

        def mod(letter, func, shifted=None):
            h = key(None, func, shifted)
            for m in MODIFIERS:
                r.bind(f"<{m}-{letter.lower()}>", h)
                r.bind(f"<{m}-{letter.upper()}>", h)

        mod("n", lambda: self.new_item(False), lambda: self.new_item(True))
        mod("t", self.open_terminal)
        mod("o", self.open_selection)
        mod("i", self.get_info)
        mod("d", self.duplicate)
        mod("c", self.copy)
        mod("x", lambda: self.copy(cut=True))
        mod("v", self.paste)
        mod("a", self.select_all)
        mod("h", self.toggle_hidden, self.go_home)
        mod("l", self.go_to_folder)
        mod("r", self.reload)
        mod("w", self.quit)
        mod("q", self.quit)
        for m in MODIFIERS:
            r.bind(f"<{m}-Key-1>", key(None, lambda: self.set_view("icon")))
            r.bind(f"<{m}-Key-2>", key(None, lambda: self.set_view("list")))
            r.bind(f"<{m}-Up>", key(None, self.go_up))
            r.bind(f"<{m}-Down>", key(None, self.open_selection))
            r.bind(f"<{m}-BackSpace>", key(None, self.trash_selection))
            r.bind(f"<{m}-bracketleft>", key(None, self.go_back))
            r.bind(f"<{m}-bracketright>", key(None, self.go_forward))
        r.bind("<F1>", key(None, lambda: show_help(self)))
        r.bind("<F2>", key(None, self.begin_rename))
        r.bind("<F5>", key(None, self.reload))
        r.bind("<Return>", key(None, self.open_selection))
        r.bind("<KP_Enter>", key(None, self.open_selection))
        r.bind("<Delete>", key(None, self.trash_selection))
        r.bind("<Shift-Delete>", key(None, self.delete_selection))
        r.bind("<BackSpace>", key(None, self.go_up))
        r.bind("<Alt-Up>", key(None, self.go_up))
        r.bind("<Alt-Left>", key(None, self.go_back))
        r.bind("<Alt-Right>", key(None, self.go_forward))
        r.bind("<Escape>", key(None, lambda: self.set_selection(set())))
        r.bind("<Left>", key(None, lambda: self.move_selection(-1, 0)))
        r.bind("<Right>", key(None, lambda: self.move_selection(1, 0)))
        r.bind("<Up>", key(None, lambda: self.move_selection(0, -1)))
        r.bind("<Down>", key(None, lambda: self.move_selection(0, 1)))
        r.bind("<Home>", key(None, lambda: self.select_index(0)))
        r.bind("<End>", key(None, lambda: self.select_index(len(self.items) - 1)))
        r.bind("<Prior>", key(None, lambda: self.canvas.yview_scroll(-1, "pages")))
        r.bind("<Next>", key(None, lambda: self.canvas.yview_scroll(1, "pages")))
        r.bind("<Key>", self.on_typed)

    # ----- loading folders -----
    def scan(self, path):
        hidden = self.show_hidden.get()
        items = []
        with os.scandir(path) as it:
            for entry in it:
                if not hidden and is_hidden(entry):
                    continue
                items.append(Item(path, entry))
        return items

    def sort_items(self):
        if self.view.get() == "icon" or self.sort_col == "name":
            key = lambda i: (i.name.lower(), i.name)
            rev = self.sort_rev if self.view.get() == "list" else False
        elif self.sort_col == "size":
            key = lambda i: (i.size if not i.is_dir else -1, i.name.lower())
            rev = self.sort_rev
        elif self.sort_col == "kind":
            key = lambda i: (i.kind.lower(), i.name.lower())
            rev = self.sort_rev
        else:
            key = lambda i: (i.mtime, i.name.lower())
            rev = self.sort_rev
        self.items.sort(key=key, reverse=rev)
        self.index = {it.name: i for i, it in enumerate(self.items)}

    def dir_signature(self, path=None):
        try:
            st = os.stat(path or self.cwd)
            return (st.st_mtime_ns, st.st_ino, self.show_hidden.get())
        except OSError:
            return None

    def navigate(self, path, record=True, select=None):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            alert(self, f"The folder “{path}” could not be found.")
            return False
        try:
            items = self.scan(path)
        except PermissionError:
            alert(self, f"The folder “{display_name(path)}” could not be opened, because you "
                        "do not have enough access privileges.", icon="stop")
            return False
        except OSError as e:
            alert(self, f"The folder “{display_name(path)}” could not be opened. {e.strerror or e}",
                  icon="stop")
            return False
        if self.edit:
            self.end_rename(commit=False)
        if record and self.cwd and os.path.normcase(self.cwd) != os.path.normcase(path):
            self.history.append(self.cwd)
            self.future.clear()
        self.cwd = path
        self.items = items
        self.sig = self.dir_signature()
        self.sort_items()
        self.selected = {n for n in (select or set()) if n in self.index}
        self.anchor = next(iter(self.selected), None)
        self.root.title(display_name(path))
        self.update_disk()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.render()
        if self.anchor:
            self.ensure_visible(self.index[self.anchor])
        return True

    def reload(self, select=None):
        if not self.cwd:
            return
        if not os.path.isdir(self.cwd):
            parent = self.cwd
            while parent and not os.path.isdir(parent):
                new_parent = os.path.dirname(parent)
                if new_parent == parent:
                    parent = os.path.expanduser("~")
                    break
                parent = new_parent
            self.navigate(parent, record=False)
            return
        try:
            self.items = self.scan(self.cwd)
        except OSError as e:
            alert(self, f"The folder could not be read. {e.strerror or e}", icon="stop")
            return
        self.sig = self.dir_signature()
        self.sort_items()
        keep = self.selected if select is None else set(select)
        self.selected = {n for n in keep if n in self.index}
        if self.anchor not in self.index:
            self.anchor = next(iter(self.selected), None)
        self.update_disk()
        y = self.canvas.yview()[0]
        self.render()
        self.canvas.yview_moveto(y)
        if select:
            first = min(self.index[n] for n in self.selected) if self.selected else None
            if first is not None:
                self.ensure_visible(first)

    def update_disk(self):
        try:
            self.disk = shutil.disk_usage(self.cwd)
        except OSError:
            self.disk = None
        self.draw_header()

    def poll(self):
        try:
            self.launcher.reap()
            if self.cwd and not self.edit and not self.drag and not self.band and not self.press:
                sig = self.dir_signature()
                if sig != self.sig:
                    self.reload()
        except Exception:
            pass
        self.root.after(POLL_MS, self.poll)

    # ----- drawing -----
    def list_columns(self):
        w = max(self.canvas.winfo_width(), 320)
        name_x = 28
        name_w = max(150, int((w - 28) * 0.38))
        size_r = name_x + name_w + 64
        kind_x = size_r + 18
        date_x = kind_x + 150
        return {"name": name_x, "name_w": name_w, "size": size_r, "kind": kind_x,
                "date": date_x, "end": date_x + 190}

    def draw_header(self):
        h = self.header
        h.delete("all")
        w = h.winfo_width()
        n = len(self.items)
        f = FONTS["label"]
        y = INFO_H // 2 + 1
        h.create_text(10, y, text=f"{n} item" + ("" if n == 1 else "s"), anchor="w", font=f)
        if self.disk:
            h.create_text(w // 2, y, text=f"{fmt_disk(self.disk.used)} in disk", anchor="center", font=f)
            h.create_text(w - 10, y, text=f"{fmt_disk(self.disk.free)} available", anchor="e", font=f)
        yy = INFO_H
        if self.view.get() == "list":
            cols = self.list_columns()
            off = self.canvas.canvasx(0)
            yy += 1
            h.create_line(0, yy, w, yy, fill=BLACK)
            ty = yy + COLS_H // 2 + 1
            for key, label, x, anchor in (("name", "Name", cols["name"], "w"),
                                          ("size", "Size", cols["size"], "e"),
                                          ("kind", "Kind", cols["kind"], "w"),
                                          ("date", "Last Modified", cols["date"], "w")):
                font = FONTS["label_u"] if key == self.sort_col else f
                h.create_text(x - off, ty, text=label, anchor=anchor, font=font)
            yy += COLS_H
        h.create_line(0, yy, w, yy, fill=BLACK)
        h.create_line(0, yy + 2, w, yy + 2, fill=BLACK)
        want = yy + 3
        if int(h["height"]) != want:
            h.configure(height=want)

    def truncate(self, text, font, maxw):
        key = (text, font.name, maxw)
        hit = self.trunc_cache.get(key)
        if hit is not None:
            return hit
        result = text
        if font.measure(text) > maxw:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid] + "…") <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            result = text[:lo] + "…"
        if len(self.trunc_cache) > 20000:
            self.trunc_cache.clear()
        self.trunc_cache[key] = result
        return result

    def positions(self):
        return self.config["positions"].get(self.cwd, {})

    def layout(self):
        """Top-centre position of each icon in icon view."""
        w = max(self.canvas.winfo_width(), 100)
        self.cols = cols = max(1, w // CELL_MIN_W)
        cw = w / cols
        self.cell_w = cw
        custom = self.positions()
        out, slot = [], 0
        for it in self.items:
            if it.name in custom:
                x, y = custom[it.name]
            else:
                r, c = divmod(slot, cols)
                x, y = cw * c + cw / 2, 8 + r * CELL_H
                slot += 1
            out.append((x, y))
        return out

    def render(self):
        if self.edit:
            return
        c = self.canvas
        c.delete("all")
        self.vis.clear()
        self.id2idx.clear()
        self.drop_target = None
        width, height = c.winfo_width(), c.winfo_height()
        if self.view.get() == "icon":
            maxx, maxy = width, height
            for i, (x, y) in enumerate(self.layout()):
                it = self.items[i]
                font = FONTS["label_i"] if it.is_link else FONTS["label"]
                label = self.truncate(it.name, font, int(self.cell_w) - 8)
                bg = c.create_rectangle(0, 0, 0, 0, outline="", fill="")
                img = c.create_image(x, y, image=self.icons.get(it.icon), anchor="n")
                txt = c.create_text(x, y + 35, text=label, anchor="n", font=font, fill=BLACK)
                x0, y0, x1, y1 = c.bbox(txt)
                c.coords(bg, x0 - 2, y0, x1 + 2, y1)
                self.vis[i] = (img, txt, bg)
                for cid in (img, txt, bg):
                    self.id2idx[cid] = i
                maxx = max(maxx, x1 + 8, x + self.cell_w / 2)
                maxy = max(maxy, y + CELL_H)
        else:
            cols = self.list_columns()
            maxx = max(width, cols["end"])
            maxy = max(height, 4 + len(self.items) * ROW_H + 4)
            for i, it in enumerate(self.items):
                y = 4 + i * ROW_H
                cy = y + ROW_H // 2
                row = c.create_rectangle(0, y, maxx, y + ROW_H, outline="", fill=WHITE)
                font = FONTS["label_i"] if it.is_link else FONTS["label"]
                img = c.create_image(16, cy, image=self.icons.get(it.icon, 16))
                bg = c.create_rectangle(0, 0, 0, 0, outline="", fill="")
                txt = c.create_text(cols["name"], cy, anchor="w", font=font, fill=BLACK,
                                    text=self.truncate(it.name, font, cols["name_w"]))
                x0, y0, x1, y1 = c.bbox(txt)
                c.coords(bg, x0 - 2, y0, x1 + 2, y1)
                size = "—" if it.is_dir else fmt_list_size(it.size)
                others = [
                    c.create_text(cols["size"], cy, anchor="e", font=FONTS["label"], text=size),
                    c.create_text(cols["kind"], cy, anchor="w", font=FONTS["label"],
                                  text=self.truncate(it.kind, FONTS["label"], 140)),
                    c.create_text(cols["date"], cy, anchor="w", font=FONTS["label"],
                                  text=fmt_date(it.mtime) if it.mtime else "—"),
                ]
                self.vis[i] = (img, txt, bg)
                for cid in [row, img, txt, bg] + others:
                    self.id2idx[cid] = i
        self.region = (maxx, maxy)
        c.configure(scrollregion=(0, 0, maxx, maxy))
        for i in self.vis:
            if self.items[i].name in self.selected:
                self.update_visual(i)
        self.draw_header()

    def update_visual(self, i):
        if i not in self.vis:
            return
        img, txt, bg = self.vis[i]
        it = self.items[i]
        sel = it.name in self.selected
        dark = sel or i == self.drop_target
        size = 32 if self.view.get() == "icon" else 16
        c = self.canvas
        c.itemconfigure(img, image=self.icons.get(it.icon, size, dark))
        c.itemconfigure(txt, fill=WHITE if sel else BLACK)
        c.itemconfigure(bg, fill=BLACK if sel else "")

    def schedule_render(self):
        if self.render_job:
            self.root.after_cancel(self.render_job)
        self.render_job = self.root.after(30, self._do_render)

    def _do_render(self):
        self.render_job = None
        self.render()

    def on_resize(self, _e):
        self.schedule_render()

    def on_xscroll(self, first, last):
        self.hbar.set(first, last)
        if self.view.get() == "list":
            self.draw_header()

    def on_wheel(self, e, horizontal=False):
        if getattr(e, "num", None) == 4:
            step = -1
        elif getattr(e, "num", None) == 5:
            step = 1
        elif IS_MAC:
            step = -e.delta
        else:
            step = -1 if e.delta > 0 else 1
        if horizontal or (e.state & 0x1):
            self.canvas.xview_scroll(step * 2, "units")
        else:
            self.canvas.yview_scroll(step * 2, "units")
        return "break"

    def item_bbox(self, i):
        if i not in self.vis:
            return None
        img, txt, bg = self.vis[i]
        a = self.canvas.bbox(img)
        b = self.canvas.bbox(txt)
        if not a or not b:
            return a or b
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    def ensure_visible(self, i):
        bb = self.item_bbox(i)
        if not bb:
            return
        c = self.canvas
        total_w, total_h = self.region
        top, h = c.canvasy(0), c.winfo_height()
        if bb[1] < top:
            c.yview_moveto(max(0, bb[1] - 4) / max(1, total_h))
        elif bb[3] > top + h:
            c.yview_moveto((bb[3] + 4 - h) / max(1, total_h))
        left, w = c.canvasx(0), c.winfo_width()
        if bb[0] < left:
            c.xview_moveto(max(0, bb[0] - 4) / max(1, total_w))
        elif bb[2] > left + w:
            c.xview_moveto((bb[2] + 4 - w) / max(1, total_w))

    # ----- selection -----
    def set_selection(self, names):
        old = self.selected
        self.selected = set(names)
        for n in old ^ self.selected:
            if n in self.index:
                self.update_visual(self.index[n])

    def select_index(self, i):
        if not self.items:
            return
        i = max(0, min(len(self.items) - 1, i))
        name = self.items[i].name
        self.set_selection({name})
        self.anchor = name
        self.ensure_visible(i)

    def select_all(self):
        self.set_selection({it.name for it in self.items})

    def move_selection(self, dx, dy):
        if not self.items:
            return
        if self.anchor not in self.index:
            self.select_index(0)
            return
        cur = self.index[self.anchor]
        if self.view.get() == "icon":
            step = dx + dy * self.cols
        else:
            step = dy
            if not step:
                return
        new = cur + step
        if 0 <= new < len(self.items):
            self.select_index(new)

    def selected_items(self):
        return [it for it in self.items if it.name in self.selected]

    def on_typed(self, e):
        if self.edit or not e.char or not e.char.isprintable():
            return None
        if e.state & (0x4 | 0x8 | 0x20000):  # Ctrl, Alt/Cmd, Windows Alt
            return None
        text, when = self.typed
        now = time.monotonic()
        text = (text if now - when < 1.0 else "") + e.char.lower()
        self.typed = (text, now)
        for i, it in enumerate(self.items):
            if it.name.lower().startswith(text):
                self.select_index(i)
                break
        else:
            # pick the closest following name alphabetically
            for i, it in enumerate(sorted(self.items, key=lambda x: x.name.lower())):
                if it.name.lower() > text:
                    self.select_index(self.index[it.name])
                    break
        return "break"

    # ----- mouse -----
    def hit(self, x, y):
        """Index of the item under canvas coordinates (x, y), or None."""
        for cid in reversed(self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)):
            if cid in self.id2idx:
                return self.id2idx[cid]
        return None

    def on_press(self, e):
        self.canvas.focus_set()
        if self.edit:
            self.end_rename(commit=True)
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        i = self.hit(x, y)
        toggle = bool(e.state & (0x1 | 0x4)) or (IS_MAC and bool(e.state & 0x8))
        if i is not None:
            name = self.items[i].name
            if toggle:
                self.set_selection(self.selected ^ {name})
            elif name not in self.selected:
                self.set_selection({name})
            self.anchor = name
        elif not toggle:
            self.set_selection(set())
        self.press = {"x": x, "y": y, "i": i, "toggle": toggle, "base": set(self.selected)}

    def on_motion(self, e):
        p = self.press
        if not p:
            return
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self.drag:
            self.drag_to(x, y)
            return
        if self.band:
            self.band_to(x, y)
            return
        if abs(x - p["x"]) < 4 and abs(y - p["y"]) < 4:
            return
        if p["i"] is not None and self.items[p["i"]].name in self.selected:
            self.start_drag(p["x"], p["y"])
            self.drag_to(x, y)
        elif p["i"] is None:
            self.band = self.canvas.create_rectangle(p["x"], p["y"], x, y, outline=BLACK, dash=(2, 2))
            self.band_to(x, y)

    def on_release(self, e):
        p, self.press = self.press, None
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self.drag:
            self.finish_drag(x, y)
        elif self.band:
            self.canvas.delete(self.band)
            self.band = None
        elif p and p["i"] is not None and not p["toggle"]:
            name = self.items[p["i"]].name
            if self.selected != {name}:
                self.set_selection({name})

    def on_double(self, e):
        self.press = None
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        i = self.hit(x, y)
        if i is None:
            return
        self.set_selection({self.items[i].name})
        self.anchor = self.items[i].name
        self.open_selection()

    def band_to(self, x, y):
        p = self.press
        c = self.canvas
        c.coords(self.band, p["x"], p["y"], x, y)
        x0, x1 = sorted((p["x"], x))
        y0, y1 = sorted((p["y"], y))
        inside = set()
        for i in self.vis:
            bb = self.item_bbox(i)
            if bb and bb[0] < x1 and bb[2] > x0 and bb[1] < y1 and bb[3] > y0:
                inside.add(self.items[i].name)
        self.set_selection(p["base"] ^ inside if p["toggle"] else inside)

    def start_drag(self, x, y):
        c = self.canvas
        ghosts = []
        for it in self.selected_items():
            i = self.index[it.name]
            if i not in self.vis:
                continue
            img, txt, _bg = self.vis[i]
            parts = (img, txt) if self.view.get() == "icon" else (txt,)
            for part in parts:
                bb = c.bbox(part)
                if bb:
                    ghosts.append(c.create_rectangle(*bb, outline=BLACK, dash=(2, 2), tags="ghost"))
        self.drag = {"x": x, "y": y, "lx": x, "ly": y}

    def drag_to(self, x, y):
        d = self.drag
        self.canvas.move("ghost", x - d["lx"], y - d["ly"])
        d["lx"], d["ly"] = x, y
        i = self.hit(x, y)
        target = None
        if i is not None and self.items[i].is_dir and self.items[i].name not in self.selected:
            target = i
        if target != self.drop_target:
            old, self.drop_target = self.drop_target, target
            if old is not None:
                self.update_visual(old)
            if target is not None:
                self.update_visual(target)

    def finish_drag(self, x, y):
        d, self.drag = self.drag, None
        self.canvas.delete("ghost")
        target, self.drop_target = self.drop_target, None
        if target is not None:
            self.update_visual(target)
            self.move_into(self.items[target].path, self.selected_items())
            return
        if self.view.get() != "icon":
            return
        dx, dy = x - d["x"], y - d["y"]
        if not dx and not dy:
            return
        layout = self.layout()
        pos = dict(self.positions())
        for it in self.selected_items():
            ox, oy = layout[self.index[it.name]]
            pos[it.name] = [max(self.cell_w / 2, ox + dx), max(4, oy + dy)]
        self.config["positions"][self.cwd] = pos
        self.config.save()
        self.render()

    def on_context(self, e):
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        i = self.hit(x, y)
        m = self.context
        m.delete(0, "end")
        if i is not None:
            name = self.items[i].name
            if name not in self.selected:
                self.set_selection({name})
                self.anchor = name
            m.add_command(label="Open", command=self.open_selection)
            m.add_command(label="Get Info", command=self.get_info)
            m.add_command(label="Rename", command=self.begin_rename)
            m.add_command(label="Duplicate", command=self.duplicate)
            m.add_separator()
            m.add_command(label="Cut", command=lambda: self.copy(cut=True))
            m.add_command(label="Copy", command=self.copy)
            m.add_separator()
            m.add_command(label="Move to Trash", command=self.trash_selection)
        else:
            self.set_selection(set())
            m.add_command(label="New File", command=lambda: self.new_item(False))
            m.add_command(label="New Folder", command=lambda: self.new_item(True))
            m.add_command(label="Paste", command=self.paste,
                          state="normal" if self.clipboard[0] else "disabled")
            m.add_separator()
            m.add_command(label="Open Terminal Here", command=self.open_terminal)
            m.add_command(label="Clean Up Window", command=self.clean_up)
            m.add_command(label="Refresh", command=self.reload)
        m.tk_popup(e.x_root, e.y_root)
        return "break"

    def on_header_click(self, e):
        if self.view.get() != "list" or e.y <= INFO_H:
            return
        cols = self.list_columns()
        x = e.x + self.canvas.canvasx(0)
        if x < cols["name"] + cols["name_w"] + 8:
            col = "name"
        elif x < cols["kind"] - 6:
            col = "size"
        elif x < cols["date"] - 6:
            col = "kind"
        else:
            col = "date"
        if col == self.sort_col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col = col
            self.sort_rev = col in ("size", "date")
        self.config["sort"], self.config["sort_reverse"] = self.sort_col, self.sort_rev
        self.config.save()
        self.sort_items()
        self.render()

    # ----- views -----
    def set_view(self, view):
        self.view.set(view)
        self.on_view_change()

    def on_view_change(self):
        if self.edit:
            self.end_rename(commit=True)
        self.config["view"] = self.view.get()
        self.config.save()
        self.sort_items()
        self.canvas.xview_moveto(0)
        self.render()

    def toggle_hidden(self):
        self.show_hidden.set(not self.show_hidden.get())
        self.on_hidden_change()

    def on_hidden_change(self):
        self.config["show_hidden"] = self.show_hidden.get()
        self.config.save()
        self.reload()

    def clean_up(self):
        self.config["positions"].pop(self.cwd, None)
        self.config.save()
        self.render()

    # ----- navigation -----
    def go_up(self):
        parent = os.path.dirname(self.cwd)
        if parent and parent != self.cwd:
            self.navigate(parent, select={os.path.basename(self.cwd)})
        else:
            self.root.bell()

    def go_back(self):
        if not self.history:
            self.root.bell()
            return
        here = self.cwd
        target = self.history.pop()
        if self.navigate(target, record=False):
            self.future.append(here)

    def go_forward(self):
        if not self.future:
            self.root.bell()
            return
        here = self.cwd
        target = self.future.pop()
        if self.navigate(target, record=False):
            self.history.append(here)

    def go_home(self):
        self.navigate(os.path.expanduser("~"))

    def go_to_folder(self):
        path = ask_string(self, "Go to the folder:", self.cwd, ok="Go")
        if path and path.strip():
            p = os.path.expanduser(path.strip())
            if not os.path.isabs(p):
                p = os.path.join(self.cwd, p)
            if os.path.isfile(p):
                self.navigate(os.path.dirname(p), select={os.path.basename(p)})
            else:
                self.navigate(p)

    # ----- opening -----
    @staticmethod
    def ext_key(name):
        ext = os.path.splitext(name)[1].lower()
        return ext or "(none)"

    def open_selection(self):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        files = [it for it in items if not it.is_dir]
        dirs = [it for it in items if it.is_dir]
        if files:
            self.open_files(files)
        if dirs:
            self.navigate(dirs[0].path)

    def open_files(self, files):
        choice = OpenWithDialog(self, files).show()
        if not choice:
            return
        self.config["open_with"][self.ext_key(files[0].name)] = {
            k: v for k, v in choice.items() if k != "mimes"}
        self.config.save()
        try:
            self.launcher.launch(choice, [f.path for f in files])
        except (OSError, ValueError) as e:
            what = f"“{files[0].name}”" if len(files) == 1 else "the items"
            alert(self, f"Could not open {what} with {choice.get('name', 'that program')}.\n\n"
                        f"{getattr(e, 'strerror', None) or e}", icon="stop")

    def open_terminal(self):
        try:
            self.launcher.open_terminal(self.cwd)
        except OSError as e:
            alert(self, f"Could not open a terminal.\n\n{getattr(e, 'strerror', None) or e}", icon="stop")

    # ----- creating and renaming -----
    def new_item(self, folder):
        if self.edit:
            self.end_rename(commit=True)
        name = unique_name(self.cwd, "untitled folder" if folder else "untitled")
        path = os.path.join(self.cwd, name)
        try:
            if folder:
                os.mkdir(path)
            else:
                with open(path, "x"):
                    pass
        except OSError as e:
            alert(self, f"The {'folder' if folder else 'file'} could not be created. "
                        f"{e.strerror or e}", icon="stop")
            return
        self.reload(select={name})
        self.anchor = name
        self.begin_rename(name)

    def begin_rename(self, name=None, text=None):
        if self.edit:
            return
        if name is None:
            if len(self.selected) != 1:
                self.root.bell()
                return
            name = next(iter(self.selected))
        if name not in self.index:
            return
        i = self.index[name]
        self.ensure_visible(i)
        img, txt, bg = self.vis[i]
        c = self.canvas
        font = FONTS["label"]
        var = tk.StringVar(value=text if text is not None else name)
        entry = tk.Entry(c, textvariable=var, font=font, relief="flat", bd=0, bg=WHITE, fg=BLACK,
                         highlightthickness=1, highlightbackground=BLACK, highlightcolor=BLACK,
                         insertbackground=BLACK, selectbackground=BLACK, selectforeground=WHITE,
                         justify="center" if self.view.get() == "icon" else "left")
        x0, y0, x1, y1 = c.bbox(txt)
        if self.view.get() == "icon":
            x = (c.coords(img)[0])
            win = c.create_window(x, y0 - 2, window=entry, anchor="n")
        else:
            win = c.create_window(x0 - 3, (y0 + y1) / 2, window=entry, anchor="w")
        c.itemconfigure(txt, state="hidden")
        c.itemconfigure(bg, state="hidden")

        def fit(*_):
            w = font.measure(var.get() or " ") + 16
            maxw = 360 if self.view.get() == "list" else max(160, int(self.cell_w * 1.8))
            c.itemconfigure(win, width=max(60, min(maxw, w)), height=font.metrics("linespace") + 4)

        var.trace_add("write", fit)
        fit()
        self.edit = {"entry": entry, "win": win, "name": name, "var": var, "txt": txt, "bg": bg}
        entry.focus_set()
        current = var.get()
        stem, _ext = split_ext(current)
        is_dir = self.items[i].is_dir
        entry.select_range(0, len(current) if is_dir else len(stem))
        entry.icursor(len(current) if is_dir else len(stem))
        entry.bind("<Return>", lambda e: self.end_rename(commit=True))
        entry.bind("<KP_Enter>", lambda e: self.end_rename(commit=True))
        entry.bind("<Escape>", lambda e: self.end_rename(commit=False))
        entry.bind("<FocusOut>", lambda e: self.end_rename(commit=True))

    def validate_name(self, old, new):
        bad = [os.sep] + ([os.altsep] if os.altsep else [])
        if IS_WIN:
            bad += list('<>:"|?*')
        if any(ch in new for ch in bad) or new in (".", ".."):
            return f"The name “{new}” contains characters that cannot be used in a file name."
        new_path = os.path.join(self.cwd, new)
        if os.path.lexists(new_path) and new.lower() != old.lower():
            return "That name is already taken. Please use a different name."
        if os.path.lexists(new_path) and new != old:
            try:
                if not os.path.samefile(new_path, os.path.join(self.cwd, old)):
                    return "That name is already taken. Please use a different name."
            except OSError:
                return "That name is already taken. Please use a different name."
        return None

    def end_rename(self, commit=True):
        ed, self.edit = self.edit, None
        if not ed:
            return "break"
        new = ed["var"].get().strip()
        old = ed["name"]
        try:
            self.canvas.delete(ed["win"])
            ed["entry"].destroy()
        except tk.TclError:
            pass
        self.canvas.focus_set()
        if not commit or not new or new == old:
            try:
                self.canvas.itemconfigure(ed["txt"], state="normal")
                self.canvas.itemconfigure(ed["bg"], state="normal")
            except tk.TclError:
                pass
            if self.render_job is None:
                self.render()
            return "break"
        err = self.validate_name(old, new)
        if err:
            self.render()
            alert(self, err)
            self.begin_rename(old, text=new)
            return "break"
        try:
            os.rename(os.path.join(self.cwd, old), os.path.join(self.cwd, new))
        except OSError as e:
            self.render()
            alert(self, f"“{old}” could not be renamed. {e.strerror or e}", icon="stop")
            return "break"
        pos = self.config["positions"].get(self.cwd)
        if pos and old in pos:
            pos[new] = pos.pop(old)
            self.config.save()
        self.anchor = new
        self.reload(select={new})
        return "break"

    # ----- copy, paste, move, delete -----
    def busy(self, on):
        try:
            self.root.configure(cursor="watch" if on else "")
            self.root.update_idletasks()
        except tk.TclError:
            pass

    def copy(self, cut=False):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        paths = [it.path for it in items]
        self.clipboard = (paths, "cut" if cut else "copy")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(paths))

    def paste(self):
        paths, mode = self.clipboard
        if not paths:
            self.root.bell()
            return
        new_names, errors = set(), []
        self.busy(True)
        try:
            for src in paths:
                if not os.path.lexists(src):
                    errors.append(f"“{os.path.basename(src)}” no longer exists.")
                    continue
                name = os.path.basename(src)
                if os.path.isdir(src) and not os.path.islink(src):
                    real_src, real_dst = os.path.realpath(src), os.path.realpath(self.cwd)
                    if real_dst == real_src or real_dst.startswith(real_src + os.sep):
                        errors.append(f"“{name}” cannot be put inside itself.")
                        continue
                try:
                    if mode == "cut":
                        if os.path.normcase(os.path.dirname(src)) == os.path.normcase(self.cwd):
                            new_names.add(name)
                            continue
                        dest = os.path.join(self.cwd, name)
                        if os.path.lexists(dest):
                            errors.append(f"An item named “{name}” already exists here.")
                            continue
                        shutil.move(src, dest)
                    else:
                        if os.path.lexists(os.path.join(self.cwd, name)):
                            name = copy_name(self.cwd, name)
                        self.copy_path(src, os.path.join(self.cwd, name))
                    new_names.add(name)
                except (OSError, shutil.Error) as e:
                    errors.append(f"“{name}”: {getattr(e, 'strerror', None) or e}")
        finally:
            self.busy(False)
        if mode == "cut":
            self.clipboard = ([], "copy")
        self.reload(select=new_names)
        if errors:
            alert(self, "Some items could not be pasted.\n\n" + "\n".join(errors[:8]), icon="stop")

    @staticmethod
    def copy_path(src, dest):
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)

    def duplicate(self):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        new_names, errors = set(), []
        self.busy(True)
        try:
            for it in items:
                name = copy_name(self.cwd, it.name)
                try:
                    self.copy_path(it.path, os.path.join(self.cwd, name))
                    new_names.add(name)
                except (OSError, shutil.Error) as e:
                    errors.append(f"“{it.name}”: {getattr(e, 'strerror', None) or e}")
        finally:
            self.busy(False)
        self.reload(select=new_names)
        if errors:
            alert(self, "Some items could not be duplicated.\n\n" + "\n".join(errors[:8]), icon="stop")

    def move_into(self, folder, items):
        errors = []
        self.busy(True)
        try:
            for it in items:
                if it.is_dir and not it.is_link:
                    real_src, real_dst = os.path.realpath(it.path), os.path.realpath(folder)
                    if real_dst == real_src or real_dst.startswith(real_src + os.sep):
                        errors.append(f"“{it.name}” cannot be put inside itself.")
                        continue
                dest = os.path.join(folder, it.name)
                if os.path.lexists(dest):
                    errors.append(f"An item named “{it.name}” already exists in “{display_name(folder)}”.")
                    continue
                try:
                    shutil.move(it.path, dest)
                except (OSError, shutil.Error) as e:
                    errors.append(f"“{it.name}”: {getattr(e, 'strerror', None) or e}")
        finally:
            self.busy(False)
        self.reload(select=set())
        if errors:
            alert(self, "Some items could not be moved.\n\n" + "\n".join(errors[:8]), icon="stop")

    def trash_selection(self):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        errors = []
        self.busy(True)
        try:
            for it in items:
                try:
                    if not move_to_trash(it.path):
                        self.busy(False)
                        self.delete_selection()
                        return
                except (OSError, shutil.Error) as e:
                    errors.append(f"“{it.name}”: {getattr(e, 'strerror', None) or e}")
        finally:
            self.busy(False)
        self.reload(select=set())
        if errors:
            alert(self, "Some items could not be moved to the Trash.\n\n" + "\n".join(errors[:8]),
                  icon="stop")

    def delete_selection(self):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        what = f"“{items[0].name}”" if len(items) == 1 else f"these {len(items)} items"
        ok = alert(self, f"Are you sure you want to permanently delete {what}? This cannot be undone.",
                   buttons=(("Cancel", False, False), ("Delete", True, True)), cancel_value=False)
        if not ok:
            return
        errors = []
        self.busy(True)
        try:
            for it in items:
                try:
                    remove_path(it.path)
                except OSError as e:
                    errors.append(f"“{it.name}”: {e.strerror or e}")
        finally:
            self.busy(False)
        self.reload(select=set())
        if errors:
            alert(self, "Some items could not be deleted.\n\n" + "\n".join(errors[:8]), icon="stop")

    def empty_trash(self):
        root, contents = trash_contents()
        if root is None:
            alert(self, "There is no Trash on this system, so there is nothing to empty.", icon="note")
            return
        if not contents:
            alert(self, "The Trash is empty.", icon="note")
            return
        n = len(contents)
        ok = alert(self, f"The Trash contains {n} item{'s' if n != 1 else ''}. Are you sure you "
                         "want to permanently remove these items?",
                   buttons=(("Cancel", False, False), ("OK", True, True)), cancel_value=False)
        if not ok:
            return
        self.busy(True)
        failed = 0
        try:
            for p in contents:
                try:
                    remove_path(p)
                except OSError:
                    failed += 1
            if not IS_MAC:
                info = os.path.join(root, "info")
                for n_ in os.listdir(info) if os.path.isdir(info) else []:
                    try:
                        os.remove(os.path.join(info, n_))
                    except OSError:
                        pass
        finally:
            self.busy(False)
        self.reload()
        if failed:
            alert(self, f"{failed} item(s) in the Trash could not be removed.", icon="stop")

    # ----- other commands -----
    def get_info(self):
        items = self.selected_items()
        if not items:
            self.root.bell()
            return
        for it in items[:10]:
            InfoWindow(self, it)

    def about(self):
        alert(self, "Finder\n\nA recreation of the Macintosh System 7 Finder, written in Python "
                    "with Tkinter.\n\nPress F1 for help.", icon="note", title="About the Finder")

    def quit(self):
        if self.edit:
            self.end_rename(commit=True)
        try:
            self.config["geometry"] = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
        except tk.TclError:
            pass
        self.config.save()
        self.root.destroy()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    start = argv[0] if argv else os.getcwd()
    try:
        root = tk.Tk(className="Finder")
    except tk.TclError as e:
        sys.stderr.write(f"finder-py: cannot open a window ({e}).\n")
        return 1
    Finder(root, start)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
