# -*- coding: utf-8 -*-
import os, sys, json, shutil, subprocess, time, re, threading, tempfile
from pathlib import Path
from urllib import request

# Qt stability
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("QT_OPENGL", "software")

from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtGui import QPixmap, QIcon, QCursor, QPainter, QColor, QPen, QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMenu, QAction, QFileDialog, QMessageBox,
    QSystemTrayIcon, QStyle, QDialog, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton
)

# ---------- CONFIG ----------
STEAM_DEFAULT = r"C:\Program Files (x86)\Steam"

TARGET_NAME = "spprt.exe"  # tool adı
RTOOL_DIR = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "rTool"

APPDATA_DIR = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "rTools"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = APPDATA_DIR / "state.json"
NAME_CACHE_FILE = APPDATA_DIR / "name_cache.json"

NESTED_MAX_DEPTH = 6

# ---------- HELPERS ----------
def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    return default

def save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    except Exception:
        pass

def ensure_dir(p: str):
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass

def open_path(p: str) -> bool:
    try:
        if p and os.path.exists(p):
            os.startfile(p)
            return True
    except Exception:
        pass
    return False

def is_lua(p: str) -> bool:
    return (p or "").lower().endswith(".lua")

def is_manifest(p: str) -> bool:
    low = (p or "").lower()
    return low.endswith(".manifest") or low.endswith(".mfst")

def iter_files_limited(root: str, max_depth: int = 6):
    rootp = Path(root)
    if rootp.is_file():
        yield str(rootp)
        return
    if not rootp.exists():
        return
    base = rootp.resolve()
    for dirpath, dirnames, filenames in os.walk(str(base)):
        try:
            rel = Path(dirpath).resolve().relative_to(base)
            if len(rel.parts) >= max_depth:
                dirnames[:] = []
        except Exception:
            pass
        for fn in filenames:
            yield str(Path(dirpath) / fn)

def extract_appid_from_lua(path: str) -> str:
    try:
        t = open(path, "r", encoding="utf-8", errors="ignore").read()
        for pat in (
            r"addappid\s*\(\s*(\d+)",
            r"setmanifestid\s*\(\s*(\d+)",
            r"app[_\s-]*id\s*[:=]\s*(\d+)",
            r"\b(\d{4,7})\b",
        ):
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""

# ✅ NEW: tool path resolver (dev + embedded build)
def get_tool_path_for_run(target_name: str) -> str:
    """
    - PyInstaller onefile build ise: sys._MEIPASS içinden al, temp'e çıkar, onu döndür.
    - .py ile çalışıyorsan: script klasöründeki exe'yi döndür.
    - Bulamazsa boş string döndür (fallback için).
    """
    # Build mode
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        src = base / target_name
        if not src.exists():
            src = base / "bin" / target_name  # eğer bin'e gömdüysen
        if not src.exists():
            return ""

        out_dir = Path(tempfile.gettempdir()) / "rTools"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / target_name
        try:
            shutil.copy2(src, dst)  # her seferinde kopyala -> cache derdi yok
        except Exception:
            # aynı dosya kullanımda vs olabilir; yine de dene
            pass
        return str(dst)

    # Dev (.py) mode: yanında duran exe
    exe = Path(__file__).parent / target_name
    if exe.exists():
        return str(exe)

    return ""

# ---------- ADMIN / UAC ----------
def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def run_as_admin(params: str) -> bool:
    """Relaunch current exe as admin with params. Returns True if launch requested."""
    try:
        import ctypes
        exe = sys.executable  # if built, this is your .exe
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return ret > 32
    except Exception:
        return False

def is_built_exe() -> bool:
    return not (sys.executable.lower().endswith("python.exe") or sys.executable.lower().endswith("pythonw.exe"))

def current_app_exe_path() -> str:
    return os.path.abspath(sys.executable)

# ---------- STEAM NAME CACHE ----------
_NAME_CACHE = load_json(NAME_CACHE_FILE, {})

def req_json(url: str, timeout=6):
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)

def get_game_name(appid: str) -> str:
    if not appid:
        return ""
    if appid in _NAME_CACHE and _NAME_CACHE[appid]:
        return _NAME_CACHE[appid]

    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=en"
    for _ in range(3):
        try:
            data = req_json(url, timeout=6)
            block = data.get(str(appid))
            if block and block.get("success") and isinstance(block.get("data"), dict):
                name = (block["data"].get("name") or "").strip()
                if name:
                    _NAME_CACHE[appid] = name
                    save_json(NAME_CACHE_FILE, _NAME_CACHE)
                    return name
        except Exception:
            time.sleep(0.35)

    if appid not in _NAME_CACHE:
        _NAME_CACHE[appid] = ""
        save_json(NAME_CACHE_FILE, _NAME_CACHE)
    return f"App {appid}"

# ---------- Search Dialog ----------
class GameSearchDialog(QDialog):
    def __init__(self, parent, games_items):
        super().__init__(parent)
        self.setWindowTitle("Search Games")
        self.setModal(True)
        self.resize(520, 560)

        self.all = games_items[:]
        self.all.sort(key=lambda x: x[0].lower())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type to filter (e.g. baldur)")
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)

        row = QHBoxLayout()
        self.btn_open = QPushButton("Actions…")
        self.btn_close = QPushButton("Close")
        row.addWidget(self.btn_open)
        row.addStretch(1)
        row.addWidget(self.btn_close)

        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

        self.setStyleSheet("""
            QDialog { background: #0f0f0f; color: #f2f2f2; }
            QLineEdit {
                background: #151515; border: 1px solid #2a2a2a;
                border-radius: 10px; padding: 10px; color: #f2f2f2;
                selection-background-color: #2a82da;
            }
            QListWidget {
                background: #111111; border: 1px solid #2a2a2a;
                border-radius: 12px; color: #f2f2f2;
            }
            QListWidget::item {
                padding: 10px;
                border-radius: 10px;
                color: #f2f2f2;
            }
            QListWidget::item:selected {
                background: #1f1f1f;
                color: #ffffff;
            }
            QPushButton {
                background: #151515; border: 1px solid #2a2a2a;
                border-radius: 10px; padding: 10px 14px; color: #f2f2f2;
            }
            QPushButton:hover { background: #1b1b1b; }
        """)

        self.btn_close.clicked.connect(self.reject)
        self.btn_open.clicked.connect(self._do_actions)
        self.edit.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _: self._do_actions())

        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._ctx_menu)

        self._filter("")

    def update_items(self, games_items):
        self.all = games_items[:]
        self.all.sort(key=lambda x: x[0].lower())
        self._filter(self.edit.text())

    def _filter(self, text: str):
        q = (text or "").strip().lower()
        self.list.clear()
        for name, aid in self.all:
            if q and q not in name.lower():
                continue
            it = QListWidgetItem(f"{name}  ({aid})")
            it.setData(Qt.UserRole, aid)
            it.setForeground(QColor(242, 242, 242))
            self.list.addItem(it)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _do_actions(self):
        it = self.list.currentItem()
        if not it:
            return
        aid = it.data(Qt.UserRole)
        self.accept()
        self.parent().open_game_actions(aid)

    def _ctx_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        aid = it.data(Qt.UserRole)

        m = QMenu(self)
        m.setStyleSheet("""
            QMenu {
                background: rgba(18,18,18,235);
                color: #f2f2f2;
                border: 1px solid rgba(60,60,60,180);
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item { padding: 8px 14px; border-radius: 10px; }
            QMenu::item:selected { background: rgba(40,40,40,220); }
        """)
        a1 = QAction("Delete (LUA only)", self)
        a2 = QAction("Delete (LUA + manifests)", self)
        a1.triggered.connect(lambda: self.parent()._remove_game(aid, False))
        a2.triggered.connect(lambda: self.parent()._remove_game(aid, True))
        m.addAction(a1); m.addAction(a2)
        m.exec_(self.list.mapToGlobal(pos))

# ---------- Main ----------
class MiniIcon(QWidget):
    def __init__(self):
        super().__init__()

        self.state = load_json(STATE_FILE, {
            "steam_path": STEAM_DEFAULT,
            "pos": [60, 180],
            "always_on_top": True,
            "icon_path": "",
            "tool_cached_path": ""
        })

        self.steam_path = self.state.get("steam_path") or STEAM_DEFAULT
        self.always_on_top = bool(self.state.get("always_on_top", True))

        self.stplugin = os.path.join(self.steam_path, "config", "stplug-in")
        self.depotcache = os.path.join(self.steam_path, "depotcache")
        ensure_dir(self.stplugin)
        ensure_dir(self.depotcache)
        ensure_dir(str(RTOOL_DIR))

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._apply_window_flags()
        self.setAcceptDrops(True)
        self.setFixedSize(58, 58)

        self._dragging = False
        self._drag_offset = QPoint()

        self._menu_css = """
            QMenu {
                background: rgba(18,18,18,235);
                color: #f2f2f2;
                border: 1px solid rgba(60,60,60,180);
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item { padding: 8px 14px; border-radius: 10px; color: #f2f2f2; }
            QMenu::item:selected { background: rgba(40,40,40,220); color: #ffffff; }
            QMenu::separator { height: 1px; background: rgba(80,80,80,160); margin: 6px 10px; }
        """

        self.icon_pm = QPixmap()
        self.icon_qicon = QIcon()
        self._load_icon()

        x, y = self.state.get("pos", [60, 180])
        self.move(int(x), int(y))

        self.games = {}
        self.search_dlg = None
        self.refresh_games()

        self._setup_tray()
        self._update_hover_text()
        self._start_name_resolver()

    def _apply_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _update_hover_text(self):
        self.setToolTip(f"rTools\nTool: {TARGET_NAME}")
        if getattr(self, "tray", None):
            self.tray.setToolTip(f"rTools — {TARGET_NAME}")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(2, 2, -2, -2)
        bg = QColor(10, 10, 10, 170)
        border = QColor(80, 80, 80, 160)
        p.setBrush(bg)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(rect, 14, 14)

        if not self.icon_pm.isNull():
            pm = self.icon_pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - pm.width()) // 2
            y = (self.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        else:
            p.setPen(QColor(242, 242, 242, 230))
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.drawText(self.rect(), Qt.AlignCenter, "r")

        p.end()

    def _load_icon(self):
        here = os.path.dirname(os.path.abspath(__file__))
        fallback = os.path.join(here, "steam.png")

        path = (self.state.get("icon_path") or "").strip()
        if path and os.path.exists(path):
            use = path
        elif os.path.exists(fallback):
            use = fallback
        else:
            use = ""

        self.icon_pm = QPixmap(use) if use else QPixmap()
        self.icon_qicon = QIcon(use) if use else QIcon()
        if not self.icon_qicon.isNull():
            self.setWindowIcon(self.icon_qicon)
        self.update()

    def pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Icon", "", "Images (*.png *.ico *.jpg *.jpeg *.bmp)")
        if not path:
            return
        self.state["icon_path"] = path
        save_json(STATE_FILE, self.state)
        self._load_icon()
        if getattr(self, "tray", None):
            self.tray.setIcon(self._tray_icon())

    def _tray_icon(self) -> QIcon:
        if self.windowIcon() and not self.windowIcon().isNull():
            return self.windowIcon()
        return self.style().standardIcon(QStyle.SP_MessageBoxInformation)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._tray_icon())
        self.tray.show()

        tray_menu = QMenu()
        tray_menu.setStyleSheet(self._menu_css)
        tray_menu.addAction(QAction("Show / Hide", self, triggered=self.toggle_show_hide))
        tray_menu.addSeparator()
        tray_menu.addAction(QAction("Open Menu", self, triggered=lambda: self.menu(QCursor.pos())))
        tray_menu.addSeparator()
        tray_menu.addAction(QAction("Exit", self, triggered=QApplication.quit))
        self.tray.setContextMenu(tray_menu)

        self.tray.activated.connect(lambda r: self.toggle_show_hide() if r == QSystemTrayIcon.Trigger else None)

    def toggle_show_hide(self):
        self.setVisible(not self.isVisible())

    def _toast(self, msg: str):
        def f():
            self.setToolTip(f"rTools\n{msg}\nTool: {TARGET_NAME}")
            try:
                self.tray.showMessage("rTools", msg, QSystemTrayIcon.Information, 1500)
            except Exception:
                pass
        QTimer.singleShot(0, f)

    # ----- drag / click -----
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.menu(e.globalPos()); return
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.move(e.globalPos() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False
            self.state["pos"] = [self.x(), self.y()]
            save_json(STATE_FILE, self.state)

    # ----- DnD -----
    def dragEnterEvent(self, e):
        if not e.mimeData().hasUrls(): return
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if any(is_lua(f) or is_manifest(f) for f in files):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        self.import_from_paths(files)

    # ----- import -----
    def import_from_paths(self, paths):
        if not paths: return
        copied = 0; errs = []
        for p in paths:
            try:
                for fp in iter_files_limited(p, NESTED_MAX_DEPTH):
                    if is_lua(fp):
                        shutil.copy2(fp, os.path.join(self.stplugin, os.path.basename(fp))); copied += 1
                    elif is_manifest(fp):
                        shutil.copy2(fp, os.path.join(self.depotcache, os.path.basename(fp))); copied += 1
            except Exception as ex:
                errs.append(f"{p}: {ex}")
        self.refresh_games()
        self._toast(f"Imported {copied} file(s)")
        if errs:
            QMessageBox.warning(self, "Import errors", "\n".join(errs[:10]))

    # ----- steam account -----
    def get_current_steam_account(self) -> str:
        vdf = os.path.join(self.steam_path, "config", "loginusers.vdf")
        if not os.path.exists(vdf): return "Unknown"
        try:
            txt = open(vdf, "r", encoding="utf-8", errors="ignore").read()
            ids = re.findall(r'"(\d{5,})"\s*\{', txt)
            blocks = re.split(r'"\d{5,}"\s*\{', txt)
            for i, b in enumerate(blocks[1:]):
                if re.search(r'"MostRecent"\s*"1"', b):
                    persona = re.search(r'"PersonaName"\s*"([^"]+)"', b)
                    if persona: return persona.group(1)
                    return ids[i] if i < len(ids) else "Unknown"
        except Exception:
            pass
        return "Unknown"

    # ----- games -----
    def refresh_games(self):
        games = {}
        try:
            for fn in os.listdir(self.stplugin):
                fp = os.path.join(self.stplugin, fn)
                if fp.lower().endswith(".lua"):
                    aid = extract_appid_from_lua(fp)
                    if not aid: continue
                    g = games.setdefault(aid, {"name": "", "lua": []})
                    g["lua"].append(fp)
        except Exception:
            pass

        for aid in list(games.keys()):
            games[aid]["name"] = get_game_name(aid)

        self.games = games

        dlg = getattr(self, "search_dlg", None)
        if dlg and dlg.isVisible():
            items = [(meta["name"], aid) for aid, meta in self.games.items()]
            dlg.update_items(items)

    def _start_name_resolver(self):
        def worker():
            while True:
                unknown = []
                for aid, meta in self.games.items():
                    nm = meta.get("name") or ""
                    if nm.startswith("App "):
                        unknown.append(aid)
                if not unknown:
                    time.sleep(2.0)
                    continue

                for aid in unknown[:8]:
                    nm = get_game_name(aid)
                    if nm and not nm.startswith("App "):
                        self.games[aid]["name"] = nm
                        QTimer.singleShot(0, self.refresh_games)
                    time.sleep(0.25)
        threading.Thread(target=worker, daemon=True).start()

    def open_search(self):
        items = [(meta["name"], aid) for aid, meta in self.games.items()]
        self.search_dlg = GameSearchDialog(self, items)
        self.search_dlg.exec_()

    def open_game_actions(self, appid: str):
        meta = self.games.get(appid)
        if not meta: return
        name = meta.get("name") or f"App {appid}"
        mm = QMenu(); mm.setStyleSheet(self._menu_css)
        title = QAction(f"{name} ({appid})", self); title.setEnabled(False)
        mm.addAction(title); mm.addSeparator()
        mm.addAction(QAction("Show LUA files", self, triggered=lambda: self._show_lua_files(appid)))
        mm.addAction(QAction("Delete (LUA only)", self, triggered=lambda: self._remove_game(appid, False)))
        mm.addAction(QAction("Delete (LUA + manifests)", self, triggered=lambda: self._remove_game(appid, True)))
        mm.exec_(QCursor.pos())

    def _show_lua_files(self, appid: str):
        meta = self.games.get(appid)
        if not meta: return
        QMessageBox.information(self, "LUA files", "\n".join(meta.get("lua", [])) or "(none)")

    def _remove_game(self, appid: str, remove_manifests: bool):
        meta = self.games.get(appid)
        if not meta: return
        lua_files = list(meta.get("lua") or [])
        mani_files = []

        if remove_manifests:
            try:
                for fn in os.listdir(self.depotcache):
                    if fn.lower().endswith((".manifest", ".mfst")) and appid in fn:
                        mani_files.append(os.path.join(self.depotcache, fn))
            except Exception:
                pass

        files = lua_files + mani_files
        if not files:
            return

        name = meta.get("name") or f"App {appid}"
        if QMessageBox.question(
            self, "Confirm delete",
            f"Delete {len(files)} file(s) for:\n{name}\n\n" + ("(LUA + manifests)" if remove_manifests else "(LUA only)"),
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        errs = []
        for fp in files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception as e:
                errs.append(f"{os.path.basename(fp)}: {e}")

        self.refresh_games()
        if errs:
            QMessageBox.warning(self, "Delete errors", "\n".join(errs[:10]))

    # ----- steam actions -----
    def steam_exe(self):
        return os.path.join(self.steam_path, "Steam.exe")

    def launch_steam(self):
        exe = self.steam_exe()
        if not os.path.exists(exe):
            QMessageBox.warning(self, "Steam not found", f"Steam.exe not found:\n{exe}")
            return
        subprocess.Popen([exe], shell=False)

    def restart_steam(self):
        exe = self.steam_exe()
        if not os.path.exists(exe):
            QMessageBox.warning(self, "Steam not found", f"Steam.exe not found:\n{exe}")
            return
        subprocess.call(["taskkill", "/F", "/IM", "steam.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        subprocess.Popen([exe], shell=False)

    # ----- always on top -----
    def toggle_always_on_top(self, checked: bool):
        self.always_on_top = bool(checked)
        self.state["always_on_top"] = self.always_on_top
        save_json(STATE_FILE, self.state)
        geo = self.geometry()
        self._apply_window_flags()
        self.setGeometry(geo)
        self.show()

    # ----- tool finder (2s scan + fallback ProgramData) -----
    def _cached_tool_path(self) -> str:
        p = (self.state.get("tool_cached_path") or "").strip()
        return p if p and os.path.exists(p) else ""

    def _save_cached_tool_path(self, p: str):
        self.state["tool_cached_path"] = p
        save_json(STATE_FILE, self.state)

    def find_tool_fast(self, target_name: str) -> str | None:
        cached = self._cached_tool_path()
        if cached:
            return cached

        deadline = time.time() + 2.0

        candidates = []
        user = Path.home()
        for sub in ("Desktop", "Downloads", "Documents"):
            candidates.append(user / sub)

        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
            base = os.getenv(env)
            if base:
                candidates.append(Path(base))

        max_depth = 4
        for root in candidates:
            if time.time() > deadline:
                break
            try:
                if not root.exists():
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    if time.time() > deadline:
                        dirnames[:] = []
                        break
                    if target_name in filenames:
                        found = str(Path(dirpath) / target_name)
                        self._save_cached_tool_path(found)
                        return found
                    try:
                        rel = Path(dirpath).relative_to(root)
                        if len(rel.parts) >= max_depth:
                            dirnames[:] = []
                    except Exception:
                        pass
            except Exception:
                pass

        p = RTOOL_DIR / target_name
        if p.exists():
            self._save_cached_tool_path(str(p))
            return str(p)

        return None

    # ✅ UPDATED: run tool (embedded first, fallback to finder)
    def run_tool(self):
        self._toast("Starting tool...")
        def worker():
            # 1) embedded / dev local path
            path = get_tool_path_for_run(TARGET_NAME)

            # 2) fallback: old fast finder (bozulmasın diye)
            if not path:
                path = self.find_tool_fast(TARGET_NAME) or ""

            if not path:
                self._toast(f"Not found: {TARGET_NAME}")
                return

            try:
                os.startfile(path)
                self._toast("Tool started ✅")
            except Exception:
                try:
                    p = subprocess.Popen([path], cwd=str(Path(path).parent), shell=False)
                    time.sleep(1.0)
                    code = p.poll()
                    if code is not None:
                        self._toast(f"Tool exited (code {code})")
                    else:
                        self._toast("Tool started ✅")
                except Exception as ex:
                    self._toast(f"Failed: {ex}")
        threading.Thread(target=worker, daemon=True).start()

    # ----- steam folder picker -----
    def pick_steam(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Steam folder", self.steam_path)
        if not folder: return
        self.steam_path = folder
        self.state["steam_path"] = folder
        save_json(STATE_FILE, self.state)
        self.stplugin = os.path.join(self.steam_path, "config", "stplug-in")
        self.depotcache = os.path.join(self.steam_path, "depotcache")
        ensure_dir(self.stplugin); ensure_dir(self.depotcache)
        self.refresh_games()
        self._toast("Steam folder saved ✅")

    # ----- Explorer right-click import (registry) -----
    def enable_explorer_import(self):
        if not is_built_exe():
            QMessageBox.warning(self, "Build needed",
                "Explorer right-click için exe build şart (PyInstaller). .py ile olmaz.")
            return

        if not is_admin():
            ok = run_as_admin('--do-explorer-import=enable')
            if not ok:
                QMessageBox.warning(self, "Cancelled", "UAC iptal edildi.")
            return

        self._write_explorer_registry(enable=True)
        self._toast("Explorer import enabled ✅")

    def disable_explorer_import(self):
        if not is_built_exe():
            QMessageBox.warning(self, "Build needed",
                "Explorer right-click için exe build şart (PyInstaller). .py ile olmaz.")
            return

        if not is_admin():
            ok = run_as_admin('--do-explorer-import=disable')
            if not ok:
                QMessageBox.warning(self, "Cancelled", "UAC iptal edildi.")
            return

        self._write_explorer_registry(enable=False)
        self._toast("Explorer import disabled ✅")

    def _write_explorer_registry(self, enable: bool):
        exe = current_app_exe_path()
        if enable:
            reg_text = rf"""
Windows Registry Editor Version 5.00

[HKEY_CLASSES_ROOT\*\shell\rToolImport]
@="Import to rTool"
"Icon"="{exe}"

[HKEY_CLASSES_ROOT\*\shell\rToolImport\command]
@="\"{exe}\" \"%1\""

[HKEY_CLASSES_ROOT\Directory\shell\rToolImport]
@="Import to rTool"
"Icon"="{exe}"

[HKEY_CLASSES_ROOT\Directory\shell\rToolImport\command]
@="\"{exe}\" \"%1\""
""".strip()

            tmp = os.path.join(os.getenv("TEMP", "."), "rtool_import.reg")
            with open(tmp, "w", encoding="utf-16") as f:
                f.write(reg_text)
            subprocess.run(["regedit", "/s", tmp], capture_output=True, text=True)
        else:
            subprocess.run(["cmd", "/c", r'reg delete "HKEY_CLASSES_ROOT\*\shell\rToolImport" /f'], capture_output=True, text=True)
            subprocess.run(["cmd", "/c", r'reg delete "HKEY_CLASSES_ROOT\Directory\shell\rToolImport" /f'], capture_output=True, text=True)

    # ----- menu -----
    def menu(self, pos):
        m = QMenu(); m.setStyleSheet(self._menu_css)

        acc = self.get_current_steam_account()
        a_acc = QAction(f"Account: {acc}", self); a_acc.setEnabled(False)
        m.addAction(a_acc); m.addSeparator()

        m.addAction(QAction("Launch Steam", self, triggered=self.launch_steam))
        m.addAction(QAction("Restart Steam", self, triggered=self.restart_steam))
        m.addSeparator()

        m.addAction(QAction("Open Steam Folder", self, triggered=lambda: open_path(self.steam_path)))
        m.addAction(QAction("Open stplug-in Folder", self, triggered=lambda: open_path(self.stplugin)))
        m.addAction(QAction("Open depotcache Folder", self, triggered=lambda: open_path(self.depotcache)))
        m.addAction(QAction("Open rTool", self, triggered=lambda: open_path(str(RTOOL_DIR))))
        m.addSeparator()

        m.addAction(QAction("Enable Explorer Import (Right-Click)", self, triggered=self.enable_explorer_import))
        m.addAction(QAction("Disable Explorer Import", self, triggered=self.disable_explorer_import))
        m.addSeparator()

        games_menu = m.addMenu(f"Games ({len(self.games)})"); games_menu.setStyleSheet(self._menu_css)
        games_menu.addAction(QAction("Refresh", self, triggered=self.refresh_games))
        games_menu.addAction(QAction("Search…", self, triggered=self.open_search))
        m.addSeparator()

        m.addAction(QAction(f"Run Tool ({TARGET_NAME})", self, triggered=self.run_tool))
        m.addSeparator()

        m.addAction(QAction("Select Icon…", self, triggered=self.pick_icon))
        a_top = QAction("Always on top", self); a_top.setCheckable(True); a_top.setChecked(self.always_on_top)
        a_top.toggled.connect(self.toggle_always_on_top)
        m.addAction(a_top)

        m.addSeparator()
        m.addAction(QAction("Set Steam Folder…", self, triggered=self.pick_steam))
        m.addAction(QAction("Hide to tray", self, triggered=self.hide))
        m.addAction(QAction("Exit", self, triggered=QApplication.quit))
        m.exec_(pos)

# ---------- MAIN ----------
def handle_admin_task_if_any():
    for a in sys.argv[1:]:
        if a.startswith("--do-explorer-import="):
            mode = a.split("=", 1)[1].strip().lower()
            if not is_admin():
                return True
            dummy = MiniIcon()
            if mode == "enable":
                dummy._write_explorer_registry(enable=True)
            elif mode == "disable":
                dummy._write_explorer_registry(enable=False)
            return True
    return False

def main():
    if handle_admin_task_if_any():
        return

    app = QApplication(sys.argv)
    w = MiniIcon()
    w.show()

    args = [a for a in sys.argv[1:] if not a.startswith("--do-explorer-import=")]
    if args:
        QTimer.singleShot(250, lambda: w.import_from_paths(args))

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
