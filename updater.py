import json
import re
import ssl
import sys
import time
from datetime import datetime
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PySide6.QtCore import QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import QTextBrowser
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtCore import QUrl

from qfluentwidgets import (
    MessageBoxBase, SubtitleLabel, BodyLabel, CheckBox
)

from utils import show_error, show_info
from logger_utils import get_logger

log = get_logger(__name__)

GITHUB_LATEST_API = "https://api.github.com/repos/H1ghSyst3m/DIM-Creator/releases/latest"


@dataclass
class ReleaseInfo:
    tag_name: str
    name: str
    html_url: str
    body: str
    published_at: str


def _normalize_version(v: str) -> str:
    if not v:
        return "0.0.0"
    v = v.strip()
    v = v[1:] if v.lower().startswith('v') else v
    v = v.split('+')[0]
    v = v.split('-')[0]
    return v


def _to_tuple(v: str):
    parts = [p for p in _normalize_version(v).split('.') if p != ""]
    out = []
    for p in parts:
        m = re.match(r'^(\d+)', p)
        out.append(int(m.group(1)) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(remote_tag: str, current_version: str) -> bool:
    return _to_tuple(remote_tag) > _to_tuple(current_version)


def _fetch_latest(timeout=7) -> ReleaseInfo:
    ua = f"DIM-Creator-Updater/{sys.version_info.major}.{sys.version_info.minor}"
    req = Request(GITHUB_LATEST_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": ua,
    })
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return ReleaseInfo(
            tag_name=data.get("tag_name") or "",
            name=data.get("name") or "",
            html_url=data.get("html_url") or data.get("html_url", ""),
            body=data.get("body") or "",
            published_at=data.get("published_at") or "",
        )


class UpdateCheckThread(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, *, current_version: str):
        super().__init__()
        self.current_version = current_version

    def run(self):
        try:
            rel = _fetch_latest()
            self.result.emit(rel)
        except (HTTPError, URLError) as e:
            self.error.emit(f"Network error: {e}")
        except Exception as e:
            self.error.emit(str(e))


class UpdateDialog(MessageBoxBase):
    def __init__(self, parent, *, current_version: str, rel: ReleaseInfo):
        super().__init__(parent)

        self._rel = rel
        self.skip_this_version = False

        friendly_date = rel.published_at
        try:
            dt = datetime.fromisoformat((friendly_date or "").replace("Z", "+00:00"))
            friendly_date = dt.strftime("%d %b %Y • %H:%M UTC")
        except Exception:
            pass

        title = SubtitleLabel("Update available", self)
        version_line = BodyLabel(
            f"Current: <b>{current_version}</b>  •  Latest: <b>{rel.tag_name}</b>", self
        )
        date_line = BodyLabel(f"Published: {friendly_date}", self)

        self.notes = QTextBrowser(self)
        self.notes.setOpenExternalLinks(True)
        self.notes.setFont(QFont("Consolas"))
        self.notes.setMinimumHeight(300)
        body = rel.body or "No release notes provided."
        try:
            self.notes.setMarkdown(body)
        except Exception:
            self.notes.setPlainText(body)

        self.skipCheck = CheckBox("Skip this version", self)

        self.viewLayout.addWidget(title)
        self.viewLayout.addWidget(version_line)
        self.viewLayout.addWidget(date_line)
        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(self.notes)
        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(self.skipCheck)

        self.yesButton.setText("Open Release Page")
        self.cancelButton.setText("Later")

        self.widget.setMinimumWidth(560)

        self.skipCheck.stateChanged.connect(
            lambda _: setattr(self, "skip_this_version", self.skipCheck.isChecked())
        )


class UpdateManager(QObject):
    checkingChanged = Signal(bool)

    def __init__(self, parent_widget, settings, *, current_version: str, interval_hours: int = 24):
        super().__init__(parent_widget)
        self.parent = parent_widget
        self.settings = settings
        self.current_version = current_version
        self.interval_hours = max(1, int(interval_hours))
        self._thread = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._auto_check_now)

    def manual_check(self):
        self._start_check(manual=True)

    def schedule_on_startup_if_enabled(self):
        if not self.settings.value("auto_update_check", True, type=bool):
            return

        last = self.settings.value("last_update_check_ts", 0, type=int)
        now = int(time.time())

        if last and (now - last) < self.interval_hours * 3600:
            log.info("Skipping auto update check: cooldown active.")
            return

        self._timer.start(2500)

    def set_auto_enabled(self, enabled: bool):
        self.settings.setValue("auto_update_check", bool(enabled))

    def _auto_check_now(self):
        self._start_check(manual=False)

    def _start_check(self, *, manual: bool):
        if self._thread and self._thread.isRunning():
            if manual:
                show_info(self.parent, "Update", "Already checking for updates…")
            return

        self.checkingChanged.emit(True)
        self._thread = UpdateCheckThread(current_version=self.current_version)
        self._thread.result.connect(lambda rel: self._on_result(rel, manual))
        self._thread.error.connect(lambda msg: self._on_error(msg, manual))
        self._thread.finished.connect(lambda: self.checkingChanged.emit(False))
        self._thread.start()

    def _on_result(self, rel: ReleaseInfo, manual: bool):
        self.settings.setValue("last_update_check_ts", int(time.time()))

        if not rel or not rel.tag_name:
            if manual:
                show_error(self.parent, "Update", "Could not parse release data.")
            return

        ignored = self.settings.value("ignore_version", "", type=str) or ""
        if ignored and rel.tag_name == ignored:
            log.info("Latest version %s is currently ignored by user.", rel.tag_name)
            if manual:
                show_info(self.parent, "Update", f"You’ve chosen to skip {rel.tag_name}.")
            return

        if ignored and is_newer(rel.tag_name, ignored):
            self.settings.remove("ignore_version")

        if is_newer(rel.tag_name, self.current_version):
            self._show_update_dialog(rel)
        else:
            if manual:
                show_info(self.parent, "Up to date", f"You’re on the latest version ({self.current_version}).")

    def _on_error(self, msg: str, manual: bool):
        log.error(f"Update check failed: {msg}")
        if manual:
            show_error(self.parent, "Update check failed", msg)

    def _show_update_dialog(self, rel: ReleaseInfo):
        dlg = UpdateDialog(self.parent, current_version=self.current_version, rel=rel)
        accepted = dlg.exec()
        if accepted:
            QDesktopServices.openUrl(QUrl(rel.html_url or "https://github.com/H1ghSyst3m/DIM-Creator/releases/latest"))

        if getattr(dlg, "skip_this_version", False):
            self.settings.setValue("ignore_version", rel.tag_name)
