"""WeChat window mode: use the logged-in official page's accessibility controls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

TZ = timezone(timedelta(hours=8))
TITLE = "深圳市天文台公众开放日预约"


class WindowError(Exception):
    pass


def name_of(control):
    # window_text() can return Value/TextPattern contents instead of the UIA label.
    return (control.element_info.name or "").strip()


def date_matches(text, target):
    match = re.fullmatch(r"\s*(\d{1,2})-(\d{1,2})\s+星期([一二三四五六日天])\s*", text)
    return bool(match and (int(match[1]), int(match[2])) == (target.month, target.day)
                and match[3] in ("一二三四五六日"[target.weekday()], "天" if target.weekday() == 6 else ""))


@dataclass(frozen=True)
class Plan:
    release: datetime
    target: date
    period: str
    interval: float = 5
    minutes: float = 10

    def validate(self, now=None):
        now = now or datetime.now(TZ)
        if self.release.tzinfo is None:
            raise WindowError("放号时间必须采用北京时间。")
        if self.period not in ("上午", "下午", "任一时段"):
            raise WindowError("请选择上午、下午或任一时段。")
        if not 5 <= self.interval <= 300 or not 1 <= self.minutes <= 60:
            raise WindowError("刷新间隔为 5–300 秒，持续时间为 1–60 分钟。")
        if not now.date() <= self.target <= now.date() + timedelta(days=14):
            raise WindowError("参观日期需在今天至未来 14 天内。")
        if self.release > now + timedelta(days=7):
            raise WindowError("请在放号前 7 天内设置任务，并在临近放号时打开网页。")
        week_start = self.release.date() - timedelta(days=self.release.weekday())
        if not week_start <= self.target <= week_start + timedelta(days=6):
            raise WindowError("参观日期必须属于所设放号时间的同一周。")


class SubmitLatch:
    """Persist only an attempt marker; never persist form or login data."""
    def __init__(self, path=None):
        self.path = Path(path or Path(os.environ.get("LOCALAPPDATA", ".")) /
                         "SztwtReservationAssistant" / "window-submit.json")

    def exists(self):
        return self.path.exists()

    def acquire(self, plan):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="utf-8") as stream:
                json.dump({"attempted_at": datetime.now(TZ).isoformat(),
                           "date": plan.target.isoformat(), "period": plan.period}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise WindowError("已有提交记录，请先到微信“预约信息”核对结果。")

    def clear(self):
        self.path.unlink(missing_ok=True)


class WindowClient:
    def __init__(self, stop=None):
        # Import on the worker thread so its COM objects stay on that thread.
        import sys
        sys.coinit_flags = 0
        import comtypes
        comtypes.CoInitializeEx(0)
        from pywinauto import Desktop
        import psutil
        self.desktop, self.psutil = Desktop(backend="uia"), psutil
        self.window = self.doc = None
        self.stop = stop or threading.Event()
        self.last_refresh = 0.0

    def bind(self):
        candidates = []
        for win in self.desktop.windows():
            try:
                if self.psutil.Process(win.process_id()).name().lower() != "wechatappex.exe":
                    continue
                docs = win.descendants(control_type="Document", title=TITLE)
                if len(docs) == 1 and self._official(docs[0]):
                    candidates.append(win)
            except (self.psutil.Error, OSError):
                continue
        if len(candidates) != 1:
            raise WindowError("请先把微信中的天文台“个人预约”页面放到前台，仅保留一个该页面，再点击检测。")
        self.window = candidates[0]
        self.pid = self.window.process_id()
        self.created = self.psutil.Process(self.pid).create_time()
        self.snapshot()

    @staticmethod
    def _official(doc):
        try:
            url = doc.iface_value.CurrentValue
        except Exception:
            try:
                url = doc.legacy_properties().get("Value", "")
            except Exception:
                return False
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.netloc == "weather.121.com.cn"
                and parsed.path.rstrip("/") == "/sztwt" and parsed.fragment in ("/", "/gr", ""))

    def guard(self, foreground=True):
        import win32gui
        import win32api
        if self.stop.is_set() or win32api.GetAsyncKeyState(0x1B) & 0x8000:
            raise WindowError("已停止。")
        if (not win32gui.IsWindow(self.window.handle)
                or self.psutil.Process(self.pid).create_time() != self.created):
            raise WindowError("原微信窗口已关闭，请重新检测。")
        if foreground and win32gui.GetForegroundWindow() != self.window.handle:
            raise WindowError("微信预约窗口已离开前台，任务停止；请重新启动。")

    def snapshot(self):
        self.guard(foreground=False)
        docs = self.window.descendants(control_type="Document", title=TITLE)
        if len(docs) != 1 or not self._official(docs[0]):
            raise WindowError("预约页面已切换或登录状态改变，请回到个人预约页面。")
        self.doc = docs[0]
        self.nodes = self.doc.descendants()
        dialogs = [n for n in self.nodes if n.element_info.control_type == "Window" and n.is_visible()]
        challenges = [n for n in self.nodes if n.element_info.control_type == "Edit"
                      and "验证码" in (name_of(n) + (n.element_info.element.CurrentHelpText or ""))]
        if dialogs or challenges:
            raise WindowError("网页出现弹窗或验证码，请在微信中处理后重新启动。")
        return self

    def unique(self, kind, name, partial=False):
        found = [n for n in self.nodes if n.element_info.control_type == kind
                 and (name in name_of(n) if partial else name == name_of(n))]
        if len(found) != 1:
            raise WindowError(f"无法唯一识别“{name}”，请检查页面是否正常显示。")
        return found[0]

    def refresh_button(self):
        return self.unique("Button", "点击刷新可约名额", partial=True)

    def inspect(self):
        refresh = self.refresh_button()
        submit = self.unique("Button", "提交")
        # Resolve the actual interfaces now so a missing provider is detected early.
        refresh.iface_invoke
        submit.iface_invoke
        dates = [name_of(n) for n in self.nodes if n.element_info.control_type == "Text"
                 and re.fullmatch(r"\d{1,2}-\d{1,2}\s+星期[一二三四五六日天]", name_of(n))]
        radios = [n for n in self.nodes if n.element_info.control_type == "RadioButton"]
        for radio in radios:
            radio.iface_selection_item
        return dates, len(radios), sum(r.is_enabled() for r in radios)

    @staticmethod
    def _value(control):
        try:
            return str(control.iface_value.CurrentValue).strip()
        except Exception:
            return str(control.legacy_properties().get("Value", "")).strip()

    def ready_signature(self):
        # Values are read only for readiness/change detection, never logged or written.
        values = []
        for label in ("预约人姓名", "证件号", "手机"):
            edit = self.unique("Edit", label, partial=True)
            value = self._value(edit)
            if not value:
                raise WindowError(f"请先在微信网页填写{label}。")
        for node in self.nodes:
            kind = node.element_info.control_type
            if kind == "Edit":
                value = self._value(node)
                if "*" in name_of(node) and not value:
                    raise WindowError("网页上还有未填写的必填项。")
                values.append((name_of(node), value))
            elif kind == "ComboBox":
                value = self._value(node)
                texts = " ".join(name_of(c) for c in node.parent().children())
                if "请选择" in texts:
                    raise WindowError("请在微信网页完成证件类型等必选项。")
                values.append((name_of(node), value, texts))
        checks = [n for n in self.nodes if n.element_info.control_type == "CheckBox"]
        if len(checks) != 1 or checks[0].get_toggle_state() != 1:
            raise WindowError("请先在微信网页阅读并勾选预约须知。")
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()

    def foreground(self):
        import win32gui
        self.window.set_focus()
        # Chromium/WeChat posts the activation asynchronously.
        for _ in range(30):
            if win32gui.GetForegroundWindow() == self.window.handle:
                break
            if self.stop.wait(0.05):
                raise WindowError("已停止。")
        self.guard()

    def refresh(self):
        self.snapshot()
        self.guard()
        if time.monotonic() - self.last_refresh < 5:
            return False
        button = self.refresh_button()
        if not button.is_enabled():
            return False
        self.last_refresh = time.monotonic()
        button.iface_invoke.Invoke()
        return True

    def day_control(self, target):
        found = [n for n in self.nodes if n.element_info.control_type == "Text"
                 and date_matches(name_of(n), target)]
        if not found:
            return None
        if len(found) != 1:
            raise WindowError("目标日期出现多次，无法确定预约日期。")
        return found[0].parent()

    def ensure_visible(self, control):
        """Bring the observed control into view without changing zoom or form input."""
        import win32gui
        from pywinauto import mouse
        try:
            control.iface_scroll_item.ScrollIntoView()
        except Exception:
            pass
        for _ in range(24):
            self.guard()
            wr, cr = self.window.rectangle(), control.rectangle()
            x, y = (cr.left + cr.right) // 2, (cr.top + cr.bottom) // 2
            top, bottom = wr.top + 100, wr.bottom - 35
            if cr.width() > 0 and top <= cr.top and cr.bottom <= bottom:
                under = win32gui.WindowFromPoint((x, y))
                if win32gui.GetAncestor(under, 2) != self.window.handle:
                    raise WindowError("目标控件被其他窗口遮挡，请移开后重试。")
                return x, y
            if cr.width() <= 0 or cr.height() <= 0:
                raise WindowError("目标控件不可见，请关闭网页弹窗后重试。")
            point = (wr.right - 50, (top + bottom) // 2)
            if win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2) != self.window.handle:
                raise WindowError("微信网页被遮挡。")
            mouse.scroll(coords=point, wheel_dist=3 if cr.top < top else -3)
            if self.stop.wait(0.12):
                raise WindowError("已停止。")
        raise WindowError("无法把目标日期滚动到可见位置，请手动滚动到参观时段后重试。")

    def day_selected(self, control):
        self.ensure_visible(control)
        # Official el-check-tag has no selected UIA property. Confirm its blue fill.
        from PIL import ImageGrab
        r = control.rectangle()
        img = ImageGrab.grab(bbox=(r.left + 2, r.top + 2, r.right - 2, r.bottom - 2),
                             all_screens=True).convert("RGB").resize((24, 12))
        pixels = list(img.getdata())
        return sum(b > 190 and b - red > 15 and green - red > 7
                   for red, green, b in pixels) / len(pixels) > 0.35

    def select_day(self, target):
        self.snapshot()
        control = self.day_control(target)
        if control is None:
            return False
        if not self.day_selected(control):
            from pywinauto import mouse
            point = self.ensure_visible(control)
            self.guard()
            mouse.click(coords=point)
            if self.stop.wait(0.2):
                raise WindowError("已停止。")
        self.snapshot()
        control = self.day_control(target)
        if control is None or not self.day_selected(control):
            raise WindowError("未能确认目标日期已选中，已停止。")
        return True

    def choose_slot(self, period):
        self.snapshot()
        radios = [n for n in self.nodes if n.element_info.control_type == "RadioButton"]
        for radio in radios:
            text = " ".join(name_of(c) for c in radio.parent().children())
            if radio.is_enabled() and (period == "任一时段" or period in text):
                self.guard()
                radio.iface_selection_item.Select()
                if self.stop.wait(0.15):
                    raise WindowError("已停止。")
                self.snapshot()
                selected = [n for n in self.nodes if n.element_info.control_type == "RadioButton"
                            and n.is_selected()]
                if len(selected) != 1:
                    raise WindowError("无法确认所选时段，已停止。")
                chosen = " ".join(name_of(c) for c in selected[0].parent().children())
                if period != "任一时段" and period not in chosen:
                    raise WindowError("所选时段与目标不一致，已停止。")
                return True
        return False

    def submit(self, plan, expected, latch):
        self.snapshot()
        if self.ready_signature() != expected:
            raise WindowError("网页人员信息发生变化，请核对后重新启动。")
        day = self.day_control(plan.target)
        if day is None or not self.day_selected(day):
            raise WindowError("提交前无法确认目标日期。")
        selected = [n for n in self.nodes if n.element_info.control_type == "RadioButton" and n.is_selected()]
        if len(selected) != 1 or not selected[0].is_enabled():
            raise WindowError("提交前所选时段已不可用。")
        label = " ".join(name_of(c) for c in selected[0].parent().children())
        if plan.period != "任一时段" and plan.period not in label:
            raise WindowError("提交前时段发生变化。")
        button = self.unique("Button", "提交")
        if not button.is_enabled():
            raise WindowError("提交按钮尚未启用，请检查网页上的必填项和须知。")
        self.guard()
        latch.acquire(plan)
        # A failed/uncertain Invoke must never fall back to another click or retry.
        button.iface_invoke.Invoke()


def run(client, plan, latch, stop, report, *, clock=None):
    clock = clock or (lambda: datetime.now(TZ))
    plan.validate(clock())
    if latch.exists():
        raise WindowError("已有提交记录。请先到微信“预约信息”核对，再解除提交锁。")
    client.snapshot()
    expected = client.ready_signature()
    report("已检查网页资料；等待放号。开始后请保持微信预约窗口在前台。")
    last_status = None
    while clock() < plan.release:
        if stop.wait(0.25):
            return
        remaining = int((plan.release - clock()).total_seconds())
        if remaining // 30 != last_status:
            last_status = remaining // 30
            report(f"距离放号约 {max(0, remaining)} 秒。")
    client.foreground()
    deadline = time.monotonic() + plan.minutes * 60
    while time.monotonic() < deadline and not stop.is_set():
        cycle = time.monotonic()
        client.snapshot()
        if client.ready_signature() != expected:
            raise WindowError("网页资料发生变化或已失效，请重新填写并启动。")
        if client.refresh():
            report("已点击网页刷新按钮，等待场次更新。")
            if stop.wait(1.2):
                return
            if client.select_day(plan.target):
                if client.choose_slot(plan.period):
                    client.submit(plan, expected, latch)
                    report("已点击提交并停止。请在微信处理可能出现的验证码或确认提示，最终到“预约信息”核对。")
                    return
                report("目标日期暂无可选时段，继续等待。")
            else:
                report("页面尚未显示目标日期，继续等待。")
        if stop.wait(max(0.1, plan.interval - (time.monotonic() - cycle))):
            return
    report("已停止或达到持续时间。")
