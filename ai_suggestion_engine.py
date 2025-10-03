# ai_suggestion_engine.py
import psutil
import os
import time
import hashlib
import json
import threading
from collections import defaultdict, deque


class AISuggestionsEngine:
    """
    Advanced AI-like suggestions engine integrated into your existing GUI.
    Runs in background thread and produces structured suggestions that populate
    the existing suggestions table (self.table_suggestions).
    """

    def __init__(self, history_file=None):
        self.history_file = history_file or os.path.join(os.path.expanduser("~"), ".processload_ai_history.json")
        self.history = self._load_history()
        self.trends = defaultdict(lambda: deque(maxlen=12))  # pid -> deque of (ts, rss)
        self.reputation_cache = {}  # map hash -> score (placeholder)
        self._lock = threading.RLock()

    def _load_history(self):
        if not os.path.exists(self.history_file):
            return {"ignored": [], "killed": [], "seen": {}, "trends": {}}
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"ignored": [], "killed": [], "seen": {}, "trends": {}}

    def _save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except Exception:
            pass

    def run_all_checks(self):
        """
        Runs all AI suggestion checks with debug prints and timing.
        Returns a list of suggestions.
        """
        import time as t
        with self._lock:
            results = []

            # Utility to time each step
            def timed_step(name, func):
                start = t.time()
                try:
                    res = func()
                    duration = t.time() - start
                    print(f"[AI] {name} completed: {len(res)} items (took {duration:.2f}s)")
                    return res
                except Exception as e:
                    duration = t.time() - start
                    print(f"[AI] {name} FAILED after {duration:.2f}s: {e}")
                    return []

            print("[AI] Starting full AI Suggestions scan...")

            # Security-aware scan
            results.extend(timed_step("Security-Aware Scan", self.security_aware_scan))

            # Resource bottleneck analysis
            results.extend(timed_step("Resource Bottleneck Analysis", self.resource_bottleneck_analysis))

            # Predictive suggestions
            results.extend(timed_step("Predictive Suggestions", self.predictive_suggestions))

            # Power management suggestions
            results.extend(timed_step("Power Management Suggestions", self.context_aware_power_management))

            # Proactive maintenance
            results.extend(timed_step("Proactive Maintenance Suggestions", self.proactive_maintenance_suggestions))

            # Smart Auto-Actions summary
            results.extend(timed_step("Smart Auto-Actions Summary", lambda: self.smart_auto_actions_summary(results)))

            # Add user-friendly scoring
            results = timed_step("User-Friendly Scoring", lambda: self.add_user_friendly_scoring(results))

            # Flush trends and save history
            start_flush = t.time()
            self._flush_trends_to_history()
            self._save_history()
            print(f"[AI] Trends & history saved (took {t.time() - start_flush:.2f}s)")

            print(f"[AI] Full AI scan completed: {len(results)} total suggestions")
            return results

    # ----------------------
    # Helper makers
    # ----------------------
    def _make_suggestion(self, name, ptype, pid, score, description, suggestion, action, details=None):
        return {
            "id": f"{ptype}-{pid}-{int(time.time())}",
            "name": name,
            "type": ptype,
            "pid": pid,
            "score": score,
            "description": description,
            "suggestion": suggestion,
            "action": action,
            "details": details or {},
            "priority": ""
        }

    # ----------------------
    # Security-aware scan
    # ----------------------
    def security_aware_scan(self):
        out = []
        suspicious_keys = ["temp", "\\temp\\", "/tmp/", "appdata"]
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                info = proc.info
                exe = info.get('exe') or ""
                if not exe:
                    # try method
                    try:
                        exe = proc.exe()
                    except Exception:
                        exe = ""
                exe_lower = exe.lower()
                if any(k in exe_lower for k in suspicious_keys):
                    out.append(self._make_suggestion(
                        name=info.get('name') or os.path.basename(exe),
                        ptype="Security",
                        pid=info.get('pid'),
                        score=95,
                        description=f"Unknown exe in suspicious path: {exe}",
                        suggestion="Quarantine file",
                        action="Quarantine",
                        details={"path": exe}
                    ))
                # file hash reputation stub (non-blocking)
                if exe and exe_lower.endswith(".exe"):
                    h = self._compute_file_hash_safe(exe)
                    if h and h in self.reputation_cache:
                        rep_score = self.reputation_cache.get(h)
                        if rep_score and rep_score > 85:
                            out.append(self._make_suggestion(
                                name=info.get('name') or os.path.basename(exe),
                                ptype="Security",
                                pid=info.get('pid'),
                                score=rep_score,
                                description=f"File reputation unfavorable: {exe}",
                                suggestion="Quarantine or upload to VirusTotal",
                                action="Quarantine",
                                details={"hash": h}
                            ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
        return out

    def _compute_file_hash_safe(self, path, algo='sha256'):
        try:
            if not os.path.exists(path) or not os.path.isfile(path):
                return None
            h = hashlib.new(algo)
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            digest = h.hexdigest()
            # cache
            self.reputation_cache[digest] = self.reputation_cache.get(digest, 0)
            return digest
        except Exception:
            return None

    # ----------------------
    # Resource bottleneck analysis
    # ----------------------
    def resource_bottleneck_analysis(self):
        out = []
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            du = psutil.disk_usage(os.path.abspath(os.sep)).percent
            if cpu > 80:
                out.append(self._make_suggestion("System", "Performance", None, 80,
                                                 f"CPU sustained >80% ({cpu}%)",
                                                 "Close CPU-heavy apps", "Investigate"))
            if mem > 90:
                out.append(self._make_suggestion("System", "Performance", None, 85,
                                                 f"RAM >90% ({mem}%)",
                                                 "Disable background services", "Investigate"))
            if du > 90:
                out.append(self._make_suggestion("System", "Performance", None, 75,
                                                 f"Disk usage >90% ({du}%)",
                                                 "Clean temp files / disable indexing", "Cleanup"))

            # top memory processes
            procs = []
            # initialize cpu_percent sample then sleep small interval to get meaningful per-process cpu
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
            time.sleep(0.12)
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    cpu_p = p.cpu_percent(None)
                    mi = None
                    try:
                        mi = p.memory_info()
                        rss = mi.rss if mi else 0
                    except Exception:
                        rss = 0
                    procs.append((p.pid, p.info.get('name'), cpu_p, rss))
                except Exception:
                    continue
            procs_sorted = sorted(procs, key=lambda t: t[3], reverse=True)[:8]
            for pid, name, cpu_p, rss in procs_sorted:
                mb = rss / (1024 * 1024)
                score = min(90, int((mb / 200) * 100))
                out.append(self._make_suggestion(name or f"PID {pid}", "Process", pid, score,
                                                 f"{name} using {mb:.1f} MB RAM",
                                                 "Consider restarting or reducing workload", "SuggestRestart",
                                                 details={"rss_mb": mb, "cpu_percent": cpu_p}))
        except Exception:
            pass
        return out

    # ----------------------
    # Predictive suggestions
    # ----------------------
    def predictive_suggestions(self):
        out = []
        now = time.time()
        for p in psutil.process_iter(['pid', 'name']):
            try:
                pid = p.pid
                name = p.info.get('name') or f"PID {pid}"
                try:
                    rss = p.memory_info().rss
                except Exception:
                    rss = 0
                dq = self.trends[pid]
                dq.append((now, rss))
                # simple linear growth detection across oldest->newest
                if len(dq) >= 4:
                    t0, r0 = dq[0]
                    t1, r1 = dq[-1]
                    if t1 > t0 and r1 > r0:
                        rate = (r1 - r0) / (t1 - t0)  # bytes/sec
                        # threshold: > ~1 MB / min
                        if rate > (1 * 1024 * 1024) / 60:
                            mb_per_min = rate * 60 / (1024 * 1024)
                            out.append(self._make_suggestion(name, "Predictive", pid, 80,
                                                             f"{name} shows memory growth {mb_per_min:.1f} MB/min",
                                                             "Restart to prevent crash", "SuggestRestart",
                                                             details={"growth_mb_per_min": mb_per_min}))
                # crashed often? read history 'seen' entries
                key = f"{name}:{pid}"
                cnt = self.history.get("seen", {}).get(key, 0)
                if cnt >= 3:
                    out.append(self._make_suggestion(name, "Predictive", pid, 85,
                                                     f"{name} has crashed {cnt} times recently",
                                                     "Consider reinstalling/updating", "SuggestReinstall",
                                                     details={"crash_count": cnt}))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
        return out

    # ----------------------
    # Power management suggestions
    # ----------------------
    def context_aware_power_management(self):
        out = []
        try:
            if hasattr(psutil, "sensors_battery"):
                batt = psutil.sensors_battery()
                if batt:
                    percent = getattr(batt, "percent", None)
                    plugged = getattr(batt, "power_plugged", None)
                    if percent is not None and percent < 20 and not plugged:
                        out.append(self._make_suggestion("Battery", "Power", None, 90,
                                                         f"Battery low: {percent}%",
                                                         "Switch to Battery Saver / close heavy apps",
                                                         "AdjustPower"))
        except Exception:
            pass
        return out

    # ----------------------
    # Proactive maintenance suggestions
    # ----------------------
    def proactive_maintenance_suggestions(self):
        out = []
        try:
            du = psutil.disk_usage(os.path.abspath(os.sep))
            free_percent = 100 - du.percent
            if free_percent < 10:
                out.append(self._make_suggestion("Drive", "Maintenance", None, 80,
                                                 f"Free space low on root: {free_percent:.1f}%",
                                                 "Run Disk Cleanup / delete temp files", "Cleanup"))
        except Exception:
            pass
        return out

    # ----------------------
    # Smart Auto-Actions / Summaries
    # ----------------------
    def smart_auto_actions_summary(self, existing_suggestions):
        # Return grouped actions (for UI to display). This demo returns an empty list.
        return []

    # ----------------------
    # Scoring & priority icons
    # ----------------------
    def add_user_friendly_scoring(self, suggestions):
        for s in suggestions:
            sc = s.get("score", 0) or 0
            if sc >= 80:
                pref = "🔴"
            elif sc >= 50:
                pref = "🟡"
            else:
                pref = "🟢"
            s["priority"] = pref
            desc = s.get("description") or ""
            # apply history overrides (whitelist/blacklist)
            if desc in self.history.get("ignored", []):
                s["priority"] = "🟢"
            if desc in self.history.get("killed", []):
                s["priority"] = "🔴"
        return suggestions

    def _flush_trends_to_history(self):
        for pid, dq in self.trends.items():
            pid_key = str(pid)
            recs = self.history.setdefault("trends", {}).setdefault(pid_key, [])
            for ts, rss in dq:
                recs.append({"ts": ts, "rss": rss})
            # trim
            if len(recs) > 5000:
                recs[:] = recs[-5000:]
