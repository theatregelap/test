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
from datetime import datetime
from functools import partial
from collections import deque, defaultdict

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTabWidget, QGroupBox, QSpinBox, QMenu, QMessageBox, QTextEdit,
    QHeaderView, QToolButton , QPlainTextEdit , QSplitter
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

# -----------------------
# Utility helpers (unchanged)
# -----------------------
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
from ai_suggestion_engine import AISuggestionsEngine
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
        self.sugg_ai = AISuggestionsEngine()

        # ---------- AI Engine placeholder (will be instantiated on demand) ----------
        self.ai_engine = None
        self._ai_thread = None
        self._ai_lock = threading.RLock()

        self._build_ui()

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

        # --- Refresh Button ---
        btn_refresh = QPushButton("Refresh Startup Entries")
        btn_refresh.clicked.connect(self._update_startup_tab)
        layout.addWidget(btn_refresh)

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
    def _delete_startup_approved_for_views(hive, subkey, name):
        errs = []
        views = []
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            views.append(winreg.KEY_WOW64_64KEY)
        if hasattr(winreg, "KEY_WOW64_32KEY"):
            views.append(winreg.KEY_WOW64_32KEY)
        if not views:
            views = [0]

        for v in views:
            access = winreg.KEY_SET_VALUE | v
            try:
                with winreg.OpenKey(hive, subkey, 0, access) as key:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass
            except Exception as e:
                errs.append((v, e))
        return errs

    def _write_startup_approved_for_views(self, hive, subkey, name, data_bytes):
        """
        Write name=data_bytes to hive subkey in both 64-bit and 32-bit registry views.
        Returns list of exceptions (empty list = ok).
        """
        errors = []
        views = []
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            views.append(winreg.KEY_WOW64_64KEY)
        if hasattr(winreg, "KEY_WOW64_32KEY"):
            views.append(winreg.KEY_WOW64_32KEY)
        if not views:
            views = [0]

        for view in views:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE | view) as key:
                    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data_bytes)
            except FileNotFoundError:
                continue
            except Exception as e:
                errors.append(e)
        return errors

    def _set_startup_approval(self, hive, path, name, action: str):
        """
        Update StartupApproved registry state for Run or Startup Folder entries.
        action: "enable", "disable", or "delete"
        """
        try:
            if path:  # Registry Run entry
                approved_path = path.replace("Run", r"Explorer\StartupApproved\Run")
                if action == "enable":
                    data = b"\x02" + b"\x00" * 7
                elif action == "disable":
                    data = b"\x03" + b"\x00" * 7
                elif action == "delete":
                    data = None

                if action in ("enable", "disable"):
                    errs = self._write_startup_approved_for_views(hive, approved_path, name, data)
                    if errs:
                        for e in errs:
                            self.startup_log_widget.appendPlainText(f"[Startup][Error] {e}")
                    else:
                        self.startup_log_widget.appendPlainText(f"[Startup] {action.title()}d {name} in {approved_path}")
                elif action == "delete":
                    self._delete_startup_approved_for_views(hive, approved_path, name)

            else:  # Startup Folder entry
                approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
                if action == "enable":
                    data = b"\x02" + b"\x00" * 7
                elif action == "disable":
                    data = b"\x03" + b"\x00" * 7
                elif action == "delete":
                    data = None

                if action in ("enable", "disable"):
                    errs = self._write_startup_approved_for_views(winreg.HKEY_CURRENT_USER, approved_path, name, data)
                    if errs:
                        for e in errs:
                            self.startup_log_widget.appendPlainText(f"[Startup][Error] {e}")
                    else:
                        self.startup_log_widget.appendPlainText(f"[Startup] {action.title()}d {name} in Startup Folder approval")
                elif action == "delete":
                    self._delete_startup_approved_for_views(winreg.HKEY_CURRENT_USER, approved_path, name)

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to {action} startup entry {name}:\n{e}")
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Failed to {action} {name}: {e}")

    def _disable_startup_entry_ui(self, hive, path, name):
        """Disable a startup entry (Registry Run or Startup Folder)."""
        try:
            if path:  # Registry Run entry
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                        value, valtype = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    value, valtype = None, None

                if value is not None:
                    disabled_path = path.replace("Run", "RunDisabled")
                    with winreg.CreateKey(hive, disabled_path) as key:
                        winreg.SetValueEx(key, name, 0, valtype, value)
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)

                self._set_startup_approval(hive, path, name, "disable")

            else:  # Startup Folder entry
                startup_paths = [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]
                for spath in startup_paths:
                    shortcut = os.path.join(spath, f"{name}.lnk")
                    if os.path.exists(shortcut):
                        disabled_shortcut = shortcut + ".disabled"
                        os.rename(shortcut, disabled_shortcut)
                        self.startup_log_widget.appendPlainText(f"[Startup] Renamed {shortcut} -> {disabled_shortcut}")

                self._set_startup_approval(hive, path, name, "disable")

            self._update_startup_tab()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to disable startup entry {name}:\n{e}")
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Failed to disable {name}: {e}")

    def _enable_startup_entry_ui(self, hive, path, name, cmd):
        """Enable a previously disabled startup entry."""
        try:
            if path:  # Registry Run entry
                disabled_path = path.replace("Run", "RunDisabled")
                try:
                    with winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_READ) as key:
                        value, valtype = winreg.QueryValueEx(key, name)
                    with winreg.CreateKey(hive, path) as key:
                        winreg.SetValueEx(key, name, 0, valtype, value)
                    with winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    if cmd:
                        with winreg.CreateKey(hive, path) as key:
                            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)

                self._set_startup_approval(hive, path, name, "enable")

            else:  # Startup Folder entry
                startup_paths = [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]

                restored = False
                for spath in startup_paths:
                    disabled_shortcut = os.path.join(spath, f"{name}.lnk.disabled")
                    original_shortcut = disabled_shortcut.replace(".disabled", "")

                    if os.path.exists(disabled_shortcut):
                        # If target .lnk already exists, skip to avoid overwrite
                        if os.path.exists(original_shortcut):
                            self.startup_log_widget.appendPlainText(
                                f"[Startup][Skip] {original_shortcut} already exists, keeping both."
                            )
                            continue
                        try:
                            os.rename(disabled_shortcut, original_shortcut)
                            self.startup_log_widget.appendPlainText(
                                f"[Startup] Restored {disabled_shortcut} -> {original_shortcut}"
                            )
                            restored = True
                        except PermissionError:
                            try:
                                shutil.copy2(disabled_shortcut, original_shortcut)
                                os.remove(disabled_shortcut)
                                self.startup_log_widget.appendPlainText(
                                    f"[Startup] Copied {disabled_shortcut} -> {original_shortcut} (fallback)"
                                )
                                restored = True
                            except Exception as e:
                                self.startup_log_widget.appendPlainText(
                                    f"[Startup][Error] Fallback copy failed: {e}"
                                )
                        except Exception as e:
                            self.startup_log_widget.appendPlainText(
                                f"[Startup][Error] Rename failed {disabled_shortcut}: {e}"
                            )

                if not restored:
                    self.startup_log_widget.appendPlainText(
                        f"[Startup][Warn] No disabled shortcut found for {name}"
                    )

                self._set_startup_approval(hive, path, name, "enable")

            self._update_startup_tab()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to enable startup entry {name}:\n{e}")
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Failed to enable {name}: {e}")

    def _delete_startup_entry_ui(self, hive, path, name):
        """Permanently delete a startup entry (registry or folder, including disabled links)."""
        self._set_startup_approval(hive, path, name, "delete")

        try:
            if path:  # Registry Run entry
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                        try:
                            winreg.DeleteValue(key, name)
                            self.startup_log_widget.appendPlainText(f"[Startup] Deleted {name} from {path}")
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    pass

                # Also delete from RunDisabled if exists
                disabled_path = path.replace("Run", "RunDisabled")
                try:
                    with winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_SET_VALUE) as key:
                        try:
                            winreg.DeleteValue(key, name)
                            self.startup_log_widget.appendPlainText(f"[Startup] Deleted {name} from {disabled_path}")
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    pass

            else:  # Startup Folder entry
                startup_paths = [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                ]
                for spath in startup_paths:
                    for ext in [".lnk", ".lnk.disabled"]:
                        shortcut = os.path.join(spath, f"{name}{ext}")
                        if os.path.exists(shortcut):
                            try:
                                os.remove(shortcut)
                                self.startup_log_widget.appendPlainText(f"[Startup] Deleted shortcut {shortcut}")
                            except Exception as e:
                                QMessageBox.warning(self, "Error", f"Failed to delete shortcut {shortcut}:\n{e}")
                                self.startup_log_widget.appendPlainText(f"[Startup][Error] Failed to delete shortcut {shortcut}: {e}")

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to delete startup entry {name}:\n{e}")
            self.startup_log_widget.appendPlainText(f"[Startup][Error] Failed to delete {name}: {e}")

        self._update_startup_tab()

    def _kill_startup_process_ui(self, name, cmd):
        try:
            killed = []
            target_exe = None

            # Try to extract exe from command line
            if cmd:
                parts = cmd.strip('"').split('"')
                if parts:
                    target_exe = parts[0] if os.path.isfile(parts[0]) else None

            for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                try:
                    if target_exe and proc.info['exe'] and proc.info['exe'].lower() == target_exe.lower():
                        proc.terminate()
                        killed.append(proc.info['pid'])
                    elif proc.info['name'] and name.lower() in proc.info['name'].lower():
                        proc.terminate()
                        killed.append(proc.info['pid'])
                    elif proc.info['cmdline'] and cmd and any(cmd_part in " ".join(proc.info['cmdline']) for cmd_part in [name, cmd]):
                        proc.terminate()
                        killed.append(proc.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if killed:
                QMessageBox.information(self, "Process Killed", f"Killed process(es) for {name}: {killed}")
            else:
                QMessageBox.information(self, "Not Found", f"No running process found for {name}")

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to kill process {name}:\n{e}")

    def set_startup_status(self, hive, path, name, enabled=True, cmd=None):
        """
        Enable or disable a startup entry by writing into StartupApproved.
        """
        try:
            approved_path = path.replace("Run", r"Explorer\StartupApproved\Run")
            with winreg.OpenKey(hive, approved_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    data = bytes([0x02]) + b"\x00" * 11   # Enabled
                else:
                    data = bytes([0x03]) + b"\x00" * 11   # Disabled
                winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)

            # Re-add if missing and enabling
            if enabled and cmd:
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
                except OSError:
                    pass

            return True, f"Startup entry '{name}' set to {'Enabled' if enabled else 'Disabled'}"
        except Exception as e:
            return False, f"Failed to update startup entry {name}: {e}"

    def delete_startup_entry(self, hive, path, name):
        """
        Permanently delete a startup entry from both Run and StartupApproved (registry/folder).
        """
        try:
            if path:  # Registry Run entry
                # Delete from Run key
                try:
                    with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass

                # Delete from StartupApproved\Run
                try:
                    approved_path = path.replace("Run", r"Explorer\StartupApproved\Run")
                    with winreg.OpenKey(hive, approved_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass

            else:  # Startup Folder entry
                startup_paths = [
                    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup")
                ]

                # Remove shortcut file
                for spath in startup_paths:
                    shortcut = os.path.join(spath, f"{name}.lnk")
                    if os.path.exists(shortcut):
                        try:
                            os.remove(shortcut)
                        except Exception:
                            pass

                # Remove from StartupApproved\StartupFolder
                approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE) as key:
                        try:
                            winreg.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    pass

        except Exception:
            return False, f"Failed to delete startup entry '{name}'"
        return True, f"Startup entry '{name}' deleted"

    def _open_startup_command_path_ui(self, cmd):
        import shlex
        try:
            parts = shlex.split(cmd)
            path = parts[0]
        except Exception:
            path = cmd

        if path and os.path.exists(path):
            try:
                subprocess.Popen(["explorer", "/select,", path])
            except Exception:
                try:
                    subprocess.Popen(f'explorer /select,"{path}"')
                except Exception as e:
                    QMessageBox.warning(self, "Open Failed", str(e))
        else:
            QMessageBox.information(self, "Open Path", f"Path not found: {path}")

    def _update_startup_tab(self):
        import psutil, os, winreg, shutil

        # --- Collect Registry Entries ---
        entries = self.sugg.list_registry_startup()  # returns tuples: (hive, path, name, cmd)

        # Deduplicate registry items
        seen = set()
        unique_entries = []
        for e in entries:
            hive, path, name, cmd = e
            key = (str(name).lower(), str(cmd or "").lower())
            if key not in seen:
                seen.add(key)
                unique_entries.append(e)
        entries = unique_entries

        # --- Collect Startup Folder Entries ---
        startup_folders = [
            os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
            os.path.join(os.environ["ProgramData"], r"Microsoft\Windows\Start Menu\Programs\Startup"),
        ]

        folder_entries = []
        for sdir in startup_folders:
            if not os.path.exists(sdir):
                continue
            for entry in os.listdir(sdir):
                if not entry.lower().endswith(".lnk") and not entry.lower().endswith(".lnk.disabled"):
                    continue
                fullpath = os.path.join(sdir, entry)
                name = os.path.splitext(entry)[0].replace(".lnk", "").replace(".disabled", "")
                folder_entries.append((None, None, name, fullpath))

        # Merge (Registry + Folder)
        entries.extend(folder_entries)

        # Deduplicate across all
        seen_all = set()
        final_entries = []
        for e in entries:
            hive, path, name, cmd = e
            key = (str(name).lower(), str(cmd or "").lower())
            if key not in seen_all:
                seen_all.add(key)
                final_entries.append(e)

        entries = final_entries

        # --- Setup Table ---
        self.table_startup.setRowCount(0)
        self.table_startup.setColumnCount(6)
        self.table_startup.setHorizontalHeaderLabels(
            ["Name", "Location", "Command", "Startup Status", "Running", "Manage"]
        )

        for r, (hive, path, name, cmd) in enumerate(entries):
            self.table_startup.insertRow(r)

            # Name
            self.table_startup.setItem(r, 0, QTableWidgetItem(str(name)))

            # Location
            if path:
                hive_str = {
                    winreg.HKEY_CURRENT_USER: "HKCU",
                    winreg.HKEY_LOCAL_MACHINE: "HKLM",
                }.get(hive, str(hive))
                regloc = f"{hive_str}: {path}"
            else:
                regloc = "Startup Folder"
            self.table_startup.setItem(r, 1, QTableWidgetItem(regloc))

            # Command
            self.table_startup.setItem(r, 2, QTableWidgetItem(str(cmd)))

            # --- Enabled/Disabled Detection ---
            status = "Enabled"
            try:
                if path:
                    # Registry entry: check StartupApproved\Run
                    approved_path = path.replace("Run", r"Explorer\\StartupApproved\\Run")
                    with winreg.OpenKey(hive, approved_path, 0, winreg.KEY_READ) as key:
                        val, _ = winreg.QueryValueEx(key, name)
                        if isinstance(val, (bytes, bytearray)) and len(val) > 0:
                            if val[0] == 0x03:
                                status = "Disabled"
                else:
                    # Folder entry: check file suffix or StartupApproved\StartupFolder
                    if cmd and str(cmd).lower().endswith(".lnk.disabled"):
                        status = "Disabled"
                    else:
                        approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
                        try:
                            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_READ) as key:
                                val, _ = winreg.QueryValueEx(key, name)
                                if isinstance(val, (bytes, bytearray)) and len(val) > 0:
                                    status = "Disabled" if val[0] == 0x03 else "Enabled"
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                status = "Enabled"
            except OSError:
                status = "Unknown"

            self.table_startup.setItem(r, 3, QTableWidgetItem(status))

            # --- Running Detection ---
            running = "Not Running"
            try:
                exe_name = None
                if cmd:
                    # Extract basename safely
                    parts = str(cmd).replace('"', '').split()
                    if parts:
                        exe_name = os.path.basename(parts[0]).lower()

                if exe_name:
                    for proc in psutil.process_iter(['name', 'exe', 'cmdline']):
                        pname = (proc.info['name'] or "").lower()
                        pexe = (proc.info['exe'] or "").lower()
                        pcmd = " ".join(proc.info['cmdline'] or []).lower()
                        if exe_name in pname or exe_name in pexe or exe_name in pcmd:
                            running = "Running"
                            break
            except Exception:
                pass

            self.table_startup.setItem(r, 4, QTableWidgetItem(running))

            # --- Manage button ---
            btn = QToolButton()
            btn.setText("Manage")
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

            menu = QMenu(btn)
            act_disable = QAction("Disable", self)
            act_enable = QAction("Enable", self)
            act_delete = QAction("Delete Entry", self)
            act_open = QAction("Open Command Path", self)
            act_kill = QAction("Kill Process", self)

            act_disable.triggered.connect(partial(self._disable_startup_entry_ui, hive, path, name))
            act_enable.triggered.connect(partial(self._enable_startup_entry_ui, hive, path, name, cmd))
            act_delete.triggered.connect(partial(self._delete_startup_entry_ui, hive, path, name))
            act_open.triggered.connect(partial(self._open_startup_command_path_ui, cmd))
            act_kill.triggered.connect(partial(self._kill_startup_process_ui, name, cmd))

            menu.addAction(act_disable)
            menu.addAction(act_enable)
            menu.addAction(act_delete)
            menu.addAction(act_open)
            menu.addAction(act_kill)

            btn.setMenu(menu)
            self.table_startup.setCellWidget(r, 5, btn)

        # --- Log Update ---
        # self.startup_log_widget.appendPlainText(f"Refreshed startup list: {len(entries)} entries")

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

    # ---------------------------
    # AI Suggestions button click handler
    # ---------------------------
    def _populate_suggestions_table(self, suggestions):
        self.table_suggestions.setRowCount(len(suggestions))
        for row, s in enumerate(suggestions):
            self.table_suggestions.setItem(row, 0, QTableWidgetItem(s.get("name", "")))          # Name
            self.table_suggestions.setItem(row, 1, QTableWidgetItem(s.get("type", "")))          # Type
            self.table_suggestions.setItem(row, 2, QTableWidgetItem(s.get("description", "")))   # Usage / details
            self.table_suggestions.setItem(row, 3, QTableWidgetItem(s.get("suggestion", "")))    # Suggestion / action text
            self.table_suggestions.setItem(row, 4, QTableWidgetItem(f"{s.get('score', 0)} {s.get('priority', '')}"))  # Score + priority
        self.table_suggestions.resizeColumnsToContents()

    def _on_ai_suggestions_clicked(self):
        """
        Runs AI suggestion checks and populates the suggestions table.
        Compatible with _refresh_suggestions / Execute buttons.
        """
        
        def worker():
            from ai_suggestion_engine import AISuggestionsEngine
            results = []

            try:
                results = self.sugg_ai.run_all_checks()  # use the AI engine
                self._populate_suggestions_table(results)
            except Exception as e:
                print(f"[AI] Engine failed: {e}")
                results = []

            # fallback sample
            if not results:
                results = [
                    {
                        "name": "chrome.exe",
                        "type": "Process",
                        "description": "CPU 75%",
                        "score": 40,
                        "suggestion": "Restart browser to free memory",
                        "priority": "🟡 Moderate",
                    },
                    {
                        "name": "temp.exe",
                        "type": "Unsigned EXE",
                        "description": "Running from Temp folder",
                        "score": 90,
                        "suggestion": "Quarantine file immediately",
                        "priority": "🔴 Critical",
                    },
                ]

            # --- Convert AI dict results into tuples for _refresh_suggestions ---
            suggestions = []
            for r in results:
                name = r.get("name", "")
                typ = r.get("type", "")
                usage = r.get("description", "")
                suggestion_text = r.get("suggestion", "")
                # Action callable can be a stub for now
                action_callable = lambda n=name: print(f"Execute action for {n}")
                suggestions.append((name, typ, usage, suggestion_text, action_callable))

            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, update_ui)

        def update_ui():
            try:
                self.table_suggestions.setRowCount(0)
                for r_idx, s in enumerate(suggestions):
                    name, typ, usage, suggestion_text, action_callable = s
                    self.table_suggestions.insertRow(r_idx)
                    self.table_suggestions.setItem(r_idx, 0, QTableWidgetItem(str(name)))
                    self.table_suggestions.setItem(r_idx, 1, QTableWidgetItem(str(typ)))
                    self.table_suggestions.setItem(r_idx, 2, QTableWidgetItem(str(usage)))
                    self.table_suggestions.setItem(r_idx, 3, QTableWidgetItem(str(suggestion_text)))
                    btn = QPushButton("Execute")
                    if not callable(action_callable):
                        btn.setText("No Action")
                        btn.setEnabled(False)
                    else:
                        btn.clicked.connect(partial(self._execute_suggestion_action, action_callable, name))
                    self.table_suggestions.setCellWidget(r_idx, 4, btn)

                total_suggestions = self.table_suggestions.rowCount()

                # ✅ Show AI completion message in same log widget
                self.suggestions_status.append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] [AI] Full AI scan completed: {total_suggestions} total suggestions."
                )

                # ✅ Keep your existing completion line
                self.suggestions_status.append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ✅ AI Suggestions completed ({len(suggestions)} results)"
                )

                self.btn_ai_suggestions.setEnabled(True)

            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "AI Suggestions", f"UI update failed: {e}")
                self.btn_ai_suggestions.setEnabled(True)


        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()

# Bind methods into PerformanceMonitorGUI class
#setattr(PerformanceMonitorGUI, "_on_ai_suggestions_clicked", _on_ai_suggestions_clicked)
#setattr(PerformanceMonitorGUI, "_ai_results_to_rows", _ai_results_to_rows)
# -----------------------
# Application entrypoint
# -----------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = PerformanceMonitorGUI()
    w.show()
    sys.exit(app.exec())
