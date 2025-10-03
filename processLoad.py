# processLoad_merged_part1.py
# ---------------------------
# Imports (original + additions)
# ---------------------------winreg
import sys
import os
import psutil
import shutil
import platform
import subprocess
import winreg
import json
import hashlib
import threading
import time
import ctypes
import traceback
from datetime import datetime
from functools import partial
from collections import deque, defaultdict

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTabWidget, QGroupBox, QSpinBox, QMenu, QMessageBox, QTextEdit,
    QHeaderView, QToolButton , QPlainTextEdit , QSplitter ,QCheckBox
)
from PyQt6.QtGui import QColor, QFont, QAction
from PyQt6.QtCore import Qt, QTimer

# Optional WMI import (if present).
try:
    import wmi
except Exception:
    wmi = None

# -----------
# Constants
# -----------
APP_TITLE = "Century EVO Performance Monitor"
REPORT_FILE = "performance_report_gui.txt"
REFRESH_MS_DEFAULT = 5000  # 5 seconds
LOG_FILE = "ai_engine.log"

# -----------------------
# Utility helpers (unchanged)
# -----------------------
def safe_log(msg: str):
    """Log both to console and file."""
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def bytes_to_gb(n):
    try:
        return round(n / (1024 ** 3), 2)
    except Exception:
        return 0.0

def is_system_process_name(name):
    if not name:
        return True
    low = name.lower()
    if "idle" in low or low in ("system", "system idle process", "ntoskrnl.exe"):
        return True
    return False

def backup_registry_value(hive, path, name, backup_dir="startup_backups"):
    try:
        os.makedirs(backup_dir, exist_ok=True)
        with winreg.OpenKey(hive, path) as key:
            val, t = winreg.QueryValueEx(key, name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = f"{name}_{timestamp}.reg"
            dest = os.path.join(backup_dir, safe_name)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(f"Name: {name}\nValue: {val}\nType: {t}\n")
        return dest
    except Exception:
        return None

# -----------------------
# Suggestion Engine (original, preserved)
# -----------------------
class SuggestionEngine:
    def __init__(self):
        self.svc_cpu_threshold = 3.0
        self.svc_mem_threshold = 5.0
        self.startup_cpu_threshold = 5.0
        self.startup_mem_threshold = 5.0
        # Auto-learning storage
        self.pref_file = "user_prefs.json"
        if os.path.exists(self.pref_file):
            with open(self.pref_file, "r") as f:
                try:
                    self.user_prefs = json.load(f)
                except Exception:
                    self.user_prefs = {"whitelist": [], "blacklist": []}
        else:
            self.user_prefs = {"whitelist": [], "blacklist": []}

    def save_prefs(self):
        try:
            with open(self.pref_file, "w") as f:
                json.dump(self.user_prefs, f, indent=2)
        except Exception:
            pass

    def list_registry_startup(self):
        results = []
        reg_paths = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        ]
        for hive, path in reg_paths:
            try:
                with winreg.OpenKey(hive, path) as key:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                            results.append((hive, path, name, value))
                            i += 1
                        except OSError:
                            break
            except Exception:
                continue
        if wmi:
            try:
                c = wmi.WMI()
                for s in c.Win32_StartupCommand():
                    results.append((None, None, s.Name, s.Command))
            except Exception:
                pass
        return results

    def list_services(self):
        services = []
        try:
            for svc in psutil.win_service_iter():
                try:
                    services.append(svc.as_dict())
                except Exception:
                    continue
        except Exception:
            pass
        return services

    def scan_for_suggestions(self, proc_map):
        """
        Smart suggestion engine:
            1. Dynamic scoring system (CPU, RAM, Disk, Net, risk).
            2. Category-based actions (mapped automatically).
            3. Auto-learning feedback loop (whitelist/blacklist).
            4. Context-aware filtering (battery, disk space, fullscreen).
        """
        suggestions = []

        # === Ensure prefs always exist ===
        if not hasattr(self, "user_prefs"):
            self.user_prefs = {"whitelist": [], "blacklist": []}
        whitelist = set(self.user_prefs.get("whitelist", []))
        blacklist = set(self.user_prefs.get("blacklist", []))

        def score_item(cpu, mem, disk=0, net=0, startup=False, suspicious=False):
            """Weighted score calculation."""
            base = (cpu * 2) + (mem * 0.5) + (disk * 0.3) + (net * 0.3)
            if startup:
                base += 15
            if suspicious:
                base += 20
            return min(100, int(base))

        # === Context-aware modifiers ===
        try:
            battery = psutil.sensors_battery()
            disk_usage = shutil.disk_usage("C:\\") if os.name == 'nt' else shutil.disk_usage(os.path.expanduser("~"))
            disk_pct = (disk_usage.used / disk_usage.total) * 100
            fullscreen = self.is_fullscreen_app_running() if hasattr(self, "is_fullscreen_app_running") else False
        except Exception:
            battery, disk_pct, fullscreen = None, 50, False

        # --- Registry Startup ---
        for entry in self.list_registry_startup():
            hive, path, name, cmd = entry
            if name in whitelist:
                continue  # Skip safe apps
            suspicious = "update" not in (name or "").lower() and "microsoft" not in (cmd or "").lower()

            matched = None
            for pid, info in proc_map.items():
                nm = info[0] or ""
                if name and name.lower() in nm.lower() or (cmd and nm.lower() in str(cmd).lower()):
                    matched = (pid, info)
                    break

            if matched:
                pid, info = matched
                cpu = info[1] if len(info) > 1 else 0
                mem = info[2] if len(info) > 2 else 0
                disk_kb = info[4] if len(info) > 4 else 0
                net_ops = info[5] if len(info) > 5 else 0
                s = score_item(cpu, mem, disk_kb, net_ops, startup=True, suspicious=suspicious)
                # Context boosts
                if battery and getattr(battery, "percent", None) is not None and battery.percent < 20:
                    s += 10
                if disk_pct > 90:
                    s += 10
                if name in blacklist or s >= 60:
                    suggestions.append(
                        (
                            name,
                            "Startup App",
                            f"PID {pid} | CPU {cpu:.1f}% | RAM {mem:.1f} MB | [Score {s}]",
                            " Disable from startup (backup recommended)",
                            partial(self.disable_startup_entry, hive, path, name),
                        )
                    )
                else:
                    suggestions.append(
                        (
                            name,
                            "Startup App",
                            f"PID {pid} | [Score {s}] | Cmd: {cmd}",
                            "ℹ️ Optional: disable if unnecessary",
                            lambda: (False, "Safe – no auto action"),
                        )
                    )
            else:
                # Always include a score, even if not running
                s = 0 if name in whitelist else 20
                suggestions.append(
                    (
                        name,
                        "Startup App",
                        f"[Score {s}] Not running | Cmd: {cmd}",
                        "ℹ️ Can disable from startup if not needed",
                        partial(self.disable_startup_entry, hive, path, name),
                    )
                )

        # --- Services ---
        for svc in self.list_services():
            try:
                svc_name = svc.get("name") or svc.get("display_name") or ""
                binpath = svc.get("binpath", "") or svc.get("path", "")
                if svc_name in whitelist:
                    continue
                suspicious = "unknown" in svc_name.lower() or "temp" in binpath.lower()

                matched = None
                for pid, info in proc_map.items():
                    nm = info[0] or ""
                    if nm and nm.lower() in str(binpath).lower():
                        matched = (pid, info)
                        break

                if matched:
                    pid, info = matched
                    cpu = info[1]
                    mem = info[2]
                    disk_kb = info[4] if len(info) > 4 else 0
                    net_ops = info[5] if len(info) > 5 else 0
                    s = score_item(cpu, mem, disk_kb, net_ops, suspicious=suspicious)
                    if fullscreen and s < 70:
                        s -= 15
                    if svc_name in blacklist or s >= 50:
                        suggestions.append(
                            (
                                svc_name,
                                "Service",
                                f"PID {pid} | CPU {cpu:.1f}% | RAM {mem:.1f} MB | [Score {s}]",
                                "⚠️ Stop & disable service (if non-critical)",
                                partial(self.stop_and_disable_service, svc_name),
                            )
                        )
                    else:
                        suggestions.append(
                            (
                                svc_name,
                                "Service",
                                f"PID {pid} | [Score {s}] | Running safely",
                                "ℹ️ Leave running (safe)",
                                lambda: (False, "Service safe – no action"),
                            )
                        )
                else:
                    s = 0
                    suggestions.append(
                        (
                            svc_name,
                            "Service",
                            f"[Score {s}] Not active | BinPath: {binpath}",
                            "ℹ️ Optional: disable from services.msc",
                            lambda: (False, "Service not active"),
                        )
                    )
            except Exception:
                continue

        # --- Sorting: highest score first ---
        def _extract_score(txt):
            try:
                if "Score" in str(txt):
                    return int(str(txt).split("Score")[-1].split("]")[0].strip())
                return 0
            except Exception:
                return 0

        suggestions.sort(key=lambda x: _extract_score(x[2]), reverse=True)
        if not suggestions:
            suggestions.append(("System OK", "Info", "-", "✅ No actionable suggestions found", lambda: (False, "No action")))
        return suggestions

    def disable_startup_entry(self, hive, path, name):
        try:
            backup = backup_registry_value(hive, path, name)
            with winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS) as key:
                winreg.DeleteValue(key, name)
            return True, f"Startup entry removed: {name}\nBackup: {backup}"
        except Exception as e:
            return False, f"Failed to remove startup entry: {e}"

    def stop_and_disable_service(self, svc_name):
        try:
            subprocess.run(["sc", "stop", svc_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["sc", "config", svc_name, "start=", "disabled"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return True, f"Service {svc_name} stop/disable attempted."
        except Exception as e:
            return False, f"Failed to stop/disable service: {e}"

# -----------------------
# Main GUI
# -----------------------
from ai_suggestion_engine import AISuggestionEngine
class PerformanceMonitorGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 810)
        self.setFont(QFont("Consolas", 10))
        self.refresh_interval_ms = REFRESH_MS_DEFAULT
        self.prev_disk = {}
        self.prev_net = {}
        self.prev_disk_io = None
        self.proc_map = {}
        self.sugg = SuggestionEngine()
        self.sugg_ai = AISuggestionEngine()

        # ---------- AI Engine placeholder (will be instantiated on demand) ----------
        self.ai_engine = AISuggestionEngine()
        self._ai_thread = None
        self._ai_lock = threading.RLock()

        self._build_ui()

        # 💾 Clear previous log on startup
        open(LOG_FILE, "w").close()
        self.suggestions_status.append("[Init] AI Engine ready. Logs: ai_engine.log")

        # ✅ Apply dark neon stylesheet globally
        self.setStyleSheet(
            """
            QMainWindow { background-color: #000; }
            QWidget { background-color: #000; color: #39FF14; }
            QTabWidget::pane { border: 1px solid #39FF14; background: #000; }
            QTabBar::tab { background: #111; color: #39FF14; padding: 6px 12px; border: 1px solid #39FF14; }
            QTabBar::tab:selected { background: #39FF14; color: #000; }
            QTableWidget { background-color: #000; alternate-background-color: #000; color: #39FF14; gridline-color: #39FF14; selection-background-color: #39FF14; selection-color: #000; }
            QHeaderView::section { background-color: #111; color: #fff; border: 1px solid #39FF14; padding: 4px; }
            QToolButton { background-color: #000; color: #39FF14; border: 1px solid #39FF14; padding: 3px 8px; }
            QToolButton:hover { background-color: #39FF14; color: #000; }
            QMenu { background-color: #000; color: #39FF14; border: 1px solid #39FF14; }
            QMenu::item:selected { background-color: #39FF14; color: #000; }
            QTextEdit, QPlainTextEdit { background-color: #000; color: #39FF14; border: 1px solid #39FF14; }
            QLabel { color: #39FF14; }
            QPushButton { background-color: #111; color: #39FF14; border: 1px solid #39FF14; padding: 4px 10px; }
            QPushButton:hover { background-color: #39FF14; color: #000; }
            """
        )

        self.timer = QTimer()
        self.timer.timeout.connect(self._refresh_dashboard)
        self.timer.start(self.refresh_interval_ms)

    def _build_ui(self):
        self.tabs = QTabWidget()
        self._build_dashboard_tab()
        self._build_top_performance_tab()
        self._build_services_tab()
        self._build_startup_tab()
        self._build_suggestions_tab()
        main_layout = QVBoxLayout()
        title = QLabel("Windows Performance Monitor")
        title.setFont(QFont("Consolas", 12))
        main_layout.addWidget(title)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

    def _build_dashboard_tab(self):
        dash = QWidget()
        layout = QVBoxLayout(dash)

        info_box = QGroupBox()
        info_layout = QHBoxLayout()
        self.sys_info_label = QLabel("Loading system info…")
        info_layout.addWidget(self.sys_info_label)

        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("Refresh (s):"))
        self.spin_refresh = QSpinBox()
        self.spin_refresh.setRange(1, 60)
        self.spin_refresh.setValue(self.refresh_interval_ms // 1000)
        self.spin_refresh.valueChanged.connect(self._on_refresh_change)
        ctrl_layout.addWidget(self.spin_refresh)

        self.btn_export = QPushButton("Export Report")
        self.btn_export.clicked.connect(self._export_report)
        ctrl_layout.addWidget(self.btn_export)

        info_layout.addLayout(ctrl_layout)
        info_box.setLayout(info_layout)
        layout.addWidget(info_box)

        self.table_dashboard = QTableWidget()
        self.table_dashboard.setColumnCount(8)
        self.table_dashboard.setHorizontalHeaderLabels(
            ["PID", "Name", "CPU %", "MEM %", "Disk R/W KB", "Net Ops", "Score", "Path(Hidden)"]
        )
        self.table_dashboard.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_dashboard.setColumnHidden(7, True)
        self.table_dashboard.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_dashboard.customContextMenuRequested.connect(self._show_proc_context_menu)
        self.table_dashboard.cellClicked.connect(self._proc_table_show_info)
        layout.addWidget(self.table_dashboard)

        culpl = QHBoxLayout()
        self.label_culprit = QLabel("Selected Process: (none)")
        culpl.addWidget(self.label_culprit)
        culpl.addStretch()
        self.btn_kill = QPushButton("Kill Selected Process")
        self.btn_kill.clicked.connect(self._kill_selected_process)
        culpl.addWidget(self.btn_kill)
        layout.addLayout(culpl)

        self.tabs.addTab(dash, "Dashboard")

    def _build_top_performance_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.top_perf_table = QTableWidget()
        self.top_perf_table.setColumnCount(8)
        self.top_perf_table.setHorizontalHeaderLabels(
            ["Metric", "PID", "Name", "CPU %", "MEM %", "Disk KB", "Net Ops", "Score"]
        )
        self.top_perf_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Neon Dark Theme Styling
        self.top_perf_table.setStyleSheet(
            """
        QTableWidget { background-color: #000000; color: #39FF14; /* Neon Green */ gridline-color: #222; font: 10pt "Consolas"; selection-background-color: #111; selection-color: #FFFFFF; }
        QHeaderView::section { background-color: #111; color: #39FF14; padding: 4px; border: 1px solid #222; font: bold 10pt "Consolas"; }
        """
        )
        # Store partition indices for later insertion in update function
        self.partition_rows = {5, 11, 16}
        layout.addWidget(self.top_perf_table)
        self.tabs.addTab(tab, "Top Performance")

    def _build_startup_tab(self):
        # Create Startup tab
        self.startup_tab = QWidget()
        layout = QVBoxLayout(self.startup_tab)

        # --- Top Controls Row ---
        top_bar = QHBoxLayout()

        # Refresh Button
        btn_refresh = QPushButton("Refresh Startup Entries")
        btn_refresh.clicked.connect(self._update_startup_tab)
        top_bar.addWidget(btn_refresh)

        # ✅ Checkbox: Show only Enabled
        self.chk_startup_enabled_only = QCheckBox("Show only Enabled")
        self.chk_startup_enabled_only.setChecked(False)
        self.chk_startup_enabled_only.stateChanged.connect(self._update_startup_tab)
        top_bar.addWidget(self.chk_startup_enabled_only)

        # Spacer to align right (optional aesthetic)
        top_bar.addStretch(1)

        layout.addLayout(top_bar)

        # --- Startup Table ---
        self.table_startup = QTableWidget()
        self.table_startup.setColumnCount(6)
        self.table_startup.setHorizontalHeaderLabels(
            ["Name", "Location", "Command", "Startup Status", "Running", "Actions"]
        )
        self.table_startup.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # --- Mini Log ---
        self.startup_log_widget = QPlainTextEdit()
        self.startup_log_widget.setReadOnly(True)
        self.startup_log_widget.setPlaceholderText("Startup log messages will appear here...")

        # --- Splitter (75% table, 25% log) ---
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)
        splitter.addWidget(self.table_startup)
        splitter.addWidget(self.startup_log_widget)

        # Set initial ratio → [3 parts table, 1 part log]
        splitter.setSizes([300, 100])

        layout.addWidget(splitter)

        # Responsive resize
        self.startup_tab.resizeEvent = lambda event: splitter.setSizes([
            int(self.startup_tab.height() * 0.75),
            int(self.startup_tab.height() * 0.25)
        ])

        # Add only once
        self.tabs.addTab(self.startup_tab, "Startup")

    def _build_services_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        btn_refresh = QPushButton("Refresh Services")
        btn_refresh.clicked.connect(self._update_services_tab)
        layout.addWidget(btn_refresh)
        self.table_services = QTableWidget()
        self.table_services.setColumnCount(6)
        self.table_services.setHorizontalHeaderLabels(
            ["Service", "Status", "PID", "CPU%", "RAM%", "Actions"]
        )
        layout.addWidget(self.table_services)
        self.tabs.addTab(tab, "Services")

    def _build_suggestions_tab(self):
        sug = QWidget()
        layout = QVBoxLayout(sug)
        top = QHBoxLayout()
        top.addWidget(QLabel("Improvement Suggestions:"))

        # --- Original Refresh Suggestions button (preserved) ---
        self.btn_refresh_suggestions = QPushButton("Refresh Suggestions")
        self.btn_refresh_suggestions.clicked.connect(self._refresh_suggestions)
        top.addWidget(self.btn_refresh_suggestions)

        # --- NEW AI Suggestions button added next to Refresh Suggestions (no layout change) ---
        self.btn_ai_suggestions = QPushButton("🧠 AI Suggestions")
        self.btn_ai_suggestions.setToolTip("Run AI-powered analysis (security, bottlenecks, predictions, power, maintenance)")
        self.btn_ai_suggestions.clicked.connect(self._on_ai_suggestions_clicked)
        top.addWidget(self.btn_ai_suggestions)

        top.addStretch()
        layout.addLayout(top)

        self.table_suggestions = QTableWidget()
        self.table_suggestions.setColumnCount(5)
        self.table_suggestions.setHorizontalHeaderLabels(["Name", "Type", "Usage", "Suggestion", "Action"])
        layout.addWidget(self.table_suggestions)

        self.suggestions_status = QTextEdit()
        self.suggestions_status.setReadOnly(True)
        self.suggestions_status.setFixedHeight(120)
        layout.addWidget(self.suggestions_status)
        self.tabs.addTab(sug, "Suggestions")

    # -----------------------
    # Data gathering
    # -----------------------
    def _gather_proc_scores(self):
        """
        Returns:
            proc_map: pid -> (name, cpu% , mem%, disk_bytes_delta, net_ops, score)
            results: list of tuples (pid, name, cpu, mem, disk_kb_str, net_ops_str, score_str, exe_path, score_numeric, disk_bytes_delta, net_ops)
        """
        proc_map = {}
        results = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'exe']):
            try:
                pid = proc.info['pid']
                name = proc.info.get('name') or ""
                if is_system_process_name(name):
                    continue
                cpu = proc.info.get('cpu_percent') or 0.0
                mem = proc.info.get('memory_percent') or 0.0

                # --- Disk I/O counters ---
                try:
                    io = proc.io_counters()
                    read_b = (io.read_bytes or 0)
                    write_b = (io.write_bytes or 0)
                except Exception:
                    read_b = 0
                    write_b = 0

                prev = self.prev_disk.get(pid, (0, 0))
                delta_read = max(0, read_b - prev[0])
                delta_write = max(0, write_b - prev[1])
                self.prev_disk[pid] = (read_b, write_b)
                disk_bytes_delta = delta_read + delta_write

                # --- Network ops (approx) ---
                net_ops = 0
                try:
                    other = getattr(io, 'other_count', 0)
                    readcount = getattr(io, 'read_count', 0)
                    prev_net = self.prev_net.get(pid, (0, 0))
                    net_ops = max(0, (other - prev_net[0]) + (readcount - prev_net[1]))
                    self.prev_net[pid] = (other, readcount)
                except Exception:
                    net_ops = 0

                # --- Score ---
                disk_kb = disk_bytes_delta / 1024.0
                disk_score = (disk_kb / 1024.0) * 1.0
                net_score = (net_ops / 50.0) * 0.5
                score_numeric = (cpu * 2.0) + (mem * 1.5) + disk_score + net_score

                # --- Executable path ---
                exe_path = ""
                try:
                    exe_path = proc.info.get("exe") or ""
                    if not exe_path:
                        exe_path = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    exe_path = ""
                except Exception:
                    exe_path = ""

                proc_map[pid] = (name, cpu, mem, disk_bytes_delta, net_ops, score_numeric)
                results.append(
                    (
                        pid, name, cpu, mem, f"{int(disk_kb)} KB/s",
                        f"{int(net_ops)} ops", f"{score_numeric:.2f}", exe_path, score_numeric, disk_bytes_delta, net_ops,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return proc_map, results

    # -----------------------
    # Table population helpers (unchanged)
    # -----------------------
    def get_score_color(self, score):
        try:
            s = float(score)
        except Exception:
            s = 0.0
        if s >= 90:
            return QColor("#5a0000")
        if s >= 75:
            return QColor("#7f3f00")
        if s >= 50:
            return QColor("#556b00")
        if s >= 25:
            return QColor("#004400")
        return QColor("#002200")

    def _populate_table(self, table: QTableWidget, data):
        """
        Safely populate QTableWidget with process or AI suggestion data.
        Handles both long tuples (process info) and shorter rows (AI Suggestions).
        Prevents Qt invalid index warnings and applies color based on score.
        """
        table.blockSignals(True)

        # Clear table if no data
        if not data:
            table.clearContents()
            table.setRowCount(0)
            table.blockSignals(False)
            return

        col_count = table.columnCount()
        table.setRowCount(len(data))

        for row, item in enumerate(data):
            # Defensive: ensure item is a list/tuple
            if not isinstance(item, (list, tuple)):
                item = [str(item)]

            # Get score (used for background color)
            # AI Suggestion rows often have no numeric score, so default to 0
            score_val = 0
            if len(item) > 6:
                score_val = item[6]
            elif len(item) > 3 and str(item[3]).isdigit():
                score_val = int(item[3])
            elif len(item) > 4 and str(item[4]).isdigit():
                score_val = int(item[4])

            color = self.get_score_color(score_val)

            # Write up to available columns
            for col in range(col_count):
                try:
                    val = item[col]
                except Exception:
                    val = ""

                # Process tables: format CPU%/MEM% nicely
                if isinstance(val, float) and col in (2, 3):
                    display = f"{val:.1f}"
                else:
                    display = str(val)

                cell = QTableWidgetItem(display)
                cell.setBackground(color)
                cell.setForeground(QColor("#fff"))
                table.setItem(row, col, cell)

        table.blockSignals(False)


    # -----------------------
    # (Other original GUI methods are preserved below — event handlers, export, refresh etc.)
    # For brevity in this part I keep original methods intact and continue in Part 2.
    # -----------------------

    def _on_refresh_change(self, val):
        try:
            self.refresh_interval_ms = int(val) * 1000
            self.timer.start(self.refresh_interval_ms)
        except Exception:
            pass

    def _export_report(self):
        try:
            # Simple export of current dashboard to a text file
            proc_map, results = self._gather_proc_scores()
            with open(REPORT_FILE, "w", encoding="utf-8") as f:
                f.write(f"Report generated at: {datetime.now().isoformat()}\n\n")
                f.write("Top processes:\n")
                for row in results[:50]:
                    f.write(str(row) + "\n")
            QMessageBox.information(self, "Export Complete", f"Exported to {REPORT_FILE}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _show_proc_context_menu(self, pos):
        row = self.table_dashboard.currentRow()
        if row < 0:
            return
        pid_item = self.table_dashboard.item(row, 0)
        path_item = self.table_dashboard.item(row, 7)
        if not pid_item:
            return
        try:
            pid = int(pid_item.text())
        except Exception:
            pid = None
        exe_path = path_item.text().strip() if path_item else ""

        menu = QMenu(self)
        act_kill = menu.addAction("Kill Process")
        act_open_loc = menu.addAction("Open File Location")
        act_info = menu.addAction("Info Path")
        act_delete = menu.addAction("Delete File")
        act_deep_delete = menu.addAction("Deep Delete (Kill + Delete + Registry)")
        action = menu.exec(self.table_dashboard.viewport().mapToGlobal(pos))

        if action == act_kill:
            if pid is not None:
                self._kill_process(pid)
        elif action == act_open_loc:
            if exe_path:
                if os.path.exists(exe_path):
                    try:
                        self._open_file_location(exe_path)
                    except Exception as e:
                        QMessageBox.warning(self, "Error", f"Open location failed:\n{e}")
                else:
                    QMessageBox.warning(self, "Error", "Executable path not found.")
            else:
                QMessageBox.warning(self, "Error", "Executable path not available.")
        elif action == act_info:
            if exe_path:
                QMessageBox.information(self, "File Path", exe_path)
            else:
                QMessageBox.warning(self, "Error", "Executable path not available.")
        elif action == act_delete:
            if exe_path and os.path.exists(exe_path):
                ok, msg = self._delete_file(exe_path)
                if ok:
                    QMessageBox.information(self, "Deleted", msg)
                else:
                    QMessageBox.warning(self, "Delete Failed", msg)
            else:
                QMessageBox.warning(self, "Error", "File not found for deletion.")
        elif action == act_deep_delete:
            ok, msg = self._deep_delete(pid, exe_path)
            if ok:
                QMessageBox.information(self, "Deep Delete", msg)
            else:
                QMessageBox.warning(self, "Deep Delete Failed", msg)

    def _proc_table_show_info(self, row, column):
        try:
            pid_item = self.table_dashboard.item(row, 0)
            path_item = self.table_dashboard.item(row, 7)
            if not pid_item:
                return
            pid = pid_item.text()
            file_path = path_item.text() if path_item else ""
            if file_path:
                self.label_culprit.setText(f"PID {pid} → {file_path}")
            else:
                self.label_culprit.setText(f"PID {pid} → [No Path Detected]")
        except Exception:
            pass

    def _open_file_location(self, file_path: str):
        if file_path and os.path.exists(file_path):
            try:
                subprocess.Popen(["explorer", "/select,", file_path])
            except Exception:
                try:
                    subprocess.Popen(f'explorer /select,"{file_path}"')
                except Exception:
                    pass

    def _kill_selected_process(self):
        row = self.table_dashboard.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Kill Process", "Select a process row in the Dashboard table first.")
            return
        pid_item = self.table_dashboard.item(row, 0)
        if not pid_item:
            QMessageBox.warning(self, "Kill Process", "Unable to read PID from selected row.")
            return
        try:
            pid = int(pid_item.text())
        except Exception:
            QMessageBox.warning(self, "Kill Process", "Invalid PID.")
            return
        confirm = QMessageBox.question(self, "Confirm Kill", f"Terminate process PID {pid}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=3)
            QMessageBox.information(self, "Process Terminated", f"Process {pid} terminated.")
        except psutil.NoSuchProcess:
            QMessageBox.information(self, "Process Gone", "Process no longer exists.")
        except Exception as e:
            QMessageBox.critical(self, "Kill Failed", str(e))

    def _kill_process(self, pid):
        try:
            proc = psutil.Process(pid)
            proc.kill()
            self.label_culprit.setText(f"Process killed: PID {pid}")
        except Exception as e:
            QMessageBox.warning(self, "Kill Failed", str(e))

    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True, f"Deleted: {path}"
            return False, "File not found"
        except Exception as e:
            return False, str(e)

    def _deep_delete(self, pid, path):
        try:
            if pid:
                try:
                    p = psutil.Process(pid)
                    p.kill()
                except Exception:
                    pass

            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

            # best-effort registry cleanup
            reg_paths = [
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            ]
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for pth in reg_paths:
                    try:
                        with winreg.OpenKey(root, pth, 0, winreg.KEY_ALL_ACCESS) as key:
                            i = 0
                            while True:
                                try:
                                    name, val, _ = winreg.EnumValue(key, i)
                                    if path and path.lower() in str(val).lower():
                                        winreg.DeleteValue(key, name)
                                    else:
                                        i += 1
                                except OSError:
                                    break
                    except Exception:
                        continue

            return True, "Deep delete attempted"
        except Exception as e:
            return False, str(e)

    #---------  StartUp  --------------#
    # --- Registry Utilities ---
    def _delete_startup_value_for_views(self, hive, subkey, name):
        """Delete startup approval value from all registry views (64/32)."""
        errs, views = [], []
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            views.append(winreg.KEY_WOW64_64KEY)
        if hasattr(winreg, "KEY_WOW64_32KEY"):
            views.append(winreg.KEY_WOW64_32KEY)
        if not views:
            views = [0]
        for v in views:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE | v) as key:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass
            except Exception as e:
                errs.append((v, e))
        return errs

    def _write_startup_value_for_views(self, hive, subkey, name, data_bytes):
        """Write startup approval value to all registry views (64/32)."""
        errs, views = [], []
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            views.append(winreg.KEY_WOW64_64KEY)
        if hasattr(winreg, "KEY_WOW64_32KEY"):
            views.append(winreg.KEY_WOW64_32KEY)
        if not views:
            views = [0]
        for v in views:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE | v) as key:
                    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data_bytes)
            except FileNotFoundError:
                continue
            except Exception as e:
                errs.append((v, e))
        return errs

    def _get_approval_path(self, path=None, is_folder=False):
        """
        Resolve the correct StartupApproved registry path.
        Handles both 'Run' and 'StartupFolder' entries.
        """
        try:
            if is_folder or not path:
                return r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
            if "Run" in path:
                return path.replace("Run", r"Explorer\\StartupApproved\\Run")
            return r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\\Run"
        except Exception as e:
            self.startup_log_widget.appendPlainText(f"[Startup][Error] _get_approval_path failed: {e}")
            return r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\\Run"

    def _deduplicate_startup_entries(self, entries):
        """
        Deduplicate startup entries with same target exe.
        Prefer Startup Folder entry if duplicate exists.
        """
        deduped = {}
        for e in entries:
            target = e.get("target", "").lower()
            name = e.get("name", "").strip().lower()
            src = e.get("source", "unknown")

            if not target:
                target = name  # fallback

            # Normalize path for comparison (handle 8.3 paths)
            norm_target = target.replace("progra~1", "program files").replace("progra~2", "program files (x86)")
            key = os.path.basename(norm_target)

            # If duplicate, prefer Startup Folder over Registry
            if key not in deduped:
                deduped[key] = e
            else:
                if "startup" in src.lower() and "run" in deduped[key]["source"].lower():
                    deduped[key] = e  # overwrite registry entry

        return list(deduped.values())

    # --- Core Approval ---
    def _set_startup_approval(self, hive, path, name, action: str, is_folder=False):
        """
        Update StartupApproved registry state for Run or Startup Folder entries.
        action: enable | disable | delete
        """
        try:
            approved_path = self._get_approval_path(path, is_folder)

            if action == "enable":
                data = b"\x02" + b"\x00" * 7
            elif action == "disable":
                data = b"\x03" + b"\x00" * 7
            else:
                data = None

            if action in ("enable", "disable"):
                errs = self._write_startup_value_for_views(hive, approved_path, name, data)
                for e in errs:
                    self.startup_log_widget.appendPlainText(f"[Startup][Error] {e}")
                if not errs:
                    self.startup_log_widget.appendPlainText(f"[Startup] {action.title()}d {name} in {approved_path}")
            elif action == "delete":
                self._delete_startup_value_for_views(hive, approved_path, name)

        except Exception as e:
            self.startup_log_widget.appendPlainText(
                f"[Startup][Error] Approval {action} failed {name}: {e}"
            )

    # --- Disable Entry ---
    def _disable_startup_entry_ui(self, hive, path, name):
        """Disable startup entry (Registry or Folder)."""
        try:
            if path:
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                        val, t = winreg.QueryValueEx(key, name)
                    disabled_path = path.replace("Run", "RunDisabled")
                    with winreg.CreateKey(hive, disabled_path) as key2:
                        winreg.SetValueEx(key2, name, 0, t, val)
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key3:
                        winreg.DeleteValue(key3, name)
                except FileNotFoundError:
                    pass
                self._set_startup_approval(hive, path, name, "disable", False)
            else:
                for base in [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]:
                    p = os.path.join(base, f"{name}.lnk")
                    if os.path.exists(p):
                        dp = p + ".disabled"
                        os.rename(p, dp)
                        self.startup_log_widget.appendPlainText(f"[Startup] Disabled {p} → {dp}")
                self._set_startup_approval(winreg.HKEY_CURRENT_USER, None, name, "disable", True)
            self._update_startup_tab()
        except Exception as e:
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Disable failed {name}: {e}")

    # --- Enable Entry ---
    def _enable_startup_entry_ui(self, hive, path, name, cmd):
        """Enable startup entry (Registry or Folder)."""
        try:
            if path:
                disabled_path = path.replace("Run", "RunDisabled")
                restored = False
                try:
                    with winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_READ) as key:
                        val, t = winreg.QueryValueEx(key, name)
                    with winreg.CreateKey(hive, path) as key2:
                        winreg.SetValueEx(key2, name, 0, t, val)
                    with winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_SET_VALUE) as key3:
                        winreg.DeleteValue(key3, name)
                    restored = True
                except FileNotFoundError:
                    if cmd:
                        with winreg.CreateKey(hive, path) as key:
                            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
                        restored = True
                if restored:
                    self._set_startup_approval(hive, path, name, "enable", False)
            else:
                restored = False
                for base in [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]:
                    dp = os.path.join(base, f"{name}.lnk.disabled")
                    op = dp.replace(".disabled", "")
                    if os.path.exists(dp):
                        try:
                            if os.path.exists(op):
                                self.startup_log_widget.appendPlainText(f"[Startup][Skip] {op} exists")
                            else:
                                os.rename(dp, op)
                                restored = True
                                self.startup_log_widget.appendPlainText(f"[Startup] Restored {op}")
                        except PermissionError:
                            shutil.copy2(dp, op)
                            os.remove(dp)
                            restored = True
                            self.startup_log_widget.appendPlainText(f"[Startup][Fallback] Copied {op}")
                if restored:
                    self._set_startup_approval(winreg.HKEY_CURRENT_USER, None, name, "enable", True)
            self._update_startup_tab()
        except Exception as e:
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Enable failed {name}: {e}")

    # --- Delete Entry ---
    def _delete_startup_entry_ui(self, hive, path, name):
        """Delete startup entry everywhere."""
        self._set_startup_approval(hive, path, name, "delete", not path)
        try:
            if path:
                for target in [path, path.replace("Run", "RunDisabled")]:
                    try:
                        with winreg.OpenKey(hive, target, 0, winreg.KEY_SET_VALUE) as key:
                            winreg.DeleteValue(key, name)
                            self.startup_log_widget.appendPlainText(f"[Startup] Deleted {name} from {target}")
                    except FileNotFoundError:
                        pass
            else:
                for base in [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]:
                    for ext in [".lnk", ".lnk.disabled"]:
                        p = os.path.join(base, f"{name}{ext}")
                        if os.path.exists(p):
                            os.remove(p)
                            self.startup_log_widget.appendPlainText(f"[Startup] Deleted {p}")
        except Exception as e:
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Delete failed {name}: {e}")
        self._update_startup_tab()

    # --- Quick Repair ---
    def _repair_startup_sync(self):
        """Auto-fix mismatched registry/files (.lnk.disabled vs StartupApproved)."""
        self.startup_log_widget.appendPlainText("[Startup] Running QuickSync Repair...")
        try:
            self._update_startup_tab()
            # Could iterate entries and re-sync approval byte if mismatch found
            self.startup_log_widget.appendPlainText("[Startup] QuickSync completed.")
        except Exception as e:
            self.startup_log_widget.appendPlainText(f"[Startup][Error] QuickSync failed: {e}")

    # --- Kill Process ---
    def _kill_startup_process_ui(self, name, cmd):
        try:
            killed = []
            target = None
            if cmd:
                parts = cmd.strip('"').split('"')
                if parts:
                    target = parts[0] if os.path.isfile(parts[0]) else None
            for p in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                try:
                    if target and p.info['exe'] and p.info['exe'].lower() == target.lower():
                        p.terminate(); killed.append(p.info['pid'])
                    elif p.info['name'] and name.lower() in p.info['name'].lower():
                        p.terminate(); killed.append(p.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            msg = f"Killed {len(killed)} process(es)" if killed else "No process found"
            QMessageBox.information(self, "Kill Result", msg)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # --- Update Startup Tab ---
    def _update_startup_tab(self):
        show_only_enabled = getattr(self, "chk_startup_enabled_only", None)
        show_only_enabled = show_only_enabled and show_only_enabled.isChecked()

        # Collect registry startup entries
        entries = self.sugg.list_registry_startup()

        # Dedup registry entries
        seen = set(); uniq = []
        for e in entries:
            k = (e[2].lower(), str(e[3] or "").lower())
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        entries = uniq

        # Collect Startup Folder entries
        for sdir in [
            os.path.join(os.environ["APPDATA"], r"Microsoft\\Windows\\Start Menu\\Programs\\Startup"),
            os.path.join(os.environ["ProgramData"], r"Microsoft\\Windows\\Start Menu\\Programs\\Startup"),
        ]:
            if not os.path.exists(sdir):
                continue
            for f in os.listdir(sdir):
                if not f.lower().endswith((".lnk", ".lnk.disabled")):
                    continue
                name = f.replace(".lnk.disabled", "").replace(".lnk", "")
                entries.append((None, None, name, os.path.join(sdir, f)))

        # Smart dedup
        merged = {}
        for e in entries:
            hive, path, name, cmd = e
            cmd_str = str(cmd or "").lower()
            norm_cmd = cmd_str.replace("progra~1", "program files").replace("progra~2", "program files (x86)")
            exe_name = None
            try:
                if norm_cmd.endswith(".lnk") and os.path.exists(cmd):
                    exe_name = os.path.splitext(os.path.basename(norm_cmd))[0]
                else:
                    parts = norm_cmd.replace('"', '').split()
                    if parts:
                        exe_name = os.path.basename(parts[0])
            except Exception:
                exe_name = os.path.basename(norm_cmd) if norm_cmd else name.lower()
            dedup_key = f"{exe_name}:{name.lower().strip()}"
            if dedup_key not in merged:
                merged[dedup_key] = e
            else:
                old = merged[dedup_key]
                old_cmd = str(old[3] or "").lower()
                if (".lnk" in norm_cmd and ".lnk" not in old_cmd) or ("startup" in norm_cmd and "run" in old_cmd):
                    merged[dedup_key] = e

        entries = list(merged.values())

        # Table setup
        self.table_startup.setRowCount(0)
        self.table_startup.setColumnCount(6)
        self.table_startup.setHorizontalHeaderLabels(
            ["Name", "Location", "Command", "Startup Status", "Running", "Manage"]
        )

        row_data_list = []

        # Collect all rows
        for (hive, path, name, cmd) in entries:
            loc = "Startup Folder" if not path else f"{'HKCU' if hive == winreg.HKEY_CURRENT_USER else 'HKLM'}: {path}"

            # --- Startup Status ---
            status = "Enabled"
            try:
                if path:
                    ap = self._get_approval_path(path)
                    with winreg.OpenKey(hive, ap, 0, winreg.KEY_READ) as k:
                        v, _ = winreg.QueryValueEx(k, name)
                        if v[0] == 0x03:
                            status = "Disabled"
                else:
                    if cmd and ".disabled" in str(cmd).lower():
                        status = "Disabled"
                    else:
                        ap = self._get_approval_path(None, True)
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ap, 0, winreg.KEY_READ) as k:
                            v, _ = winreg.QueryValueEx(k, name)
                            if v[0] == 0x03:
                                status = "Disabled"
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"[Startup][Warn] Failed reading status for {name}: {e}")

            if show_only_enabled and status != "Enabled":
                continue  # skip disabled entries if filter on

            # --- Running Status ---
            run = "Not Running"
            exe_candidates = set()

            if cmd:
                cmd_str = str(cmd).replace('"', '').strip().lower()
                parts = cmd_str.split()
                if parts:
                    # Take the base exe name and its variants
                    base = os.path.basename(parts[0])
                    name_only = os.path.splitext(base)[0]
                    exe_candidates.update({base, name_only, f"{name_only}.exe"})

            try:
                for p in psutil.process_iter(['name', 'exe', 'cmdline']):
                    pname = (p.info.get('name') or '').lower()
                    pexe = os.path.basename((p.info.get('exe') or '')).lower()
                    pcmd = ' '.join(p.info.get('cmdline') or []).lower()

                    # Match if any candidate in process name, exe path, or cmdline
                    if any(
                        cand in pname or cand in pexe or cand in pcmd
                        for cand in exe_candidates
                    ):
                        run = "Running"
                        break
            except Exception:
                pass

            # --- Manage Menu ---
            btn = QToolButton(); btn.setText("Manage")
            menu = QMenu(btn)

            if status == "Enabled":
                toggle_label = "Disable"
                toggle_func = partial(self._disable_startup_entry_ui, hive, path, name)
            else:
                toggle_label = "Enable"
                toggle_func = partial(self._enable_startup_entry_ui, hive, path, name, cmd)

            act_toggle = QAction(toggle_label, self)
            act_toggle.triggered.connect(toggle_func)
            menu.addAction(act_toggle)

            for label, func in {
                "Delete": partial(self._delete_startup_entry_ui, hive, path, name),
                "Kill": partial(self._kill_startup_process_ui, name, cmd),
                "Repair": self._repair_startup_sync,
            }.items():
                act = QAction(label, self)
                act.triggered.connect(func)
                menu.addAction(act)

            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

            row_data_list.append((name, loc, str(cmd), status, run, btn))

        # ✅ Sort by Running, then Name
        row_data_list.sort(key=lambda r: (r[4] != "Running", r[0].lower()))

        # Populate sorted table with color highlighting
        for r, row in enumerate(row_data_list):
            self.table_startup.insertRow(r)
            for c, value in enumerate(row):
                if isinstance(value, QToolButton):
                    self.table_startup.setCellWidget(r, c, value)
                else:
                    item = QTableWidgetItem(value)
                    # 🟢 Green for Running, 🔴 Red for Not Running
                    if c == 4:  # Running column
                        if value == "Running":
                            item.setForeground(QColor("green"))
                        else:
                            item.setForeground(QColor("red"))
                    self.table_startup.setItem(r, c, item)

        self.startup_log_widget.appendPlainText(
            f"[Startup] Table sorted by Running. Filter={'Enabled only' if show_only_enabled else 'All'}."
        )

    #---------  Others  --------------#

    def _update_services_tab(self):
        try:
            services = []
            for svc in psutil.win_service_iter():
                try:
                    info = svc.as_dict()
                    name = info.get("display_name") or info.get("name") or ""
                    status = info.get("status", "")
                    pid = info.get("pid") or "-"
                    cpu = 0.0
                    mem = 0.0
                    if isinstance(pid, int) and pid > 0:
                        try:
                            p = psutil.Process(pid)
                            cpu = p.cpu_percent(interval=0.0)
                            mem = p.memory_percent()
                        except Exception:
                            pass
                    services.append((name, status, pid, cpu, mem))
                except Exception:
                    continue

            # ✅ Sort by memory usage (highest first)
            services.sort(key=lambda x: x[4], reverse=True)

            self.table_services.setRowCount(len(services))
            for r, (name, status, pid, cpu, mem) in enumerate(services):
                bg = QColor("#222") if r % 2 == 0 else QColor("#111")
                items = [
                    QTableWidgetItem(str(name)),
                    QTableWidgetItem(str(status)),
                    QTableWidgetItem(str(pid)),
                    QTableWidgetItem(f"{cpu:.1f}"),
                    QTableWidgetItem(f"{mem:.1f}"),
                ]
                for c, it in enumerate(items):
                    it.setBackground(bg)
                    it.setForeground(QColor("#0f0"))
                    self.table_services.setItem(r, c, it)

                # Context menu with Stop / Disable / Kill
                menu = QMenu()
                stop_action = QAction("Stop", self)
                disable_action = QAction("Disable", self)
                kill_action = QAction("Kill", self)
                svc_name_local = name
                stop_action.triggered.connect(lambda _, n=svc_name_local: subprocess.run(["sc", "stop", n], capture_output=True))
                disable_action.triggered.connect(lambda _, n=svc_name_local: subprocess.run(["sc", "config", n, "start=", "disabled"], capture_output=True))
                kill_action.triggered.connect(lambda _, p=pid: (psutil.Process(p).kill() if isinstance(p, int) and p > 0 else None))
                menu.addAction(stop_action)
                menu.addAction(disable_action)
                menu.addAction(kill_action)

                btn = QToolButton()
                btn.setText("Manage")
                btn.setMenu(menu)
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                self.table_services.setCellWidget(r, 5, btn)

        except Exception:
            pass

    def _refresh_dashboard(self):
        try:
            # --- System Info (top bar) ---
            try:
                cpu = psutil.cpu_percent(interval=0.2)
                mem = psutil.virtual_memory()
                disk = shutil.disk_usage("C:\\") if os.name == 'nt' else shutil.disk_usage(os.path.expanduser("~"))
                io = psutil.disk_io_counters()

                if io:
                    if self.prev_disk_io is None:
                        io_rate = 0.0
                    else:
                        delta_read = io.read_bytes - self.prev_disk_io.read_bytes
                        delta_write = io.write_bytes - self.prev_disk_io.write_bytes
                        interval_s = self.refresh_interval_ms / 1000.0
                        io_rate = (delta_read + delta_write) / 1024.0 / interval_s  # KB/s
                    self.prev_disk_io = io
                else:
                    io_rate = 0.0

                disk_usage_pct = (disk.used / disk.total) * 100.0

                def color_for(val_pct):
                    if val_pct < 25:
                        return "green"
                    elif val_pct < 50:
                        return "yellow"
                    elif val_pct < 80:
                        return "orange"
                    else:
                        return "red"

                cpu_color = color_for(cpu)
                ram_color = color_for(mem.percent)

                max_io_kb = 100 * 1024  # assume 100 MB/s ceiling
                io_pct = min((io_rate / max_io_kb) * 100.0, 100.0)
                io_color = color_for(io_pct)

                disk_color = color_for(disk_usage_pct)

                html = (
                    f"OS: {platform.system()} {platform.release()} | "
                    f"CPU <span style='color:{cpu_color};'>{cpu:.1f}%</span> | "
                    f"RAM <span style='color:{ram_color};'>{mem.percent:.1f}%</span> | "
                    f"Disk I/O <span style='color:{io_color};'>{io_rate:.1f} KB/s</span> | "
                    f"Disk (C:) {bytes_to_gb(disk.used)}GB/{bytes_to_gb(disk.total)}GB "
                    f"(<span style='color:{disk_color};'>{disk_usage_pct:.1f}%</span>)"
                )
                self.sys_info_label.setText(html)
            except Exception:
                self.sys_info_label.setText("System info: unavailable")

            # --- Process Table (dashboard) ---
            proc_map, results = self._gather_proc_scores()
            results_sorted = sorted(results, key=lambda x: x[8], reverse=True)
            self.proc_map = proc_map
            top_dashboard = results_sorted[:15]
            self._populate_table(self.table_dashboard, top_dashboard)

            # --- Top Culprit label ---
            if results_sorted:
                top = results_sorted[0]
                try:
                    self.label_culprit.setText(
                        f"Top Culprit: {top[1]} (PID {top[0]}) Score {top[8]:.1f}"
                    )
                except Exception:
                    self.label_culprit.setText(f"Top Culprit: {top[1]} (PID {top[0]})")
            else:
                self.label_culprit.setText("Top Culprit: (none)")

            # --- Top Performance Tab ---
            try:
                self._populate_top_performance(results_sorted)
            except Exception:
                pass

        except Exception as e:
            QMessageBox.warning(self, "Dashboard Refresh Failed", str(e))

    def _populate_top_performance(self, results_sorted):
        if not hasattr(self, 'top_perf_table'):
            return

        # Collect rows: top 5 of CPU, RAM, Disk, Network
        rows = []
        top_cpu = sorted(results_sorted, key=lambda x: x[2], reverse=True)[:5]
        top_mem = sorted(results_sorted, key=lambda x: x[3], reverse=True)[:5]
        top_io = sorted(results_sorted, key=lambda x: x[9], reverse=True)[:5]
        top_net = sorted(results_sorted, key=lambda x: x[10], reverse=True)[:5]

        for p in top_cpu:
            rows.append(("CPU", p))
        for p in top_mem:
            rows.append(("RAM", p))
        for p in top_io:
            rows.append(("Disk I/O", p))
        for p in top_net:
            rows.append(("Network", p))

        # Reset table
        self.top_perf_table.setRowCount(0)
        partition_points = {5, 10, 15}  # after these many data rows insert separator

        data_index = 0
        insert_row_idx = 0
        for metric, p in rows:
            # Insert normal data row
            self.top_perf_table.insertRow(insert_row_idx)
            pid = p[0]
            name = p[1]
            cpu = p[2]
            mem = p[3]
            disk_kb = p[4]
            net_ops = p[5]
            score_str = p[6]
            color = self.get_score_color(score_str)

            values = [metric, pid, name, f"{cpu:.1f}", f"{mem:.1f}", disk_kb, net_ops, score_str]
            for col, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                item.setBackground(color)
                item.setForeground(QColor("#fff"))
                self.top_perf_table.setItem(insert_row_idx, col, item)

            insert_row_idx += 1
            data_index += 1

            # Insert black separator row if data_index in partition_points
            if data_index in partition_points:
                self.top_perf_table.insertRow(insert_row_idx)
                sep_item = QTableWidgetItem("")
                sep_item.setBackground(QColor("#000000"))
                self.top_perf_table.setItem(insert_row_idx, 0, sep_item)
                self.top_perf_table.setSpan(insert_row_idx, 0, 1, self.top_perf_table.columnCount())
                self.top_perf_table.setRowHeight(insert_row_idx, 6)
                insert_row_idx += 1

        # --- Force no scroll bars at all ---
        self.top_perf_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.top_perf_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Adjust height dynamically so all rows are visible
        row_count = self.top_perf_table.rowCount()
        base_row_height = 28
        header_height = self.top_perf_table.horizontalHeader().height()

        # Compute full table height (always enough for all rows)
        table_height = (row_count * base_row_height) + header_height + 2
        self.top_perf_table.setFixedHeight(table_height)

        # Optionally also fix width to prevent horizontal scrollbar
        total_col_width = sum(self.top_perf_table.columnWidth(c) for c in range(self.top_perf_table.columnCount()))
        self.top_perf_table.setFixedWidth(total_col_width + self.top_perf_table.verticalHeader().width() + 2)

    # -----------------------
    # Suggestions tab actions
    # -----------------------

    def _refresh_suggestions(self):
        proc_map = self.proc_map or {}
        suggestions = self.sugg.scan_for_suggestions(proc_map)
        self.table_suggestions.setRowCount(0)
        for r, s in enumerate(suggestions):
            name, typ, usage, suggestion_text, action_callable = s
            self.table_suggestions.insertRow(r)
            self.table_suggestions.setItem(r, 0, QTableWidgetItem(str(name)))
            self.table_suggestions.setItem(r, 1, QTableWidgetItem(str(typ)))
            self.table_suggestions.setItem(r, 2, QTableWidgetItem(str(usage)))
            self.table_suggestions.setItem(r, 3, QTableWidgetItem(str(suggestion_text)))
            btn = QPushButton("Execute")
            if not callable(action_callable):
                btn.setText("No Action")
                btn.setEnabled(False)
            else:
                btn.clicked.connect(partial(self._execute_suggestion_action, action_callable, name))
            self.table_suggestions.setCellWidget(r, 4, btn)
        self.suggestions_status.append(f"[{datetime.now().strftime('%H:%M:%S')}] Suggestions refreshed ({len(suggestions)} items).")

    def _execute_suggestion_action(self, action_callable, display_name):
        try:
            res = action_callable()
            if isinstance(res, tuple) and len(res) == 2:
                ok, msg = res
                if ok:
                    self.suggestions_status.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {display_name}: {msg}")
                    QMessageBox.information(self, "Action Result", msg)
                else:
                    self.suggestions_status.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {display_name}: {msg}")
                    QMessageBox.warning(self, "Action Failed", msg)
            else:
                self.suggestions_status.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {display_name}: action executed.")
                QMessageBox.information(self, "Action", f"Action executed for {display_name}.")
        except Exception as e:
            self.suggestions_status.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {display_name}: Exception {e}")
            QMessageBox.critical(self, "Action Exception", str(e))

    # ====================================================
    # 🧠 AI Suggestions Handler (Level 1 + Level 2 Engine)
    # ====================================================

    def _populate_suggestions_table(self, suggestions):
        """Populate table with AI suggestion entries."""
        self.table_suggestions.setRowCount(len(suggestions))
        for row, s in enumerate(suggestions):
            self.table_suggestions.setItem(row, 0, QTableWidgetItem(s.get("name", "")))          # Name
            self.table_suggestions.setItem(row, 1, QTableWidgetItem(s.get("type", "")))          # Type
            self.table_suggestions.setItem(row, 2, QTableWidgetItem(s.get("description", "")))   # Usage / details
            self.table_suggestions.setItem(row, 3, QTableWidgetItem(s.get("suggestion", "")))    # Suggestion / action text
            self.table_suggestions.setItem(
                row, 4, QTableWidgetItem(f"{s.get('score', 0)} {s.get('priority', '')}")
            )  # Score + priority
        self.table_suggestions.resizeColumnsToContents()

    def _on_ai_suggestions_clicked(self):
        """
        Trigger AI suggestion engine, analyze system & processes,
        and populate GUI table with structured results.
        """
        try:
            self.suggestions_status.append("[AI] Starting Level 1 + Level 2 analysis...")

            # Thread-safe start
            def run_ai_thread():
                try:
                    self.suggestions_status.append("[AI] Thread started safely...")
                    self.suggestions_status.append("[AI] Collecting process list...")

                    # Collect current process list
                    processes = []
                    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "io_counters", "exe"]):
                        try:
                            io = proc.info.get("io_counters")
                            processes.append({
                                "pid": proc.info.get("pid"),
                                "name": proc.info.get("name"),
                                "cpu_percent": proc.info.get("cpu_percent", 0.0),
                                "memory_percent": proc.info.get("memory_percent", 0.0),
                                "io_read_bytes": io.read_bytes if io else 0,
                                "io_write_bytes": io.write_bytes if io else 0,
                                "exe": proc.info.get("exe", "")
                            })
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

                    self.suggestions_status.append(f"[AI] Found {len(processes)} processes to analyze.")
                    self.suggestions_status.append("[AI] Running Level 1 system analysis...")

                    # 🔁 Reset engine context for a fresh scan
                    self.ai_engine.reset()
                    self.ai_engine.analyze_system()

                    # 🔍 Analyze each process
                    for i, proc in enumerate(processes, 1):
                        self.ai_engine.analyze_process(proc)
                        if i % 25 == 0:
                            self.suggestions_status.append(f"[AI] Analyzed {i}/{len(processes)} processes...")

                    self.suggestions_status.append(f"[AI] Process analysis completed ({len(processes)} entries).")

                    # Retrieve AI output
                    ai_suggestions = self.ai_engine.get_suggestions()
                    self.suggestions_status.append(f"[AI] Retrieved {len(ai_suggestions)} raw AI suggestions.")

                    # 🧠 Debug sample
                    if ai_suggestions:
                        print("[AI] Example suggestion:", ai_suggestions[0])
                    else:
                        print("[AI] No suggestions returned.")

                    # ✅ Build safe, normalized suggestion list
                    results = []
                    for s in ai_suggestions:
                        try:
                            # Case 1: dict-based suggestion
                            if isinstance(s, dict):
                                text = s.get("text", str(s))
                                sev = s.get("severity", "info")
                                conf = s.get("confidence", 0.5)

                            # Case 2: tuple/list (name, sev, conf)
                            elif isinstance(s, (list, tuple)) and len(s) >= 2:
                                text = str(s[0])
                                sev = str(s[1]) if len(s) > 1 else "info"
                                conf = float(s[2]) if len(s) > 2 else 0.5

                            # Case 3: plain string
                            elif isinstance(s, str):
                                text = s
                                sev = "info"
                                conf = 0.5

                            # Unknown object
                            else:
                                text = str(s)
                                sev = "info"
                                conf = 0.5

                            # Priority map
                            priority = {
                                "warning": "🔴 Critical",
                                "notice": "🟡 Moderate",
                                "info": "🟢 Info",
                            }.get(sev.lower(), "🟢 Info")

                            results.append({
                                "name": text.split(" ")[0] if text else "Unknown",
                                "type": sev.capitalize(),
                                "description": text,
                                "score": int(conf * 100),
                                "suggestion": text,
                                "priority": priority,
                            })

                        except Exception as e:
                            self.suggestions_status.append(f"[AI] Suggestion parse failed: {e}")
                            continue

                    # 🛟 Fallback: empty result
                    if not results:
                        self.suggestions_status.append("[AI] No suggestions parsed, fallback triggered.")
                        results = [{
                            "name": "system",
                            "type": "Info",
                            "description": "AI returned empty list",
                            "score": 50,
                            "suggestion": "Check AI engine output",
                            "priority": "🟢 Info",
                        }]

                    # 🧱 Update GUI table
                    self._populate_suggestions_table(results)
                    self.suggestions_status.append(f"[AI] Table populated with {len(results)} entries.")

                    # ✅ Push summary to GUI log
                    self.ai_engine.push_to_gui(self)

                except Exception as e:
                    self.suggestions_status.append(f"[AI][Error] {e}")
                    import traceback; traceback.print_exc()

            # 🔄 Launch AI in thread
            import threading
            t = threading.Thread(target=run_ai_thread, daemon=True)
            t.start()

        except Exception as e:
            self.suggestions_status.append(f"[AI][Fatal] {e}")
            import traceback; traceback.print_exc()

# -----------------------
# Application entrypoint
# -----------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = PerformanceMonitorGUI()
    w.show()
    sys.exit(app.exec())
