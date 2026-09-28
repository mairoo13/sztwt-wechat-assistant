"""Standalone executable entry for WeChat window mode."""
import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

from wechat_engine import Plan, SubmitLatch, TZ, WindowClient, WindowError, run


class App:
    def __init__(self, root):
        self.root, self.events = root, queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.latch = SubmitLatch()
        root.title("天文台预约助手 · 微信窗口模式")
        root.geometry("1060x790")
        root.minsize(1000, 740)
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 11))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=5)
        main = ttk.Frame(root, padding=20)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="微信里填资料，助手到点操作", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(main, text="无需导出会话。检测通过后，自动刷新、选日期和时段，点击一次提交。", wraplength=990).pack(anchor="w", pady=(8, 14))
        instructions = ("1. 在电脑微信打开个人预约页，填好人员资料，阅读并勾选页面底部须知。\n"
                        "2. 设置参观日期和放号时间，点击“检测页面”，再点击“开始”。\n"
                        "3. 运行时保持微信窗口在前台；按 Esc 可停止。验证码与后续确认由你在微信处理。")
        ttk.Label(main, text=instructions, justify="left", wraplength=990).pack(anchor="w")
        now = datetime.now(TZ)
        monday = (now + timedelta(days=(7 - now.weekday()) % 7)).replace(hour=10, minute=0, second=0, microsecond=0)
        if monday <= now:
            monday += timedelta(days=7)
        self.fields = {"放号时间": tk.StringVar(value=monday.strftime("%Y-%m-%d %H:%M:%S")),
                       "参观日期": tk.StringVar(value=(monday + timedelta(days=1)).date().isoformat()),
                       "时段": tk.StringVar(value="上午"), "间隔（秒）": tk.StringVar(value="5"),
                       "持续（分钟）": tk.StringVar(value="10")}
        form = ttk.LabelFrame(main, text="北京时间", padding=12)
        form.pack(fill="x", pady=15)
        self.editors = []
        for index, (name, var) in enumerate(self.fields.items()):
            row, col = divmod(index, 3)
            ttk.Label(form, text=name).grid(row=row, column=col*2, sticky="w", pady=6, padx=(0, 6))
            widget = (ttk.Combobox(form, textvariable=var, values=("上午", "下午", "任一时段"), state="readonly", width=12)
                      if name == "时段" else ttk.Entry(form, textvariable=var, width=22 if index == 0 else 13))
            widget.grid(row=row, column=col*2+1, sticky="w", padx=(0, 12))
            self.editors.append(widget)
        self.agree = tk.BooleanVar(value=False)
        self.check = ttk.Checkbutton(main, text="资料已在微信填好；允许满足所设日期和时段后自动点击提交", variable=self.agree)
        self.check.pack(anchor="w")
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=12)
        self.buttons = []
        for text, task in (("检测页面", "inspect"), ("试刷一次", "refresh"), ("试选日期", "select"), ("开始", "run")):
            button = ttk.Button(actions, text=text, command=lambda t=task: self.start(t))
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.stop_button = ttk.Button(actions, text="停止 / Esc", command=self.stop_event.set, state="disabled")
        self.stop_button.pack(side="right")
        self.status = tk.StringVar(value="先在微信打开个人预约页面。检测不会提交预约。")
        ttk.Label(main, textvariable=self.status, wraplength=990, foreground="#185878").pack(anchor="w", pady=(0, 8))
        self.log = tk.Text(main, height=3, state="disabled", wrap="word", font=("Microsoft YaHei UI", 10))
        self.log.pack(fill="both", expand=True)
        self.clear_button = ttk.Button(main, text="已核对微信预约结果，解除提交锁", command=self.clear)
        self.clear_button.pack(anchor="e", pady=(8, 0))
        root.after(100, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda e: self.stop_event.set())

    def start(self, task):
        if self.worker and self.worker.is_alive():
            return
        plan = None
        if task == "select":
            try:
                plan = date.fromisoformat(self.fields["参观日期"].get().strip())
            except ValueError:
                messagebox.showerror("检查日期", "参观日期格式为 YYYY-MM-DD。")
                return
        if task == "run":
            if not self.agree.get():
                messagebox.showinfo("尚未启用", "请先在微信填好资料，并勾选允许自动提交。")
                return
            try:
                v = {k: x.get().strip() for k, x in self.fields.items()}
                plan = Plan(datetime.strptime(v["放号时间"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ),
                            date.fromisoformat(v["参观日期"]), v["时段"], float(v["间隔（秒）"]), float(v["持续（分钟）"]))
                plan.validate()
            except (ValueError, WindowError) as exc:
                messagebox.showerror("检查设置", str(exc))
                return
        self.stop_event.clear()
        for button in self.buttons + self.editors + [self.check, self.clear_button]:
            button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("正在检测；请保持微信预约页面打开。")
        self.worker = threading.Thread(target=self.work, args=(task, plan), daemon=True)
        self.worker.start()

    def work(self, task, plan):
        def report(text):
            self.events.put(("log", text))
        try:
            client = WindowClient(self.stop_event)
            client.bind()
            dates, count, enabled = client.inspect()
            report(f"已识别官方微信页面：{len(dates)} 个日期、{count} 个时段、{enabled} 个当前可选时段。")
            report("页面日期：" + " / ".join(dates))
            if task == "inspect":
                try:
                    client.ready_signature()
                    report("网页必填资料与须知勾选检查通过，可设置目标后开始。")
                except WindowError as exc:
                    report("窗口识别通过；准备提示：" + str(exc))
            elif task == "refresh":
                client.foreground()
                changed = client.refresh()
                report("已调用官方网页刷新按钮；没有选择时段或提交。" if changed else "网页正在刷新冷却，请稍后重试。")
            elif task == "select":
                client.foreground()
                found = client.select_day(plan)
                report("已选中并核实目标日期；没有选择时段或提交。" if found else "页面尚未显示目标日期。")
            else:
                run(client, plan, self.latch, self.stop_event, report)
        except WindowError as exc:
            report(str(exc))
        except Exception as exc:
            # Provider errors can contain field contents; expose only exception type.
            detail = ": " + str(exc.name) if isinstance(exc, ModuleNotFoundError) else ""
            report(f"窗口操作停止（{type(exc).__name__}{detail}）。请将微信预约页放到前台再检测；如已点击提交，先核对预约信息。")
        finally:
            self.events.put(("done", ""))

    def drain(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.status.set(text)
                    self.log.configure(state="normal")
                    self.log.insert("end", datetime.now(TZ).strftime("%H:%M:%S ") + text + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                else:
                    for button in self.buttons + self.editors + [self.check, self.clear_button]:
                        button.configure(state="normal")
                    self.editors[2].configure(state="readonly")
                    self.stop_button.configure(state="disabled")
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def clear(self):
        if not self.latch.exists():
            messagebox.showinfo("提交记录", "没有需要解除的提交锁。")
        elif messagebox.askyesno("确认已核对", "请先在微信“预约信息”核对是否已经预约。\n解除后允许再次点击提交，确定继续？"):
            self.latch.clear()
            self.status.set("已解除本机提交锁。")

    def close(self):
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.status.set("正在停止；等待当前控件调用返回。")
            self.root.after(150, self.close)
        else:
            self.root.destroy()


def main():
    # pywinauto also enables DPI awareness. Do so before Tk creates any windows.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
    # Keep the MTA alive across successive inspection/refresh worker threads.
    sys.coinit_flags = 0
    import comtypes
    comtypes.CoInitializeEx(0)
    if "--smoke-test" in sys.argv:
        # Exercise bundled dependencies and Tk without touching a WeChat window
        # or conflicting with an already-running interactive instance.
        from pywinauto.uia_defines import IUIA
        IUIA()
        root = tk.Tk()
        root.withdraw()
        App(root)
        root.after(400, root.destroy)
        root.mainloop()
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, "Local\\SztwtWeChatWindowMode-v1")
    if not mutex or ctypes.get_last_error() == 183:
        ctypes.windll.user32.MessageBoxW(None, "微信窗口模式已在运行。", "预约助手", 0)
        return
    root = tk.Tk()
    App(root)
    root.mainloop()
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
