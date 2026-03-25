"""
Android Event Collector
=======================
通过 ADB 连接 Android 设备，实时收集、过滤、导出 Android 事件日志。
依赖: pip install customtkinter
运行: python android_event_collector.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import queue
import re
import json
import csv
import os
import time
from datetime import datetime

# ── 主题配置 ──────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

C_BG        = "#0d1117"
C_CARD      = "#161b22"
C_BORDER    = "#30363d"
C_ACCENT    = "#58a6ff"
C_GREEN     = "#3fb950"
C_YELLOW    = "#d29922"
C_RED       = "#f85149"
C_PURPLE    = "#bc8cff"
C_ORANGE    = "#ffa657"
C_TEXT      = "#e6edf3"
C_MUTED     = "#8b949e"

# 事件标签颜色映射
TAG_COLORS = {
    "ActivityManager": C_ACCENT,
    "WindowManager":   C_PURPLE,
    "InputDispatcher": C_ORANGE,
    "Choreographer":   C_GREEN,
    "System":          C_YELLOW,
    "art":             C_MUTED,
    "DEBUG":           C_RED,
    "FATAL":           C_RED,
    "ERROR":           C_RED,
    "WARN":            C_YELLOW,
    "INFO":            C_GREEN,
    "DEFAULT":         C_TEXT,
}

LEVEL_COLORS = {
    "V": ("#8b949e", "Verbose"),
    "D": ("#58a6ff", "Debug"),
    "I": ("#3fb950", "Info"),
    "W": ("#d29922", "Warn"),
    "E": ("#f85149", "Error"),
    "F": ("#ff6b6b", "Fatal"),
}

# ── ADB 管理器 ─────────────────────────────────────────────────────────────────
class ADBManager:
    def __init__(self):
        self.process = None
        self.running = False

    def check_adb(self) -> bool:
        try:
            r = subprocess.run(["adb", "version"], capture_output=True, timeout=3)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_devices(self) -> list[dict]:
        devices = []
        try:
            r = subprocess.run(["adb", "devices", "-l"], capture_output=True,
                               text=True, timeout=5)
            lines = r.stdout.strip().split("\n")[1:]
            for line in lines:
                line = line.strip()
                if not line or "offline" in line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    info = {"serial": parts[0], "state": parts[1]}
                    for p in parts[2:]:
                        if ":" in p:
                            k, v = p.split(":", 1)
                            info[k] = v
                    devices.append(info)
        except Exception:
            pass
        return devices

    def get_device_props(self, serial: str) -> dict:
        props = {}
        keys = {
            "ro.product.model":   "model",
            "ro.product.brand":   "brand",
            "ro.build.version.release": "android",
            "ro.build.version.sdk":     "sdk",
            "ro.product.cpu.abi": "abi",
        }
        for prop, name in keys.items():
            try:
                r = subprocess.run(
                    ["adb", "-s", serial, "shell", "getprop", prop],
                    capture_output=True, text=True, timeout=3
                )
                props[name] = r.stdout.strip()
            except Exception:
                props[name] = "N/A"
        return props

    def start_logcat(self, serial: str, buffer: str = "main",
                     level: str = "*:V") -> subprocess.Popen | None:
        try:
            cmd = ["adb", "-s", serial, "logcat", "-v", "threadtime",
                   "-b", buffer, level]
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1
            )
            self.running = True
            return self.process
        except Exception as e:
            print(f"logcat start error: {e}")
            return None

    def start_getevent(self, serial: str) -> subprocess.Popen | None:
        try:
            cmd = ["adb", "-s", serial, "shell", "getevent", "-lt"]
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1
            )
            self.running = True
            return self.process
        except Exception as e:
            print(f"getevent start error: {e}")
            return None

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def clear_logcat(self, serial: str):
        try:
            subprocess.run(["adb", "-s", serial, "logcat", "-c"],
                           capture_output=True, timeout=3)
        except Exception:
            pass


# ── 日志解析器 ─────────────────────────────────────────────────────────────────
class LogParser:
    # threadtime 格式: MM-DD HH:MM:SS.mmm  PID  TID LEVEL TAG: MSG
    LOGCAT_RE = re.compile(
        r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+"
        r"(\d+)\s+(\d+)\s+([VDIWEF])\s+([\w./\-:]+)\s*:\s*(.*)$"
    )
    # getevent: [ timestamp] /dev/input/eventN  TYPE  CODE  VALUE
    GETEVENT_RE = re.compile(
        r"^\[\s*([\d.]+)\]\s+(/dev/input/\S+)\s+(\S+)\s+(\S+)\s+(\S+)$"
    )

    @staticmethod
    def parse_logcat(line: str) -> dict | None:
        m = LogParser.LOGCAT_RE.match(line.strip())
        if not m:
            return None
        return {
            "type":      "logcat",
            "time":      m.group(1),
            "pid":       m.group(2),
            "tid":       m.group(3),
            "level":     m.group(4),
            "tag":       m.group(5).strip(),
            "message":   m.group(6),
            "raw":       line,
        }

    @staticmethod
    def parse_getevent(line: str) -> dict | None:
        m = LogParser.GETEVENT_RE.match(line.strip())
        if not m:
            return None
        return {
            "type":    "getevent",
            "time":    m.group(1),
            "device":  m.group(2),
            "ev_type": m.group(3),
            "code":    m.group(4),
            "value":   m.group(5),
            "raw":     line,
        }


# ── 主应用 ─────────────────────────────────────────────────────────────────────
class AndroidEventCollector(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Android Event Collector")
        self.geometry("1280x800")
        self.minsize(900, 600)
        self.configure(fg_color=C_BG)

        self.adb      = ADBManager()
        self.parser   = LogParser()
        self.log_queue: queue.Queue = queue.Queue()
        self.all_logs: list[dict]   = []
        self.paused   = False
        self.capture_mode = ctk.StringVar(value="logcat")
        self.selected_serial = ctk.StringVar(value="")
        self.filter_text = ctk.StringVar()
        self.filter_level = ctk.StringVar(value="All")
        self.filter_tag  = ctk.StringVar()
        self.max_lines   = 5000

        self._build_ui()
        self._refresh_devices()
        self.after(100, self._drain_queue)

    # ── UI 构建 ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── 顶部标题栏 ──
        topbar = ctk.CTkFrame(self, fg_color=C_CARD, height=52, corner_radius=0)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        ctk.CTkLabel(topbar, text="⚡ Android Event Collector",
                     font=("Courier New", 16, "bold"),
                     text_color=C_ACCENT).pack(side="left", padx=20, pady=12)

        self.status_label = ctk.CTkLabel(
            topbar, text="● ADB 检测中...",
            font=("Courier New", 12), text_color=C_MUTED)
        self.status_label.pack(side="right", padx=20)

        self.counter_label = ctk.CTkLabel(
            topbar, text="0 条日志",
            font=("Courier New", 11), text_color=C_MUTED)
        self.counter_label.pack(side="right", padx=10)

        # ── 主区域 (左控制 + 右日志) ──
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=0, pady=0)

        # 左侧控制面板
        self._build_sidebar(main)

        # 右侧日志区域
        right = ctk.CTkFrame(main, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        self._build_filter_bar(right)
        self._build_log_view(right)
        self._build_status_bar(right)

        self._check_adb_async()

    def _build_sidebar(self, parent):
        sidebar = ctk.CTkFrame(parent, fg_color=C_CARD, width=260, corner_radius=8)
        sidebar.pack(side="left", fill="y", padx=8, pady=8)
        sidebar.pack_propagate(False)

        # 设备区
        self._section(sidebar, "📱 设备")

        dev_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        dev_row.pack(fill="x", padx=12, pady=(0, 4))

        self.device_combo = ctk.CTkComboBox(
            dev_row, variable=self.selected_serial,
            values=["(无设备)"], font=("Courier New", 11),
            fg_color=C_BG, border_color=C_BORDER, button_color=C_BORDER,
            dropdown_fg_color=C_CARD, width=160,
            command=self._on_device_select)
        self.device_combo.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(dev_row, text="↻", width=32, height=28,
                      fg_color=C_BORDER, hover_color="#444c56",
                      font=("Courier New", 14),
                      command=self._refresh_devices).pack(side="left", padx=(4,0))

        # 设备信息
        self.device_info = ctk.CTkTextbox(
            sidebar, height=90, font=("Courier New", 10),
            fg_color=C_BG, text_color=C_MUTED,
            border_color=C_BORDER, border_width=1)
        self.device_info.pack(fill="x", padx=12, pady=(0, 8))
        self.device_info.insert("end", "选择设备后显示信息...")
        self.device_info.configure(state="disabled")

        # 采集模式
        self._section(sidebar, "🎯 采集模式")

        mode_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        mode_frame.pack(fill="x", padx=12, pady=(0, 8))

        for label, val in [("Logcat", "logcat"), ("getevent", "getevent")]:
            ctk.CTkRadioButton(
                mode_frame, text=label, variable=self.capture_mode, value=val,
                font=("Courier New", 12), text_color=C_TEXT,
                fg_color=C_ACCENT, hover_color=C_ACCENT
            ).pack(anchor="w", pady=2)

        # Logcat 选项
        self.logcat_opts = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.logcat_opts.pack(fill="x", padx=12, pady=(0, 4))

        self._label(self.logcat_opts, "缓冲区")
        self.buffer_var = ctk.StringVar(value="main")
        buf_menu = ctk.CTkOptionMenu(
            self.logcat_opts, variable=self.buffer_var,
            values=["main", "system", "crash", "events", "all"],
            fg_color=C_BG, button_color=C_BORDER,
            dropdown_fg_color=C_CARD, font=("Courier New", 11))
        buf_menu.pack(fill="x", pady=(0, 6))

        self._label(self.logcat_opts, "最低级别")
        self.min_level_var = ctk.StringVar(value="*:V")
        lvl_menu = ctk.CTkOptionMenu(
            self.logcat_opts, variable=self.min_level_var,
            values=["*:V", "*:D", "*:I", "*:W", "*:E", "*:F"],
            fg_color=C_BG, button_color=C_BORDER,
            dropdown_fg_color=C_CARD, font=("Courier New", 11))
        lvl_menu.pack(fill="x", pady=(0, 4))

        # 控制按钮
        self._section(sidebar, "⚙ 控制")

        btn_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 8))

        self.start_btn = ctk.CTkButton(
            btn_frame, text="▶ 开始采集",
            fg_color=C_GREEN, hover_color="#2ea043",
            font=("Courier New", 13, "bold"), height=36,
            command=self._start_capture)
        self.start_btn.pack(fill="x", pady=(0, 6))

        self.stop_btn = ctk.CTkButton(
            btn_frame, text="■ 停止",
            fg_color=C_RED, hover_color="#da3633",
            font=("Courier New", 13, "bold"), height=36,
            state="disabled", command=self._stop_capture)
        self.stop_btn.pack(fill="x", pady=(0, 6))

        self.pause_btn = ctk.CTkButton(
            btn_frame, text="⏸ 暂停显示",
            fg_color=C_BORDER, hover_color="#444c56",
            font=("Courier New", 12), height=32,
            command=self._toggle_pause)
        self.pause_btn.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            btn_frame, text="🗑 清空日志",
            fg_color=C_BORDER, hover_color="#444c56",
            font=("Courier New", 12), height=32,
            command=self._clear_logs).pack(fill="x")

        # 导出
        self._section(sidebar, "💾 导出")

        exp_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        exp_frame.pack(fill="x", padx=12, pady=(0, 8))

        for label, fn in [("导出 TXT", self._export_txt),
                           ("导出 JSON", self._export_json),
                           ("导出 CSV", self._export_csv)]:
            ctk.CTkButton(exp_frame, text=label, command=fn,
                          fg_color=C_BORDER, hover_color="#444c56",
                          font=("Courier New", 11), height=28
                          ).pack(fill="x", pady=2)

        # 底部
        ctk.CTkLabel(sidebar, text="v1.0  ADB Event Collector",
                     font=("Courier New", 9), text_color=C_BORDER
                     ).pack(side="bottom", pady=8)

    def _build_filter_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=8, height=48)
        bar.pack(fill="x", pady=(0, 6))
        bar.pack_propagate(False)

        ctk.CTkLabel(bar, text="过滤:", font=("Courier New", 12),
                     text_color=C_MUTED).pack(side="left", padx=(12, 6), pady=10)

        ctk.CTkEntry(bar, textvariable=self.filter_text,
                     placeholder_text="关键词 / 正则",
                     font=("Courier New", 12),
                     fg_color=C_BG, border_color=C_BORDER,
                     width=200, height=30
                     ).pack(side="left", padx=4)

        ctk.CTkLabel(bar, text="Tag:", font=("Courier New", 12),
                     text_color=C_MUTED).pack(side="left", padx=(8, 4))
        ctk.CTkEntry(bar, textvariable=self.filter_tag,
                     placeholder_text="Tag 名称",
                     font=("Courier New", 12),
                     fg_color=C_BG, border_color=C_BORDER,
                     width=140, height=30
                     ).pack(side="left", padx=4)

        ctk.CTkLabel(bar, text="级别:", font=("Courier New", 12),
                     text_color=C_MUTED).pack(side="left", padx=(8, 4))
        ctk.CTkOptionMenu(
            bar, variable=self.filter_level,
            values=["All", "Verbose", "Debug", "Info", "Warn", "Error", "Fatal"],
            fg_color=C_BG, button_color=C_BORDER,
            dropdown_fg_color=C_CARD,
            font=("Courier New", 11), width=90, height=30
        ).pack(side="left", padx=4)

        ctk.CTkButton(bar, text="应用过滤", command=self._apply_filter,
                      fg_color=C_ACCENT, hover_color="#4080e0",
                      font=("Courier New", 11), width=80, height=30
                      ).pack(side="left", padx=(8, 4))

        ctk.CTkButton(bar, text="重置", command=self._reset_filter,
                      fg_color=C_BORDER, hover_color="#444c56",
                      font=("Courier New", 11), width=60, height=30
                      ).pack(side="left", padx=4)

        self.autoscroll_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar, text="自动滚动",
                        variable=self.autoscroll_var,
                        font=("Courier New", 11),
                        fg_color=C_ACCENT, hover_color=C_ACCENT
                        ).pack(side="right", padx=12)

    def _build_log_view(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=8)
        frame.pack(fill="both", expand=True)

        # 使用 tk.Text 以支持彩色标签
        self.log_text = tk.Text(
            frame,
            bg=C_BG, fg=C_TEXT,
            font=("Courier New", 11),
            insertbackground=C_TEXT,
            selectbackground="#264f78",
            relief="flat", bd=0,
            wrap="none",
            state="disabled",
            exportselection=True,
        )

        vsb = ctk.CTkScrollbar(frame, command=self.log_text.yview)
        vsb.pack(side="right", fill="y", padx=(0, 4), pady=4)

        hsb = ctk.CTkScrollbar(frame, orientation="horizontal",
                                command=self.log_text.xview)
        hsb.pack(side="bottom", fill="x", padx=4, pady=(0, 4))

        self.log_text.configure(
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # 颜色标签
        for level, (color, _) in LEVEL_COLORS.items():
            self.log_text.tag_configure(f"level_{level}", foreground=color)
        for tag_name, color in TAG_COLORS.items():
            self.log_text.tag_configure(f"tag_{tag_name}", foreground=color)
        self.log_text.tag_configure("time",    foreground="#5c6370")
        self.log_text.tag_configure("pid",     foreground="#4a5568")
        self.log_text.tag_configure("muted",   foreground=C_MUTED)
        self.log_text.tag_configure("ev_type", foreground=C_PURPLE)
        self.log_text.tag_configure("ev_val",  foreground=C_ORANGE)
        self.log_text.tag_configure("device",  foreground=C_ACCENT)

    def _build_status_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=8, height=28)
        bar.pack(fill="x", pady=(6, 0))
        bar.pack_propagate(False)

        self.capture_status = ctk.CTkLabel(
            bar, text="● 空闲", font=("Courier New", 11),
            text_color=C_MUTED)
        self.capture_status.pack(side="left", padx=12)

        self.visible_count = ctk.CTkLabel(
            bar, text="显示: 0 条", font=("Courier New", 11),
            text_color=C_MUTED)
        self.visible_count.pack(side="right", padx=12)

    # ── 辅助构建方法 ───────────────────────────────────────────────────────────
    def _section(self, parent, text):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(f, text=text, font=("Courier New", 11, "bold"),
                     text_color=C_MUTED).pack(side="left")
        ctk.CTkFrame(f, height=1, fg_color=C_BORDER).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=0)

    def _label(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=("Courier New", 10),
                     text_color=C_MUTED).pack(anchor="w", pady=(4, 1))

    # ── ADB 检测 ───────────────────────────────────────────────────────────────
    def _check_adb_async(self):
        def check():
            ok = self.adb.check_adb()
            self.after(0, lambda: self._update_adb_status(ok))
        threading.Thread(target=check, daemon=True).start()

    def _update_adb_status(self, ok: bool):
        if ok:
            self.status_label.configure(text="● ADB 已就绪", text_color=C_GREEN)
        else:
            self.status_label.configure(text="● ADB 未找到", text_color=C_RED)
            messagebox.showwarning("ADB 未找到",
                "未检测到 adb 命令。\n请安装 Android SDK Platform-Tools 并添加至 PATH。")

    def _refresh_devices(self):
        def fetch():
            devices = self.adb.get_devices()
            self.after(0, lambda: self._update_devices(devices))
        threading.Thread(target=fetch, daemon=True).start()

    def _update_devices(self, devices: list[dict]):
        if not devices:
            self.device_combo.configure(values=["(无设备)"])
            self.selected_serial.set("(无设备)")
            return
        serials = [d["serial"] for d in devices]
        self.device_combo.configure(values=serials)
        self.selected_serial.set(serials[0])
        self._on_device_select(serials[0])

    def _on_device_select(self, serial: str):
        if serial in ("(无设备)", ""):
            return
        def fetch():
            props = self.adb.get_device_props(serial)
            self.after(0, lambda: self._show_device_info(props))
        threading.Thread(target=fetch, daemon=True).start()

    def _show_device_info(self, props: dict):
        self.device_info.configure(state="normal")
        self.device_info.delete("1.0", "end")
        lines = [
            f"型号: {props.get('model','N/A')}",
            f"品牌: {props.get('brand','N/A')}",
            f"Android: {props.get('android','N/A')}  SDK {props.get('sdk','N/A')}",
            f"ABI: {props.get('abi','N/A')}",
        ]
        self.device_info.insert("end", "\n".join(lines))
        self.device_info.configure(state="disabled")

    # ── 采集控制 ───────────────────────────────────────────────────────────────
    def _start_capture(self):
        serial = self.selected_serial.get()
        if not serial or serial == "(无设备)":
            messagebox.showwarning("无设备", "请先选择一个已连接的设备。")
            return

        self.adb.stop()
        mode = self.capture_mode.get()

        if mode == "logcat":
            proc = self.adb.start_logcat(
                serial, self.buffer_var.get(), self.min_level_var.get())
        else:
            proc = self.adb.start_getevent(serial)

        if not proc:
            messagebox.showerror("启动失败", "无法启动采集进程，请检查设备连接。")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.capture_status.configure(
            text=f"● {mode} 采集中 · {serial}", text_color=C_GREEN)

        threading.Thread(target=self._read_proc, args=(proc, mode),
                         daemon=True).start()

    def _read_proc(self, proc: subprocess.Popen, mode: str):
        for line in proc.stdout:
            if not self.adb.running:
                break
            line = line.rstrip("\n")
            if not line:
                continue
            parsed = (self.parser.parse_logcat(line) if mode == "logcat"
                      else self.parser.parse_getevent(line))
            entry = parsed or {"type": "raw", "raw": line,
                               "time": "", "level": "V", "tag": "", "message": line}
            self.log_queue.put(entry)

    def _stop_capture(self):
        self.adb.stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.capture_status.configure(text="● 已停止", text_color=C_YELLOW)

    def _toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.configure(
            text="▶ 继续显示" if self.paused else "⏸ 暂停显示",
            fg_color=C_YELLOW if self.paused else C_BORDER)

    def _clear_logs(self):
        self.all_logs.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._update_counter()

    # ── 队列消费 ───────────────────────────────────────────────────────────────
    def _drain_queue(self):
        batch = 0
        while not self.log_queue.empty() and batch < 200:
            entry = self.log_queue.get_nowait()
            self.all_logs.append(entry)
            if len(self.all_logs) > self.max_lines * 2:
                self.all_logs = self.all_logs[-self.max_lines:]
            if not self.paused and self._passes_filter(entry):
                self._append_line(entry)
            batch += 1
        if batch:
            self._update_counter()
        self.after(80, self._drain_queue)

    # ── 渲染日志行 ─────────────────────────────────────────────────────────────
    def _append_line(self, entry: dict):
        self.log_text.configure(state="normal")

        # 限制行数
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > self.max_lines:
            self.log_text.delete("1.0", "500.0")

        if entry["type"] == "logcat":
            self._render_logcat(entry)
        elif entry["type"] == "getevent":
            self._render_getevent(entry)
        else:
            self.log_text.insert("end", entry["raw"] + "\n", "muted")

        self.log_text.configure(state="disabled")

        if self.autoscroll_var.get():
            self.log_text.see("end")

    def _render_logcat(self, e: dict):
        t = self.log_text
        level = e.get("level", "V")
        tag   = e.get("tag", "")
        level_color = f"level_{level}"
        tag_color   = f"tag_{tag}" if tag in TAG_COLORS else f"level_{level}"

        t.insert("end", e["time"] + " ", "time")
        t.insert("end", f"{e['pid']:>5}/{e['tid']:<5} ", "pid")
        t.insert("end", f"[{level}] ", level_color)
        t.insert("end", f"{tag:<25} ", tag_color)
        t.insert("end", e["message"] + "\n", level_color)

    def _render_getevent(self, e: dict):
        t = self.log_text
        t.insert("end", f"[{e['time']:>12}] ", "time")
        t.insert("end", f"{e['device']:<22} ", "device")
        t.insert("end", f"{e['ev_type']:<12} ", "ev_type")
        t.insert("end", f"{e['code']:<16} ", "muted")
        t.insert("end", e["value"] + "\n", "ev_val")

    # ── 过滤 ───────────────────────────────────────────────────────────────────
    def _passes_filter(self, entry: dict) -> bool:
        kw = self.filter_text.get().strip()
        if kw:
            try:
                if not re.search(kw, entry.get("raw", ""), re.IGNORECASE):
                    return False
            except re.error:
                if kw.lower() not in entry.get("raw", "").lower():
                    return False

        tag_f = self.filter_tag.get().strip()
        if tag_f and tag_f.lower() not in entry.get("tag", "").lower():
            return False

        lvl_f = self.filter_level.get()
        if lvl_f != "All":
            lvl_map = {"Verbose":"V","Debug":"D","Info":"I",
                       "Warn":"W","Error":"E","Fatal":"F"}
            order = list(lvl_map.values())
            entry_lvl = entry.get("level", "V")
            min_idx   = order.index(lvl_map.get(lvl_f, "V"))
            if entry_lvl not in order or order.index(entry_lvl) < min_idx:
                return False
        return True

    def _apply_filter(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        visible = 0
        for entry in self.all_logs:
            if self._passes_filter(entry):
                self._append_line(entry)
                visible += 1
        self.log_text.configure(state="disabled")
        self.visible_count.configure(text=f"显示: {visible} 条")

    def _reset_filter(self):
        self.filter_text.set("")
        self.filter_tag.set("")
        self.filter_level.set("All")
        self._apply_filter()

    def _update_counter(self):
        total = len(self.all_logs)
        self.counter_label.configure(text=f"{total} 条日志")

    # ── 导出 ───────────────────────────────────────────────────────────────────
    def _get_filtered(self) -> list[dict]:
        return [e for e in self.all_logs if self._passes_filter(e)]

    def _export_txt(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            initialfile=f"android_events_{datetime.now():%Y%m%d_%H%M%S}.txt")
        if not path:
            return
        logs = self._get_filtered()
        with open(path, "w", encoding="utf-8") as f:
            for e in logs:
                f.write(e["raw"] + "\n")
        messagebox.showinfo("导出成功", f"已导出 {len(logs)} 条日志\n{path}")

    def _export_json(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialfile=f"android_events_{datetime.now():%Y%m%d_%H%M%S}.json")
        if not path:
            return
        logs = self._get_filtered()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("导出成功", f"已导出 {len(logs)} 条日志\n{path}")

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialfile=f"android_events_{datetime.now():%Y%m%d_%H%M%S}.csv")
        if not path:
            return
        logs = self._get_filtered()
        if not logs:
            return
        fieldnames = list(logs[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(logs)
        messagebox.showinfo("导出成功", f"已导出 {len(logs)} 条日志\n{path}")

    # ── 关闭 ───────────────────────────────────────────────────────────────────
    def on_close(self):
        self.adb.stop()
        self.destroy()


# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = AndroidEventCollector()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
