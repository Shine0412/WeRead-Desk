# -*- coding: utf-8 -*-
"""WeRead Desk v0.3 — 微信读书桌面壳

纯「壳」应用：用系统 WebView2 加载微信读书网页版（weread.qq.com），
不修改、不抓取页面任何内容。登录态由 WebView2 用户数据目录持久化。

v0.3：设置面板（自定义全局快捷键、最小化/关闭到托盘、启动最小化、开机自启）。
"""

import ctypes
import json
import os
import sys
import threading
import winreg
from ctypes import wintypes
from pathlib import Path

import webview

try:
    import pystray
    from PIL import Image

    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

APP_NAME = "WeReadDesk"
APP_TITLE = "WeRead Desk - 微信读书"
SETTINGS_TITLE = "WeRead Desk 设置"
HOME_URL = "https://weread.qq.com"
MUTEX_NAME = "Local\\WeReadDesk_SingleInstance"

BASE_DIR = Path(__file__).resolve().parent.parent


def resource_path(rel):
    """定位随包资源：源码运行取项目根目录，PyInstaller 单文件运行取解包目录。"""
    base = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    return base / rel


ICON_PATH = resource_path(Path("assets") / "icon.ico")
TRAY_PNG_PATH = resource_path(Path("assets") / "icon.png")
SETTINGS_HTML = resource_path(Path("src") / "settings.html")

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = CONFIG_DIR / "webview-data"  # WebView2 用户数据（登录态/Cookie）

DEFAULT_GEOMETRY = {"width": 1200, "height": 800, "x": None, "y": None}
MIN_SIZE = (860, 600)

DEFAULT_SETTINGS = {
    "hotkey_enabled": True,
    "hotkey": "ctrl+alt+w",      # 至少一个修饰键 + 一个主键
    "minimize_to_tray": True,    # 最小化按钮 → 托盘
    "close_to_tray": False,      # 关闭按钮 → 托盘（退出走托盘菜单）
    "start_minimized": False,    # 启动时最小化到托盘
    "autostart": False,          # 开机自启（HKCU Run）
}

WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
WM_SET_HOTKEY = 0x8001  # 自定义线程消息：wParam=修饰键（0=注销），lParam=主键 VK
SW_HIDE, SW_SHOW, SW_RESTORE = 0, 5, 9
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
HOTKEY_ID = 0xB001

MOD_NAMES = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
SPECIAL_KEYS = {
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "enter": 0x0D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "backspace": 0x08,
}
for _i in range(1, 13):
    SPECIAL_KEYS[f"f{_i}"] = 0x70 + _i - 1

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ---------------- 运行时全局状态 ----------------
_icon_applied = False
_force_quit = False
_hotkey_tid = None
_settings_window = None


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_settings():
    """读取设置，缺项补默认值。"""
    saved = load_config().get("settings") or {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_config(patch):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = load_config()
        config.update(patch)
        CONFIG_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception:
        return False


def save_geometry(geom):
    save_config({"geometry": geom})


def clamp_to_screen(geom):
    """显示器布局变化后，避免窗口恢复到虚拟屏幕外的位置。

    使用虚拟屏幕（多显示器整体）而非主屏，左/上侧副屏坐标为负值。
    """
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    try:
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(SM_XVIRTUALSCREEN), u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw, vh = u.GetSystemMetrics(SM_CXVIRTUALSCREEN), u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        x, y = geom.get("x"), geom.get("y")
        if x is not None and (x + geom["width"] < vx + 50 or x > vx + vw - 50):
            geom["x"] = None
        if y is not None and (y + geom["height"] < vy + 50 or y > vy + vh - 50):
            geom["y"] = None
    except Exception:
        geom["x"] = geom["y"] = None
    return geom


def parse_combo(combo):
    """'ctrl+alt+w' -> (mods, vk)；非法输入返回 None。"""
    try:
        mods, key = 0, None
        for part in str(combo).lower().split("+"):
            part = part.strip()
            if not part:
                return None
            if part in MOD_NAMES:
                mods |= MOD_NAMES[part]
            elif key is None:
                key = part
            else:
                return None
        if key is None or mods == 0:
            return None  # 必须包含主键且至少一个修饰键
        if len(key) == 1 and key.isalnum():
            vk = ord(key.upper())
        elif key in SPECIAL_KEYS:
            vk = SPECIAL_KEYS[key]
        else:
            return None
        return mods, vk
    except Exception:
        return None


# ---------------- 窗口与托盘 ----------------

def _app_hwnd():
    return ctypes.windll.user32.FindWindowW(None, APP_TITLE)


def toggle_window(*_):
    """显示/隐藏主窗口（纯 Win32 调用，任意线程可安全使用）。"""
    u = ctypes.windll.user32
    hwnd = _app_hwnd()
    if not hwnd:
        return
    if u.IsWindowVisible(hwnd):
        u.ShowWindow(hwnd, SW_HIDE)
    elif u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
        u.SetForegroundWindow(hwnd)
    else:
        u.ShowWindow(hwnd, SW_SHOW)
        u.SetForegroundWindow(hwnd)


def close_window(*_):
    """退出应用：走正常关闭流程（等同托盘菜单「退出」）。"""
    global _force_quit
    _force_quit = True
    hwnd = _app_hwnd()
    if hwnd:
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def apply_window_icon():
    """把应用图标设置到窗口标题栏与任务栏（源码运行时覆盖 python.exe 默认图标）。

    用 LoadImageW 按目标尺寸从 ico 中取最接近的图像；ExtractIconExW 对
    多尺寸 PNG 压缩 ico 的分次取图不可靠，已弃用。loaded 事件每次导航都会
    触发，只做一次。
    """
    global _icon_applied
    if _icon_applied:
        return
    try:
        if not ICON_PATH.exists():
            return
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            return

        LR_LOADFROMFILE = 0x10
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        icon_file = str(ICON_PATH)
        # 64px：高缩放下任务栏图标（32 logical * DPI）更清晰
        big = user32.LoadImageW(None, icon_file, 1, 64, 64, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, icon_file, 1, 16, 16, LR_LOADFROMFILE)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        _icon_applied = True
    except Exception:
        pass  # 图标设置失败不影响使用


def setup_tray(on_settings):
    """启动系统托盘图标：左键切换显隐，菜单含设置与退出。"""
    if not HAS_TRAY or not TRAY_PNG_PATH.exists():
        return None
    try:
        image = Image.open(TRAY_PNG_PATH)
        menu = pystray.Menu(
            pystray.MenuItem("显示/隐藏窗口", toggle_window, default=True),
            pystray.MenuItem("设置…", lambda icon, _: on_settings()),
            pystray.MenuItem("退出", lambda icon, _: (close_window(), icon.stop())),
        )
        icon = pystray.Icon(APP_NAME, image, APP_TITLE, menu)
        threading.Thread(target=icon.run, daemon=True).start()
        return icon
    except Exception:
        return None


# ---------------- 全局热键 ----------------

def hotkey_loop():
    """后台线程：注册并响应全局热键；支持通过 WM_SET_HOTKEY 消息动态改键。

    RegisterHotKey 的 WM_HOTKEY 发往注册线程的消息队列，
    因此在该线程内自建 GetMessage 循环。
    """
    global _hotkey_tid
    u = ctypes.windll.user32
    _hotkey_tid = ctypes.windll.kernel32.GetCurrentThreadId()

    def register_from_settings():
        parsed = None
        if get_settings().get("hotkey_enabled"):
            parsed = parse_combo(get_settings().get("hotkey", ""))
        u.UnregisterHotKey(None, HOTKEY_ID)
        if parsed:
            u.RegisterHotKey(None, HOTKEY_ID, parsed[0], parsed[1])

    register_from_settings()

    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            toggle_window()
        elif msg.message == WM_SET_HOTKEY:
            # wParam=修饰键（0 表示注销），lParam=主键
            u.UnregisterHotKey(None, HOTKEY_ID)
            if msg.wParam:
                u.RegisterHotKey(None, HOTKEY_ID, msg.wParam, msg.lParam)


def update_hotkey_from_settings():
    """按当前设置重设热键；线程未起或已退出时静默跳过。"""
    u = ctypes.windll.user32
    u.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    parsed = parse_combo(get_settings().get("hotkey", ""))
    enabled = bool(get_settings().get("hotkey_enabled")) and parsed is not None
    if _hotkey_tid:
        u.PostThreadMessageW(
            _hotkey_tid, WM_SET_HOTKEY,
            parsed[0] if enabled else 0, parsed[1] if parsed else 0,
        )


# ---------------- 开机自启 ----------------

def set_autostart(enable):
    """HKCU Run 键；打包模式指向 exe，源码模式指向 venv python + app.py。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if not enable:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
                return True
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                cmd = f'"{sys.executable}" "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        return True
    except Exception:
        return False


# ---------------- 设置面板 ----------------

class SettingsApi:
    """设置页 JS 可调用的 Python 接口（pywebview js_api）。"""

    def get_settings(self):
        return get_settings()

    def save_settings(self, incoming):
        try:
            merged = get_settings()
            for k in DEFAULT_SETTINGS:
                if k in incoming:
                    merged[k] = incoming[k]
            # 校验快捷键
            merged["hotkey_enabled"] = bool(merged["hotkey_enabled"])
            if parse_combo(merged.get("hotkey", "")) is None:
                return "快捷键格式无效，未保存"
            for k in ("minimize_to_tray", "close_to_tray", "start_minimized", "autostart"):
                merged[k] = bool(merged[k])
            if not save_config({"settings": merged}):
                return "写入配置失败"
            set_autostart(merged["autostart"])
            update_hotkey_from_settings()
            return "已保存，立即生效"
        except Exception:
            return "保存出错"

    def close(self):
        hwnd = ctypes.windll.user32.FindWindowW(None, SETTINGS_TITLE)
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def open_settings():
    """打开或激活设置窗口。"""
    global _settings_window
    u = ctypes.windll.user32
    existing = u.FindWindowW(None, SETTINGS_TITLE)
    if existing:
        u.ShowWindow(existing, SW_RESTORE)
        u.SetForegroundWindow(existing)
        return
    if not SETTINGS_HTML.exists():
        return
    window = webview.create_window(
        SETTINGS_TITLE,
        url=SETTINGS_HTML.resolve().as_uri(),
        js_api=SettingsApi(),
        width=500,
        height=640,
        resizable=False,
    )
    window.events.closed += lambda *_: globals().__setitem__("_settings_window", None)
    _settings_window = window


# ---------------- 单实例 ----------------

class SingleInstance:
    """Windows 命名互斥量实现单实例。"""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self):
        self._handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.already_running = (
            ctypes.windll.kernel32.GetLastError() == self.ERROR_ALREADY_EXISTS
        )

    def activate_existing(self):
        """尝试把已运行实例的窗口唤到前台；找不到则提示用户。"""
        hwnd = _app_hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        else:
            ctypes.windll.user32.MessageBoxW(
                0, "WeRead Desk 已在运行。", APP_TITLE, 0x40
            )


def main():
    single = SingleInstance()
    if single.already_running:
        single.activate_existing()
        return

    settings = get_settings()
    config = load_config()
    geometry = clamp_to_screen(config.get("geometry") or dict(DEFAULT_GEOMETRY))

    window = webview.create_window(
        APP_TITLE,
        HOME_URL,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry.get("x"),
        y=geometry.get("y"),
        min_size=MIN_SIZE,
        background_color="#FFFFFF",
        confirm_close=False,
        hidden=bool(settings.get("start_minimized")),
    )

    _save_timer = None

    def read_geometry():
        """读取窗口几何；最小化/隐藏等退化状态返回 None。"""
        w, h = window.width, window.height
        if w < MIN_SIZE[0] or h < MIN_SIZE[1]:
            return None
        return {"width": w, "height": h, "x": window.x, "y": window.y}

    def persist_geometry(*_):
        """moved/resized 时防抖保存（拖动过程事件高频触发）。"""
        nonlocal _save_timer
        try:
            geom = read_geometry()
        except Exception:
            return
        if not geom:
            return
        if _save_timer:
            _save_timer.cancel()
        _save_timer = threading.Timer(0.5, lambda: save_geometry(geom))
        _save_timer.daemon = True
        _save_timer.start()

    def persist_geometry_now(*_):
        """closing 时立即保存，不能等防抖定时器。"""
        try:
            geom = read_geometry()
            if geom:
                save_geometry(geom)
        except Exception:
            pass

    def closing_behavior(*_):
        """按设置决定关闭按钮行为；返回 False 取消关闭（pywebview 取消约定）。"""
        if get_settings().get("close_to_tray") and not _force_quit:
            u = ctypes.windll.user32
            hwnd = _app_hwnd()
            if hwnd:
                u.ShowWindow(hwnd, SW_HIDE)
            return False  # 取消关闭，收进托盘
        return None

    def minimize_behavior(*_):
        if get_settings().get("minimize_to_tray"):
            window.hide()

    window.events.moved += persist_geometry
    window.events.resized += persist_geometry
    # FormClosing 时窗口仍存在、可读宽高；FormClosed 时已被销毁
    window.events.closing += persist_geometry_now
    window.events.closing += closing_behavior
    window.events.minimized += minimize_behavior
    # 页面加载完成后 hwnd 可用，此时设置标题栏/任务栏图标
    window.events.loaded += lambda *_: apply_window_icon()

    setup_tray(open_settings)
    threading.Thread(target=hotkey_loop, daemon=True).start()

    webview.start(
        private_mode=False,            # 持久化 Cookie/登录态
        storage_path=str(DATA_DIR),    # 固定用户数据目录
    )


if __name__ == "__main__":
    sys.exit(main())
