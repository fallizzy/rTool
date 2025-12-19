# -*- coding: utf-8 -*-
import os, sys, json, shutil, subprocess, time, re, threading, tempfile
import winreg
from pathlib import Path
from urllib import request

from PyQt5.QtCore import Qt, QPoint, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QPixmap, QIcon, QCursor, QPainter, QColor, QPen, QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMenu, QAction, QFileDialog, QMessageBox,
    QSystemTrayIcon, QStyle, QDialog, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton
)

# =========================
#   AUTO UPDATE (GitHub)
# =========================
OWNER = "fallizzy"
REPO = "rTool"
APP_VERSION = "1.0.2"

def ver_tuple(v: str):
    """Turn version like 1.2.3 or v1.2 into (1,2,3). Missing parts -> 0."""
    nums = re.findall(r"\d+", v or "")
    nums = (nums + ["0", "0", "0"])[:3]
    return tuple(int(x) for x in nums)



def _http_json(url: str):
    req = request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{REPO}-updater"
    })
    with request.urlopen(req, timeout=12) as r:
        return json.load(r)


def get_latest_release_info():
    api = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
    data = _http_json(api)

    tag = (data.get("tag_name") or "").strip()
    latest = tag.lstrip("v").strip()
    body = (data.get("body") or "").strip()

    assets = data.get("assets") or []
    setup = None
    for a in assets:
        name = (a.get("name") or "").lower()
        if name.endswith(".exe") and ("setup" in name or "installer" in name or "rtool" in name):
            setup = a
            break
    if not setup:
        for a in assets:
            name = (a.get("name") or "").lower()
            if name.endswith(".exe"):
                setup = a
                break

    url = setup.get("browser_download_url") if setup else ""
    setup_name = setup.get("name") if setup else ""

    return {
        "latest": latest,
        "tag": tag,
        "body": body,
        "setup_url": url,
        "setup_name": setup_name
    }


def download_and_run_setup(url: str, filename_hint: str = "rTool-Setup.exe"):
    def _download_thread():
        try:
            out = os.path.join(tempfile.gettempdir(), filename_hint or "rTool-Setup.exe")
            request.urlretrieve(url, out)
            subprocess.Popen([out], shell=False)
        except Exception:
            pass

    t = threading.Thread(target=_download_thread, daemon=True)
    t.start()


# =========================
#   UPDATE SIGNALS
# =========================
class UpdateSignals(QObject):
    update_checked = pyqtSignal(bool, str, object)  # Manual check
    update_found_auto = pyqtSignal(object)  # Startup auto check


# =========================
#   REGISTRY MANAGER
# =========================
class RegistryManager:
    KEY_NAME = "rToolImport"
    MENU_TITLE = "Import to rTool"

    LOCATIONS = [
        r"Software\Classes\*\shell",
        r"Software\Classes\Directory\shell",
        r"Software\Classes\Folder\shell"
    ]

    def add_context_menu(self):
        exe_path = sys.executable.replace('/', '\\')
        if not exe_path.lower().endswith(".exe"):
            return False, "This feature requires the compiled .exe version."

        command_val = f'"{exe_path}" "%1"'
        icon_val = exe_path

        try:
            for loc in self.LOCATIONS:
                key_path = f"{loc}\\{self.KEY_NAME}"
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, self.MENU_TITLE)
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_val)

                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command") as cmd_key:
                    winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command_val)
            return True, "Right-click menu added successfully!"
        except Exception as e:
            return False, f"Error: {e}"

    def remove_context_menu(self):
        try:
            for loc in self.LOCATIONS:
                key_path = f"{loc}\\{self.KEY_NAME}"
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command")
                except FileNotFoundError:
                    pass
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                except FileNotFoundError:
                    pass
            return True, "Right-click menu removed."
        except Exception as e:
            return False, f"Error: {e}"


# =========================
#   APP CONFIG
# =========================
STEAM_DEFAULT = r"C:\Program Files (x86)\Steam"
TARGET_NAME = "spprt.exe"

RTOOL_DIR = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "rTool"
RTOOL_DIR.mkdir(parents=True, exist_ok=True)

APPDATA_DIR = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "rTool"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = APPDATA_DIR / "state.json"
NAME_CACHE_FILE = APPDATA_DIR / "name_cache.json"
NESTED_MAX_DEPTH = 6


# =========================
#   HELPERS
# =========================
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


def get_tool_path_for_run(target_name: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        src = base / target_name
        if not src.exists():
            src = base / "bin" / target_name
        if not src.exists():
            return ""
        out_dir = RTOOL_DIR / "cache"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / target_name
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass
        return str(dst)
    exe = Path(__file__).parent / target_name
    if exe.exists():
        return str(exe)
    return ""


_NAME_CACHE = load_json(NAME_CACHE_FILE, {})


def req_json(url: str, timeout=2):
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get_game_name(appid: str) -> str:
    if not appid: return ""
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
    return f"App {appid}"


# =========================
#   SEARCH DIALOG
# =========================
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
        self.edit.setPlaceholderText("Type to filter...")
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)

        row = QHBoxLayout()
        self.btn_open = QPushButton("Actions")
        self.btn_close = QPushButton("Close")
        row.addWidget(self.btn_open)
        row.addStretch(1)
        row.addWidget(self.btn_close)

        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

        self.setStyleSheet("""
            QDialog { background: #0f0f0f; color: #f2f2f2; }
            QLineEdit { background: #151515; border: 1px solid #2a2a2a; border-radius: 10px; padding: 10px; color: #f2f2f2; }
            QListWidget { background: #111111; border: 1px solid #2a2a2a; border-radius: 12px; color: #f2f2f2; }
            QListWidget::item { padding: 10px; border-radius: 10px; color: #f2f2f2; }
            QListWidget::item:selected { background: #1f1f1f; color: #ffffff; }
            QPushButton { background: #151515; border: 1px solid #2a2a2a; border-radius: 10px; padding: 10px 14px; color: #f2f2f2; }
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
        if not it: return
        aid = it.data(Qt.UserRole)
        self.accept()
        self.parent().open_game_actions(aid)

    def _ctx_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it: return
        aid = it.data(Qt.UserRole)
        m = QMenu(self)
        a1 = QAction("Delete (LUA only)", self)
        a2 = QAction("Delete (LUA + manifests)", self)
        a1.triggered.connect(lambda: self.parent()._remove_game(aid, False))
        a2.triggered.connect(lambda: self.parent()._remove_game(aid, True))
        m.addAction(a1);
        m.addAction(a2)
        m.exec_(self.list.mapToGlobal(pos))


# =========================
#   MAIN WIDGET
# =========================
class MiniIcon(QWidget):
    def __init__(self):
        super().__init__()

        self.state = load_json(STATE_FILE, {
            "steam_path": STEAM_DEFAULT,
            "pos": [60, 180],
            "always_on_top": True
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

        # ICONS LOAD
        self.icon_pm = QPixmap()
        self.default_face_pm = QPixmap()
        self.tray_icon = QIcon()
        self._load_app_icons()

        x, y = self.state.get("pos", [60, 180])
        self.move(int(x), int(y))

        # Updates
        self.update_info = None
        self.update_available = False
        self.update_signals = UpdateSignals()
        self.update_signals.update_checked.connect(self._on_update_checked)
        self.update_signals.update_found_auto.connect(self._on_auto_update_found)

        self.reg_mgr = RegistryManager()
        self.games = {}
        self.search_dlg = None

        self._setup_tray()
        self.refresh_games()
        self._update_hover_text()
        self._start_name_resolver()

        # Startup update check
        QTimer.singleShot(1500, self.check_updates_silent)

    # ================== ICON LOGIC ==================
    def _load_app_icons(self):
        # Paths to look for icons
        here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

        # 1. Main Window/Tray Icon (ICO)
        ico_path = here / "steam.ico"
        if ico_path.exists():
            self.tray_icon = QIcon(str(ico_path))
            self.setWindowIcon(self.tray_icon)

        # 2. Default Widget Face (PNG) - The "r" logo
        face_path = here / "default.png"
        if face_path.exists():
            self.default_face_pm = QPixmap(str(face_path))

    # ================== UPDATE LOGIC ==================
    def check_updates_silent(self):
        def worker():
            try:
                info = get_latest_release_info()
                latest = info.get("latest") or ""
                if latest and ver_tuple(latest) > ver_tuple(APP_VERSION):
                    self.update_signals.update_found_auto.emit(info)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_update_found(self, info):
        self.update_info = info
        self.update_available = True

        # Update Menu Text
        if hasattr(self, "_action_check_updates"):
            self._action_check_updates.setText("Check Updates (!)")

        self._toast(f"Update available: v{info.get('latest')}")

        # POPUP ON STARTUP
        latest = info.get("latest", "")
        body = info.get("body", "").strip() or "(No changelog)"
        url = info.get("setup_url", "")
        setup_name = info.get("setup_name", "rTool-Setup.exe")

        text = f"New version available: v{latest}\n\nChangelog:\n{body}\n\nDo you want to download and install now?"
        res = QMessageBox.question(self, "Update Available", text, QMessageBox.Yes | QMessageBox.No)

        if res == QMessageBox.Yes:
            if url:
                self._toast("Downloading in background...")
                download_and_run_setup(url, setup_name)

    def on_check_updates(self):
        self._toast("Checking...")

        def worker():
            success = False
            msg = ""
            info = None
            try:
                info = get_latest_release_info()
                latest = info.get("latest") or ""
                if not latest:
                    success = False;
                    msg = "No release found."
                elif ver_tuple(latest) <= ver_tuple(APP_VERSION):
                    success = True
                    msg = f"Up to date: v{APP_VERSION}"
                else:
                    success = True
                    msg = f"Update available: v{latest}"
                    info["available"] = True
            except Exception as e:
                success = False;
                msg = f"Error: {e}"
            self.update_signals.update_checked.emit(success, msg, info)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, success, msg, info):
        self._toast(msg)
        if success and info and info.get('available'):
            self.update_info = info
            self.update_available = True
            if hasattr(self, "_action_check_updates"):
                self._action_check_updates.setText("Check Updates (!)")

            # Ask to download
            latest = info.get("latest", "")
            if QMessageBox.question(self, "Update", f"Update available: v{latest}\nDownload now?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                url = info.get("setup_url", "")
                name = info.get("setup_name", "rTool-Setup.exe")
                if url:
                    self._toast("Downloading...")
                    download_and_run_setup(url, name)
        elif success and not self.update_available:
            QMessageBox.information(self, "Update", "You are using the latest version.")

    def show_changelog(self):
        if not self.update_info:
            QMessageBox.information(self, "Changelog", "No info yet.")
            return
        latest = self.update_info.get("latest", "")
        body = self.update_info.get("body", "").strip()
        QMessageBox.information(self, "Changelog", f"Latest: v{latest}\n\n{body}")

    # ================== WINDOW PAINT ==================
    def _apply_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _update_hover_text(self):
        tip = f"rTool\nVersion: v{APP_VERSION}"
        if self.update_available and self.update_info:
            tip += f"\nUpdate: v{self.update_info.get('latest', '')}"
        self.setToolTip(tip)
        if getattr(self, "tray", None):
            self.tray.setToolTip(tip)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)
        bg = QColor(10, 10, 10, 170)
        border = QColor(80, 80, 80, 160)
        p.setBrush(bg)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(rect, 14, 14)

        # 1. Game Icon?
        if not self.icon_pm.isNull():
            pm = self.icon_pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - pm.width()) // 2
            y = (self.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        # 2. Default Face Icon (The 'r' logo)?
        elif not self.default_face_pm.isNull():
            pm = self.default_face_pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - pm.width()) // 2
            y = (self.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        # 3. Fallback Text
        else:
            p.setPen(QColor(242, 242, 242, 230))
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.drawText(self.rect(), Qt.AlignCenter, "r")
        p.end()

    # ================== TRAY & MENU ==================
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        if not self.tray_icon.isNull():
            self.tray.setIcon(self.tray_icon)
        else:
            self.tray.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxInformation))
        self.tray.show()

        tray_menu = QMenu()
        tray_menu.setStyleSheet(self._menu_css)
        tray_menu.addAction(QAction("Show / Hide", self, triggered=self.toggle_show_hide))
        tray_menu.addSeparator()

        self._action_check_updates = QAction("Check Updates", self, triggered=self.on_check_updates)
        tray_menu.addAction(self._action_check_updates)
        tray_menu.addAction(QAction("Show Changelog", self, triggered=self.show_changelog))
        tray_menu.addSeparator()
        tray_menu.addAction(QAction("Exit", self, triggered=QApplication.quit))

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(lambda r: self.toggle_show_hide() if r == QSystemTrayIcon.Trigger else None)

    def toggle_show_hide(self):
        self.setVisible(not self.isVisible())

    def _toast(self, msg: str):
        def f():
            self.setToolTip(f"rTool\n{msg}")
            try:
                self.tray.showMessage("rTool", msg, QSystemTrayIcon.Information, 1500)
            except Exception:
                pass

        QTimer.singleShot(0, f)

    def menu(self, pos):
        m = QMenu();
        m.setStyleSheet(self._menu_css)

        # Header
        head_txt = f"Version: v{APP_VERSION}"
        if self.update_available and self.update_info:
            head_txt = f"Update available: v{self.update_info.get('latest', '')}"
        head = QAction(head_txt, self)
        head.setEnabled(False)
        m.addAction(head)
        m.addSeparator()

        # Update Actions
        chk_txt = "Check Updates (!)" if self.update_available else "Check Updates"
        self._action_check_updates = QAction(chk_txt, self, triggered=self.on_check_updates)
        m.addAction(self._action_check_updates)
        m.addAction(QAction("Show Changelog", self, triggered=self.show_changelog))
        m.addSeparator()

        # Registry
        m.addAction(QAction("Register Contex Menu", self, triggered=self.add_right_click))
        m.addAction(QAction("Unregister Contex Menu", self, triggered=self.remove_right_click))
        m.addSeparator()

        # Steam
        m.addAction(QAction("Launch Steam", self, triggered=self.launch_steam))
        m.addAction(QAction("Restart Steam", self, triggered=self.restart_steam))
        m.addSeparator()

        # Folders
        folders_menu = m.addMenu("Folders");
        folders_menu.setStyleSheet(self._menu_css)
        folders_menu.addAction(QAction("Open Steam Folder", self, triggered=lambda: open_path(self.steam_path)))
        folders_menu.addAction(QAction("Open stplug-in Folder", self, triggered=lambda: open_path(self.stplugin)))
        folders_menu.addAction(QAction("Open depotcache Folder", self, triggered=lambda: open_path(self.depotcache)))
        folders_menu.addAction(QAction("Open rTool Folder", self, triggered=lambda: open_path(str(RTOOL_DIR))))
        m.addSeparator()

        # Games
        games_menu = m.addMenu(f"Games ({len(self.games)})");
        games_menu.setStyleSheet(self._menu_css)
        games_menu.addAction(QAction("Refresh", self, triggered=self.refresh_games))
        games_menu.addAction(QAction("Search...", self, triggered=self.open_search))
        m.addSeparator()

        # Run Tool - Cleaned text
        m.addAction(QAction("Run Tool", self, triggered=self.run_tool))
        m.addSeparator()

        m.addAction(QAction("Set Steam Folder...", self, triggered=self.pick_steam))
        m.addAction(QAction("Hide to tray", self, triggered=self.hide))
        m.addAction(QAction("Exit", self, triggered=QApplication.quit))
        m.exec_(pos)

    # ================== MOUSE & DRAG ==================
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.menu(e.globalPos());
            return
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.move(e.globalPos() - self._drag_offset);
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False
            self.state["pos"] = [self.x(), self.y()]
            save_json(STATE_FILE, self.state)

    def dragEnterEvent(self, e):
        if not e.mimeData().hasUrls(): return
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if any(is_lua(f) or is_manifest(f) for f in files): e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        self.import_from_paths(files)

    # ================== ACTIONS ==================
    def import_from_paths(self, paths):
        if not paths: return
        copied = 0;
        errs = []
        for p in paths:
            try:
                for fp in iter_files_limited(p, NESTED_MAX_DEPTH):
                    if is_lua(fp):
                        shutil.copy2(fp, os.path.join(self.stplugin, os.path.basename(fp)))
                        copied += 1
                    elif is_manifest(fp):
                        shutil.copy2(fp, os.path.join(self.depotcache, os.path.basename(fp)))
                        copied += 1
            except Exception as ex:
                errs.append(f"{p}: {ex}")
        self.refresh_games()
        self._toast(f"Imported {copied} file(s)")
        if errs: QMessageBox.warning(self, "Import Errors", "\n".join(errs[:10]))

    def refresh_games(self):
        games = {}
        try:
            if not os.path.isdir(self.stplugin): ensure_dir(self.stplugin)
            for fn in os.listdir(self.stplugin):
                fp = os.path.join(self.stplugin, fn)
                if fp.lower().endswith(".lua") and os.path.isfile(fp):
                    aid = extract_appid_from_lua(fp)
                    if not aid: continue
                    g = games.setdefault(aid, {"name": (self.games.get(aid, {}).get("name") if getattr(self, "games", None) else "") or f"App {aid}", "lua": []})
                    g["lua"].append(fp)
        except Exception:
            pass
        self.games = games
        dlg = getattr(self, "search_dlg", None)
        if dlg and hasattr(dlg, "update_items") and dlg.isVisible():
            items = [(meta.get("name") or f"App {aid}", aid) for aid, meta in self.games.items()]
            try:
                dlg.update_items(items)
            except Exception:
                pass

    def _start_name_resolver(self):
        def worker():
            while True:
                unknown = []
                for aid, meta in self.games.items():
                    nm = meta.get("name") or ""
                    if nm.startswith("App "): unknown.append(aid)
                if not unknown:
                    time.sleep(2.0);
                    continue
                for aid in unknown[:8]:
                    nm = get_game_name(aid)
                    if nm and not nm.startswith("App "):
                        self.games[aid]["name"] = nm
                        def update_ui():
                            dlg = getattr(self, "search_dlg", None)
                            if dlg and hasattr(dlg, "update_items") and dlg.isVisible():
                                items = [(meta.get("name") or f"App {aid}", aid) for aid, meta in self.games.items()]
                                try:
                                    dlg.update_items(items)
                                except Exception:
                                    pass

                        QTimer.singleShot(0, update_ui)
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
        mm = QMenu();
        mm.setStyleSheet(self._menu_css)
        mm.addAction(QAction(f"{name} ({appid})", self, enabled=False))
        mm.addSeparator()
        mm.addAction(QAction("Show LUA files", self, triggered=lambda: self._show_lua_files(appid)))
        mm.addAction(QAction("Delete (LUA only)", self, triggered=lambda: self._remove_game(appid, False)))
        mm.addAction(QAction("Delete (LUA + manifests)", self, triggered=lambda: self._remove_game(appid, True)))
        mm.exec_(QCursor.pos())

    def _show_lua_files(self, appid: str):
        meta = self.games.get(appid)
        if not meta: return
        QMessageBox.information(self, "LUA Files", "\n".join(meta.get("lua", [])) or "(none)")

    def _remove_game(self, appid: str, remove_manifests: bool):
        meta = self.games.get(appid)
        if not meta: return
        files = list(meta.get("lua") or [])
        if remove_manifests:
            try:
                for fn in os.listdir(self.depotcache):
                    if fn.lower().endswith((".manifest", ".mfst")) and appid in fn:
                        files.append(os.path.join(self.depotcache, fn))
            except Exception:
                pass
        if not files: return
        if QMessageBox.question(self, "Confirm Delete", f"Delete {len(files)} file(s)?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        for fp in files:
            try:
                if os.path.exists(fp): os.remove(fp)
            except Exception:
                pass
        self.refresh_games()

    def run_tool(self):
        self._toast("Starting tool...")

        def worker():
            path = get_tool_path_for_run(TARGET_NAME)
            if not path:
                self._toast(f"Not found: {TARGET_NAME}")
                return
            try:
                os.startfile(path); self._toast("Tool started")
            except Exception as ex:
                self._toast(f"Failed: {ex}")

        threading.Thread(target=worker, daemon=True).start()

    def launch_steam(self):
        exe = os.path.join(self.steam_path, "Steam.exe")
        if os.path.exists(exe):
            subprocess.Popen([exe], shell=False)
        else:
            QMessageBox.warning(self, "Error", f"Steam not found:\n{exe}")

    def restart_steam(self):
        exe = os.path.join(self.steam_path, "Steam.exe")
        if not os.path.exists(exe):
            QMessageBox.warning(self, "Error", f"Steam not found:\n{exe}")
            return
        subprocess.call(["taskkill", "/F", "/IM", "steam.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        subprocess.Popen([exe], shell=False)

    def pick_steam(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Steam Folder", self.steam_path)
        if not folder: return
        self.steam_path = folder
        self.state["steam_path"] = folder
        save_json(STATE_FILE, self.state)
        self.stplugin = os.path.join(self.steam_path, "config", "stplug-in")
        self.depotcache = os.path.join(self.steam_path, "depotcache")
        ensure_dir(self.stplugin);
        ensure_dir(self.depotcache)
        self.refresh_games()
        self._toast("Steam path saved")

    def add_right_click(self):
        success, msg = self.reg_mgr.add_context_menu()
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Error", msg)

    def remove_right_click(self):
        success, msg = self.reg_mgr.remove_context_menu()
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Error", msg)


# =========================
#   MAIN
# =========================
def main():
    app = QApplication(sys.argv)
    w = MiniIcon()
    w.show()

    args = [a for a in sys.argv[1:]]
    if args:
        w.import_from_paths(args)
        QTimer.singleShot(2500, QApplication.quit)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()