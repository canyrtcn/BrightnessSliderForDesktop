import sys
import ctypes
import subprocess
import threading
import time
import os
import winreg
from pathlib import Path

import screen_brightness_control as sbc
from PySide6.QtCore import Qt, QTimer, QSize, Signal, QObject
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "BrightnessSliderForDesktop"
TASK_NAME = "BrightnessSliderForDesktop"
DEFAULT_ACCENT = "#450606"


def app_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(__file__).resolve()


def tray_icon_candidates():
    bases = []
    if hasattr(sys, "_MEIPASS"):
        bases.append(Path(getattr(sys, "_MEIPASS")))
    bases.append(app_path().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    files = []
    for base in bases:
        for name in (
            "ICON.ico", "ICON.png",
            "ICONLIGHTMODE.ico", "ICONLIGHTMODE.png",
            "icon.ico", "icon.png",
            "iconlightmode.ico", "iconlightmode.png",
        ):
            candidate = base / name
            if candidate.exists():
                files.append(candidate)
    return files


def is_system_light_mode():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(int(value))
    except Exception:
        return False


def pick_icon_name():
    return "ICON" if is_system_light_mode() else "ICONLIGHTMODE"


def build_tray_icon():
    preferred = pick_icon_name().lower()
    candidates = tray_icon_candidates()
    ordered = [p for p in candidates if p.stem.lower() == preferred]
    ordered += [p for p in candidates if p.stem.lower() != preferred]
    if ordered:
        icon = QIcon()
        for path in ordered:
            for size in (16, 20, 24, 32, 40, 48, 64, 256):
                icon.addFile(str(path), QSize(size, size))
        if not icon.isNull():
            return icon
    if getattr(sys, "frozen", False):
        icon = QIcon(str(app_path()))
        if not icon.isNull():
            return icon
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(DEFAULT_ACCENT))
    painter.setPen(QColor("#2f0303"))
    painter.drawRoundedRect(10, 12, 44, 28, 8, 8)
    painter.setPen(QColor("#f5f2f2"))
    painter.drawLine(22, 46, 42, 46)
    painter.drawLine(32, 40, 32, 46)
    painter.end()
    return QIcon(pix)


def app_command():
    current = app_path()
    if getattr(sys, "frozen", False):
        return f'"{str(current)}"'
    return f'"{sys.executable}" "{str(current)}"'


def open_night_light_settings():
    os.startfile("ms-settings:nightlight")


class StartupManager:
    @staticmethod
    def is_enabled():
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0

    @staticmethod
    def create_task():
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", app_command(),
             "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0

    @staticmethod
    def delete_task():
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0

    @staticmethod
    def elevate_toggle(enable):
        action = "--enable-startup" if enable else "--disable-startup"
        if getattr(sys, "frozen", False):
            exe = str(app_path())
            params = action
        else:
            exe = sys.executable
            params = f'"{str(app_path())}" {action}'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)


class ThemeManager(QObject):
    palette_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.palette = self._build_palette()

    def _read_dword(self, root, path, name):
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return int(value)
        except Exception:
            return None

    def _read_accent(self):
        value = self._read_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM", "ColorizationColor")
        if value is None:
            value = self._read_dword(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
                "AccentColorMenu",
            )
        if value is None:
            return DEFAULT_ACCENT
        r = (value >> 16) & 0xFF
        g = (value >> 8) & 0xFF
        b = value & 0xFF
        return QColor(r, g, b).name()

    def _is_light_mode(self):
        value = self._read_dword(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "SystemUsesLightTheme",
        )
        return bool(value) if value is not None else False

    def _color_prevalence_enabled(self):
        value = self._read_dword(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "ColorPrevalence",
        )
        return bool(value) if value is not None else False

    def _mix(self, c1, c2, amount):
        a = QColor(c1)
        b = QColor(c2)
        p = max(0.0, min(1.0, amount))
        r  = round(a.red()   + (b.red()   - a.red())   * p)
        g  = round(a.green() + (b.green() - a.green()) * p)
        bl = round(a.blue()  + (b.blue()  - a.blue())  * p)
        return QColor(r, g, bl).name()

    def _build_palette(self):
        if self._color_prevalence_enabled():
            raw_accent = self._read_accent()
            card_bg = self._mix(raw_accent, "#000000", 0.40)
            text = "#111111" if QColor(card_bg).lightnessF() > 0.62 else "#f5f2f2"
            border = self._mix(card_bg, "#000000", 0.22)
            handle = self._mix(card_bg, "#ffffff", 0.20)
            handle_hover = self._mix(card_bg, "#ffffff", 0.30)
            handle_focus = self._mix(card_bg, "#ffffff", 0.38)
            track = "#d3cccc" if text == "#f5f2f2" else "#6e6a6a"
            subpage = "#f0ebeb" if text == "#f5f2f2" else "#3c3c3c"
            return dict(accent=card_bg, border=border, text=text,
                        handle=handle, handle_hover=handle_hover,
                        handle_focus=handle_focus, track=track, subpage=subpage)
        if self._is_light_mode():
            return dict(accent="#eeeeee", border="#d2d2d2", text="#272727",
                        handle="#bdbdbd", handle_hover="#a9a9a9",
                        handle_focus="#8f8f8f", track="#8a8a8a", subpage="#5a5a5a")
        return dict(accent="#1c1c1c", border="#0f0f0f", text="#f5f2f2",
                    handle="#4c4c4c", handle_hover="#5e5e5e",
                    handle_focus="#6f6f6f", track="#cfc6c6", subpage="#e8e2e2")

    def refresh(self, force=False):
        new_palette = self._build_palette()
        if force or new_palette != self.palette:
            self.palette = new_palette
            self.palette_changed.emit(new_palette)


class DisplayBackend:
    def __init__(self):
        self._monitors = []

    def _clean_name(self, info, idx):
        for key in ("name", "model", "serial"):
            value = info.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text or text.lower() == "none":
                continue
            if text.lower().startswith("none "):
                text = text[5:].strip()
            return text
        return f"Display {idx + 1}"

    def refresh(self):
        self._monitors = []
        displays = []
        try:
            infos = sbc.list_monitors_info(method="vcp", allow_duplicates=False)
        except Exception:
            infos = []
        for idx, info in enumerate(infos):
            try:
                value = sbc.get_brightness(display=idx, method="vcp")
                if isinstance(value, list):
                    value = value[0]
                brightness = max(0, min(100, int(round(float(value)))))
                name = self._clean_name(info, idx)
                self._monitors.append({"display": idx, "name": name, "brightness": brightness})
                displays.append({"index": idx, "name": name, "brightness": brightness})
            except Exception:
                continue
        return displays

    def set_brightness(self, index, value):
        value = max(0, min(100, int(value)))
        sbc.set_brightness(value, display=index, method="vcp")
        for item in self._monitors:
            if item["display"] == index:
                item["brightness"] = value
                break
        return value


class DisplayController(QObject):
    brightness_changed = Signal(int, int)
    displays_updated = Signal(list)

    def __init__(self):
        super().__init__()
        self.backend = DisplayBackend()
        self.pending = {}
        self.timers = {}
        self.cached_displays = []
        self._refresh_lock = threading.Lock()
        # Tracks brightness values set by the user, until hardware confirms them
        self._user_brightness = {}

    def refresh(self):
        if not self._refresh_lock.acquire(blocking=False):
            return

        def worker():
            try:
                displays = self.backend.refresh()
                # Never let a hardware read overwrite a value the user set.
                # _user_brightness tracks every value the user has chosen;
                # it is only cleared when hardware confirms the new value.
                for d in displays:
                    idx = d["index"]
                    user_val = self._user_brightness.get(idx)
                    if user_val is not None:
                        if d["brightness"] == user_val:
                            # Hardware caught up — stop overriding
                            del self._user_brightness[idx]
                        else:
                            # Hardware still shows old value; keep user's value
                            d["brightness"] = user_val
                self.cached_displays = displays
                self.displays_updated.emit(displays)
            finally:
                self._refresh_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def set_brightness_debounced(self, index, value):
        self.pending[index] = value
        # Track the user's chosen value so hardware reads never overwrite it.
        self._user_brightness[index] = value
        for item in self.cached_displays:
            if item["index"] == index:
                item["brightness"] = value
                break
        timer = self.timers.get(index)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda i=index: self._apply(i))
            self.timers[index] = timer
        timer.start(30)

    def _apply(self, index):
        if index not in self.pending:
            return
        value = self.pending.pop(index)
        try:
            applied = self.backend.set_brightness(index, value)
            for item in self.cached_displays:
                if item["index"] == index:
                    item["brightness"] = applied
                    break
            self.brightness_changed.emit(index, applied)
        except Exception:
            pass


class MonitorIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_color = QColor("#f5f2f2")
        self.setFixedSize(22, 22)

    def set_theme(self, palette):
        self.text_color = QColor(palette["text"])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.text_color)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(3, 3, 16, 11, 2, 2)
        painter.drawLine(8, 18, 14, 18)
        painter.drawLine(11, 14, 11, 18)
        painter.end()


class BrightnessSlider(QSlider):
    def __init__(self, theme_manager, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.theme_manager = theme_manager
        self.setRange(0, 100)
        self.setSingleStep(1)
        self.setPageStep(5)
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.theme_manager.palette_changed.connect(self._apply_current_theme)
        self._apply_style(False, False, self.theme_manager.palette)

    def enterEvent(self, event):
        self._apply_style(True, self.hasFocus(), self.theme_manager.palette)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_style(False, self.hasFocus(), self.theme_manager.palette)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._apply_style(self.underMouse(), True, self.theme_manager.palette)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._apply_style(self.underMouse(), False, self.theme_manager.palette)
        super().focusOutEvent(event)

    def _apply_current_theme(self, palette):
        self._apply_style(self.underMouse(), self.hasFocus(), palette)

    def _apply_style(self, hover, focused, palette):
        handle = palette["handle_hover"] if hover else palette["handle"]
        border = palette["handle_focus"] if focused else palette["border"]
        self.setStyleSheet(f"""
            QSlider {{ background: transparent; min-height: 30px; }}
            QSlider::groove:horizontal {{
                height: 4px; background: {palette['track']};
                border-radius: 2px; margin: 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {palette['subpage']}; border-radius: 2px;
            }}
            QSlider::add-page:horizontal {{
                background: {palette['track']}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {handle}; border: 2px solid {border};
                width: 14px; height: 14px; margin: -7px 0; border-radius: 9px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {palette['handle_hover']};
                border: 2px solid {palette['handle_focus']};
            }}
            QSlider:focus {{ outline: none; }}
        """)


class MonitorCard(QFrame):
    brightness_changed = Signal(int, int)

    def __init__(self, theme_manager, index, name, value, parent=None):
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.index = index
        self.value_label = QLabel(str(value))
        self.name_label = QLabel(name)
        self.icon_widget = MonitorIcon()
        self.slider = BrightnessSlider(self.theme_manager)
        self.slider.setValue(value)
        self._pending_external_value = None
        self.slider.sliderReleased.connect(self._apply_pending_external_value)
        self.setObjectName("monitorCard")
        self.setMinimumWidth(330)
        self.setMaximumWidth(330)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._build()
        self.slider.valueChanged.connect(self._emit_value)
        self.theme_manager.palette_changed.connect(self.apply_theme)
        self.apply_theme(self.theme_manager.palette)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.name_label.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        self.value_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setMinimumWidth(38)
        top.addWidget(self.icon_widget)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.value_label)
        outer.addLayout(top)
        outer.addWidget(self.slider)

    def apply_theme(self, palette):
        self.icon_widget.set_theme(palette)
        self.setStyleSheet(f"""
            QFrame#monitorCard {{
                background: {palette['accent']};
                border: 1px solid {palette['border']};
                border-radius: 12px;
            }}
            QLabel {{ color: {palette['text']}; background: transparent; }}
        """)

    def set_value(self, value):
        if self.slider.isSliderDown():
            self._pending_external_value = value
            return
        self._pending_external_value = None
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_label.setText(str(value))

    def _apply_pending_external_value(self):
        if self._pending_external_value is None:
            return
        value = self._pending_external_value
        self._pending_external_value = None
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_label.setText(str(value))

    def _emit_value(self, value):
        self.value_label.setText(str(value))
        self.brightness_changed.emit(self.index, value)


class PopupPanel(QWidget):
    closed = Signal()

    def __init__(self, controller, theme_manager, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.controller = controller
        self.theme_manager = theme_manager
        self.cards = {}
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("popupRoot")
        self.setStyleSheet("QWidget#popupRoot { background: transparent; }")
        self._build()
        # NOTE: displays_updated is NOT connected here.
        # TrayApp routes it manually and passes sync_values=False while the
        # popup is open, so background refreshes never reset the sliders.
        self.controller.brightness_changed.connect(self.apply_external_value)
        self.theme_manager.palette_changed.connect(self._refresh_empty_label)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.container = QFrame()
        self.cards_layout = QVBoxLayout(self.container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        root.addWidget(self.container)

    def _refresh_empty_label(self, palette):
        for i in range(self.cards_layout.count()):
            widget = self.cards_layout.itemAt(i).widget()
            if isinstance(widget, QLabel) and widget.objectName() == "emptyLabel":
                widget.setStyleSheet(
                    f"background:{palette['accent']}; color:{palette['text']};"
                    f" border:1px solid {palette['border']}; border-radius:12px;"
                    f" padding:16px 18px; font:11pt 'Segoe UI'; min-width:330px;"
                )

    def update_displays(self, displays, sync_values=True):
        """Rebuild or update the card list.

        sync_values=True  — first paint / popup not yet visible: write the
                            hardware-read brightness into every card.
        sync_values=False — background refresh while popup is open: only
                            add/remove cards for monitors that appeared or
                            disappeared; never touch existing slider values.
        """
        incoming_indices = {d["index"] for d in displays}

        # Remove cards for monitors that are no longer present
        for idx in list(self.cards.keys()):
            if idx not in incoming_indices:
                card = self.cards.pop(idx)
                self.cards_layout.removeWidget(card)
                card.deleteLater()

        # Remove the "no displays" label if present
        for i in reversed(range(self.cards_layout.count())):
            widget = self.cards_layout.itemAt(i).widget()
            if isinstance(widget, QLabel) and widget.objectName() == "emptyLabel":
                self.cards_layout.takeAt(i)
                widget.deleteLater()

        if not displays:
            palette = self.theme_manager.palette
            empty = QLabel("No DDC/CI displays found")
            empty.setObjectName("emptyLabel")
            empty.setStyleSheet(
                f"background:{palette['accent']}; color:{palette['text']};"
                f" border:1px solid {palette['border']}; border-radius:12px;"
                f" padding:16px 18px; font:11pt 'Segoe UI'; min-width:330px;"
            )
            self.cards_layout.addWidget(empty)
            self.adjustSize()
            if self.isVisible():
                self._reposition()
            return

        for display in displays:
            idx = display["index"]
            if idx in self.cards:
                if sync_values:
                    self.cards[idx].set_value(display["brightness"])
                # sync_values=False → leave the slider exactly where it is
            else:
                card = MonitorCard(self.theme_manager, idx, display["name"], display["brightness"])
                card.brightness_changed.connect(self.controller.set_brightness_debounced)
                self.cards_layout.addWidget(card)
                self.cards[idx] = card

        self.adjustSize()
        if self.isVisible():
            self._reposition()

    def apply_external_value(self, index, value):
        if index in self.cards:
            self.cards[index].set_value(value)

    def _reposition(self):
        self.adjustSize()
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        x = available.x() + available.width() - self.width() - 28
        y = available.y() + available.height() - self.height() - 8
        self.move(max(available.x() + 8, x), max(available.y() + 8, y))

    def show_bottom_right(self):
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()
        self._reposition()
        QTimer.singleShot(0, self._reposition)
        QTimer.singleShot(80, self._reposition)
        QTimer.singleShot(160, self._reposition)


class TrayApp(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.theme_manager = ThemeManager()
        self.controller = DisplayController()
        self.popup = PopupPanel(self.controller, self.theme_manager)
        self.pending_popup = False
        self._last_popup_hide = 0.0
        self.popup.closed.connect(self._on_popup_closed)
        self.tray = QSystemTrayIcon(build_tray_icon(), self.app)
        self.tray.setToolTip("Brightness")
        self.menu = QMenu()
        self.refresh_action = QAction("Refresh Displays", self.menu)
        self.night_light_action = QAction("Night Light Settings", self.menu)
        self.startup_action = QAction("Launch at Startup", self.menu)
        self.startup_action.setCheckable(True)
        self.exit_action = QAction("Exit", self.menu)
        self.menu.addAction(self.refresh_action)
        self.menu.addAction(self.night_light_action)
        self.menu.addSeparator()
        self.menu.addAction(self.startup_action)
        self.menu.addSeparator()
        self.menu.addAction(self.exit_action)
        self.tray.setContextMenu(self.menu)
        self.refresh_action.triggered.connect(self.controller.refresh)
        self.night_light_action.triggered.connect(open_night_light_settings)
        self.exit_action.triggered.connect(self.app.quit)
        self.startup_action.triggered.connect(self.toggle_startup)
        self.tray.activated.connect(self.handle_tray_activation)

        # Route displays_updated manually so we control sync_values
        self.controller.displays_updated.connect(self._on_displays_updated)

        self.theme_manager.palette_changed.connect(self._theme_changed)
        self.tray.show()
        self._sync_startup_state()
        self.theme_timer = QTimer(self)
        self.theme_timer.timeout.connect(self.theme_manager.refresh)
        self.theme_timer.start(2000)
        QTimer.singleShot(250, self.controller.refresh)

    def _on_displays_updated(self, displays):
        if self.pending_popup and not self.popup.isVisible():
            # First time showing: paint with hardware values
            self.pending_popup = False
            self.popup.update_displays(displays, sync_values=True)
            self.popup.show_bottom_right()
            return

        if self.popup.isVisible():
            # Background refresh while popup is open:
            # only add/remove cards, never overwrite slider positions
            self.popup.update_displays(displays, sync_values=False)
            return

        # Popup is closed: safe to silently update cached state
        self.popup.update_displays(displays, sync_values=True)

    def _on_popup_closed(self):
        self._last_popup_hide = time.monotonic()

    def _sync_startup_state(self):
        self.startup_action.blockSignals(True)
        self.startup_action.setChecked(StartupManager.is_enabled())
        self.startup_action.blockSignals(False)

    def _theme_changed(self, palette):
        self.tray.setIcon(build_tray_icon())
        if self.popup.isVisible() and self.controller.cached_displays:
            self.popup.update_displays(self.controller.cached_displays, sync_values=False)

    def handle_tray_activation(self, reason):
        if reason != QSystemTrayIcon.Trigger:
            return
        now = time.monotonic()
        if now - self._last_popup_hide < 0.25:
            return
        self.theme_manager.refresh()
        if self.popup.isVisible():
            self.popup.hide()
            self.pending_popup = False
            return
        if self.controller.cached_displays:
            # Show cached values immediately, then refresh in background.
            # The background result arrives as sync_values=False so it will
            # never overwrite what the user is (or is about to be) dragging.
            self.popup.update_displays(self.controller.cached_displays, sync_values=True)
            self.popup.show_bottom_right()
            self.controller.refresh()
        else:
            self.pending_popup = True
            self.controller.refresh()

    def toggle_startup(self, enabled):
        StartupManager.elevate_toggle(enabled)
        QTimer.singleShot(1200, self._sync_startup_state)


def handle_cli_admin_actions():
    if "--enable-startup" in sys.argv:
        StartupManager.create_task()
        return True
    if "--disable-startup" in sys.argv:
        StartupManager.delete_task()
        return True
    return False


def main():
    if handle_cli_admin_actions():
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(build_tray_icon())
    tray = TrayApp(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
