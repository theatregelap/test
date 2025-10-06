#!/usr/bin/env python3
"""
ebay_ai_seller_ultimate.py Ultimate AI eBay Seller Assistant (PyQt5)

Features:
- Tab A: Manage local supplier items (CSV import/export, add/remove)
- Tab B: Market Research (eBay Finding API or Browse API if OAuth credentials exist) per marketplace & keyword
- Tab C: AI Smart Match & AI Rank, TOP 5 summary + bar chart
- Tab D: Sell on eBay (OAuth2) -> create inventory item, create offer, publish offer
- Price optimizer (rule-based + competitor-aware)
- Shipping estimator (simple per-100g rates, editable)
- Trending scraper stub (import CSV from Shopee/Lazada) + cross-check
- Report export (CSV + chart PNG)
- Smart alerts via Telegram (optional)
- Uses sandbox by default (change EBAY_ENV to 'production' for live)

Requirements: pip install PyQt5 requests pandas matplotlib

IMPORTANT: Replace EBAY_APP_ID/CLIENT_SECRET/REDIRECT_URI in settings.json
"""

import os
import sys
import math
import json
import time
import webbrowser
import traceback
import base64
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass

import requests
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QFileDialog, QMessageBox, QComboBox, QTextEdit,
    QSpinBox, QDoubleSpinBox, QInputDialog
)
from PyQt5.QtCore import Qt

# Matplotlib for chart
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

with open("settings.json", "r") as f:
    SETTINGS = json.load(f)

# eBay credentials / environment (only production App ID is needed for research)
EBAY_APP_ID = SETTINGS.get("EBAY_APP_ID", "")
EBAY_ENV = SETTINGS.get("EBAY_ENV", "production")

# New client credentials for OAuth Browse API (placeholders in settings.json)
EBAY_CLIENT_ID = SETTINGS.get("EBAY_CLIENT_ID", "YOUR_CLIENT_ID")
EBAY_CLIENT_SECRET = SETTINGS.get("EBAY_CLIENT_SECRET", "YOUR_CLIENT_SECRET")

# Telegram for smart alerts (optional)
TELEGRAM_BOT_TOKEN = SETTINGS.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = SETTINGS.get("TELEGRAM_CHAT_ID", "")

# Default FX placeholder (MYR -> USD/GBP/AUD).
# Can be updated live.
DEFAULT_FX = SETTINGS.get("DEFAULT_FX", {"USD": 0.22, "GBP": 0.18, "AUD": 0.29})
# Shipping default rates per 100g to target markets (in USD)
DEFAULT_SHIPPING = SETTINGS.get("DEFAULT_SHIPPING", {"US": 1.5, "UK": 2.0, "AU": 1.8})

# Scoring weights for AI Rank
WEIGHT_MARGIN = SETTINGS.get("WEIGHT_MARGIN", 0.55)
WEIGHT_COMPETITION = SETTINGS.get("WEIGHT_COMPETITION", 0.25)
WEIGHT_PRICE = SETTINGS.get("WEIGHT_PRICE", 0.20)
COMPETITION_MAX = SETTINGS.get("COMPETITION_MAX", 50.0)

# API endpoints (support both production & sandbox)
FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
if EBAY_ENV.lower() == "production":
    OAUTH_AUTHORIZE = "https://auth.ebay.com/oauth2/authorize"
    OAUTH_TOKEN = "https://api.ebay.com/identity/v1/oauth2/token"
    SELL_INVENTORY_BASE = "https://api.ebay.com/sell/inventory/v1"
    BROWSE_SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search"
else:
    OAUTH_AUTHORIZE = "https://auth.sandbox.ebay.com/oauth2/authorize"
    OAUTH_TOKEN = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    SELL_INVENTORY_BASE = "https://api.sandbox.ebay.com/sell/inventory/v1"
    BROWSE_SEARCH = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"

# Persistent token storage
TOKEN_FILE = "ebay_token.json"

# Data classes
@dataclass
class LocalItem:
    sku: str
    name: str
    supplier_price_myr: float
    weight_g: int = 100
    moq: int = 1
    notes: str = ""

# Helper utilities
def safe_float(s, default=0.0):
    try:
        return float(s)
    except Exception:
        return default

def parse_price_string(price_str: str) -> Optional[float]:
    """Extract numeric portion of e.g. 'US $23.50' or '23.50 USD' or '£12.99'"""
    if not price_str or not isinstance(price_str, str):
        return None
    buf = []
    for ch in price_str:
        if ch.isdigit() or ch == ".":
            buf.append(ch)
        elif ch in ",":
            # skip commas
            continue
    if not buf:
        return None
    try:
        return float("".join(buf))
    except:
        return None

def fetch_exchange_rate(target: str) -> Optional[float]:
    """Fetch 1 MYR -> target currency using exchangerate.host (no key required).
    Return rate or None."""
    try:
        resp = requests.get("https://api.exchangerate.host/convert", params={"from":"MYR","to":target,"amount":1}, timeout=8)
        resp.raise_for_status()
        j = resp.json()
        rate = j.get("info", {}).get("rate")
        if rate:
            return float(rate)
        if "result" in j:
            return float(j["result"])
    except Exception:
        return None

def send_telegram_alert(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        return resp.status_code == 200
    except Exception:
        return False

# ---------------- Token persistence and OAuth helpers ----------------

def load_token_from_file() -> Optional[Dict[str, Any]]:
    """Load token dict from disk if present and not expired."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
        if time.time() < data.get("expires_at", 0):
            return data
    except Exception:
        return None
    return None

def save_token_to_file(access_token: str, expires_in: int) -> None:
    """Save token info to TOKEN_FILE, refreshing 60s early."""
    data = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in) - 60,
    }
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def get_ebay_token() -> Optional[str]:
    """Return a valid OAuth token.

    Priority:
      1) If token file exists and valid -> return it
      2) If EBAY_CLIENT_ID/SECRET provided -> request a token and cache
      3) Return None if no credentials (caller should fallback to Finding API)
    """
    # 1) try token file
    t = load_token_from_file()
    if t and t.get("access_token"):
        return t["access_token"]

    # 2) request using client credentials
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET or "YOUR_CLIENT" in EBAY_CLIENT_ID:
        # no client credentials configured
        return None

    url = OAUTH_TOKEN
    creds = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
    auth_header = base64.b64encode(creds.encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth_header}",
    }
    data = {
        "grant_type": "client_credentials",
        # scope for Browse API search
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    try:
        resp = requests.post(url, headers=headers, data=data, timeout=15)
        resp.raise_for_status()
        jd = resp.json()
        access_token = jd.get("access_token")
        expires_in = int(jd.get("expires_in", 7200))
        if access_token:
            save_token_to_file(access_token, expires_in)
            return access_token
    except Exception:
        traceback.print_exc()
        return None

    return None

# ---------------- eBay search: Browse API with Finding fallback ----------------

def ebay_find_items(keyword: str, entries_per_page: int = 10, global_id: str = "EBAY-US") -> List[Tuple[str,str,str]]:
    """
    Return list of (title, price_str, viewUrl).

    Behavior:
    - Primary: use Browse API (modern) if OAuth client credentials configured.
    - Fallback: use Finding API (legacy) if Browse/OAuth not available.

    This preserves the original signature so GUI code remains unchanged.
    """
    results: List[Tuple[str,str,str]] = []

    # Try Browse API first (requires OAuth token)
    token = get_ebay_token()
    if token:
        try:
            # Map global_id to marketplace header accepted by Browse API
            # e.g., "EBAY-US" -> "EBAY_US" or use common marketplace IDs expected
            marketplace_header = global_id.replace("-", "_")
            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace_header
            }
            params = {
                "q": keyword,
                "limit": entries_per_page
            }
            resp = requests.get(BROWSE_SEARCH, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("itemSummaries", [])
            for it in items:
                title = it.get("title", "")
                price_val = None
                currency = ""
                price_obj = it.get("price") or {}
                if isinstance(price_obj, dict):
                    price_val = price_obj.get("value")
                    currency = price_obj.get("currency") or price_obj.get("currencyCode", "")
                if price_val is None:
                    price_val = it.get("price", {}).get("value") if it.get("price") else None
                price_str = f"{currency} {price_val}" if price_val is not None else ""
                view_url = it.get("itemWebUrl") or it.get("itemHref") or ""
                results.append((title, price_str, view_url))
            return results
        except Exception:
            traceback.print_exc()
            # fall through to Finding API fallback

    # Fallback: original Finding API (legacy) using EBAY_APP_ID
    if not EBAY_APP_ID:
        return results

    headers_f = {
        "X-EBAY-SOA-SECURITY-APPNAME": EBAY_APP_ID,
        "X-EBAY-SOA-OPERATION-NAME": "findItemsByKeywords",
        "X-EBAY-SOA-SERVICE-VERSION": "1.0.0",
        "X-EBAY-SOA-RESPONSE-DATA-FORMAT": "JSON",
    }
    params_f = {
        "keywords": keyword,
        "paginationInput.entriesPerPage": str(entries_per_page),
        "GLOBAL-ID": global_id.replace("-", "_"),
        "outputSelector": "SellerInfo",
        "REST-PAYLOAD": ""
    }
    try:
        resp = requests.get(FINDING_URL, headers=headers_f, params=params_f, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        try:
            items = data["findItemsByKeywordsResponse"][0]["searchResult"][0].get("item", [])
        except Exception:
            items = []
        for it in items:
            title = it.get("title", [""])[0] if isinstance(it.get("title"), list) else it.get("title", "")
            price_info = {}
            if isinstance(it.get("sellingStatus"), list) and it.get("sellingStatus"):
                price_info = it.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0]
            elif isinstance(it.get("sellingStatus"), dict):
                price_info = it.get("sellingStatus", {}).get("currentPrice", {})
            price_val = price_info.get("__value__", "0") if isinstance(price_info, dict) else "0"
            currency = price_info.get("@currencyId", "") if isinstance(price_info, dict) else ""
            view_url = it.get("viewItemURL", [""])[0] if isinstance(it.get("viewItemURL"), list) else it.get("viewItemURL", "")
            price_str = f"{currency} {price_val}"
            results.append((title, price_str, view_url))
    except Exception:
        traceback.print_exc()

    return results

# Price optimizer: rule-based using competitor prices + margin target
def price_optimizer(local_cost_myr: float, competitor_prices_foreign: List[float], fx_rate: float, shipping_cost_foreign: float, desired_margin_pct: float = 30.0) -> Dict[str, Any]:
    """ Simple optimizer:
    - compute competitor median price (foreign currency)
    - convert competitor median -> MYR (competitor_med * (1/fx if fx is MYR->FOREIGN?))
    In this app we use fx = 1 MYR -> foreign ; to convert foreign -> MYR: foreign_price / fx
    - suggest list price to meet desired margin after shipping.
    - also suggest price in foreign currency (rounded).
    """
    out = {"competitor_median_foreign": None, "suggested_foreign": None, "suggested_myr": None, "expected_margin_pct": None}
    if not competitor_prices_foreign:
        return out
    med = sorted(competitor_prices_foreign)[len(competitor_prices_foreign)//2]
    out["competitor_median_foreign"] = med
    # convert competitor median to MYR
    try:
        competitor_myr = med / fx_rate if fx_rate != 0 else med * (1.0 / DEFAULT_FX.get("USD", 0.22))
    except Exception:
        competitor_myr = med * (1.0 / DEFAULT_FX.get("USD", 0.22))
    # compute shipping MYR
    shipping_myr = shipping_cost_foreign / fx_rate if fx_rate != 0 else 0.0
    desired_margin = desired_margin_pct / 100.0
    try:
        candidate_list_myr = (local_cost_myr + shipping_myr) / (1 - desired_margin) if (1 - desired_margin) > 0 else local_cost_myr * 1.5
    except Exception:
        candidate_list_myr = local_cost_myr * 1.5
    candidate_list_myr = min(candidate_list_myr, competitor_myr * 1.05)
    suggested_foreign = candidate_list_myr * fx_rate
    out["suggested_foreign"] = round(suggested_foreign, 2)
    out["suggested_myr"] = round(candidate_list_myr, 2)
    try:
        expected_margin = ((candidate_list_myr - local_cost_myr - shipping_myr) / candidate_list_myr) * 100.0
    except Exception:
        expected_margin = 0.0
    out["expected_margin_pct"] = round(expected_margin, 1)
    return out

# Trending scraper stub: import CSV from Shopee/Lazada exports or do scraping (NOT implemented)
def import_trending_csv(path: str) -> List[Dict[str, Any]]:
    """ Load a CSV exported from local marketplace or your supplier list.
    Expected columns: name, price_myr, weight_g (optional)
    Returns list of dicts.
    """
    rows = []
    try:
        df = pd.read_csv(path)
        for _, r in df.iterrows():
            rows.append({
                "name": str(r.get("name") or r.get("title") or ""),
                "price_myr": float(r.get("price_myr") or r.get("price") or 0.0),
                "weight_g": int(r.get("weight_g") or 100),
                "notes": r.get("notes", "")
            })
    except Exception:
        pass
    return rows

# Report export (CSV + PNG chart)
def export_report_csv(path_csv: str, matches: List[Dict[str, Any]], chart_png_path: Optional[str] = None):
    rows = []
    for m in matches:
        rows.append({
            "local_item": m.get("local_name"),
            "local_price_myr": m.get("local_rm"),
            "target_title": m.get("target_title"),
            "target_price_str": m.get("target_price_str"),
            "target_currency": m.get("target_currency"),
            "target_rm": m.get("target_rm"),
            "margin_pct": m.get("margin_pct"),
            "ai_score": m.get("score"),
            "ai_stars": m.get("stars"),
            "url": m.get("url")
        })
    df = pd.DataFrame(rows)
    df.to_csv(path_csv, index=False)
    return True

# ---------------- AI Helper ----------------
# ai_helper_ollama.py
import subprocess
class AIHelper:
    def __init__(self, settings=None):
        self.model = (settings.get("OLLAMA_MODEL") if settings else None) or "llama3:instruct"
        self.extra_args = settings.get("OLLAMA_ARGS", []) if settings else []
    def _run_ollama(self, prompt: str) -> str:
        """ Run Ollama model locally and return response text. """
        try:
            result = subprocess.run(
                ["ollama", "run", self.model, prompt] + self.extra_args,
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return f"❌ Ollama error: {result.stderr.strip()}"
            return result.stdout.strip()
        except Exception as e:
            return f"❌ Exception running Ollama: {e}"

    # -------------------- AI Features --------------------
    def expand_keywords(self, product_name: str):
        """ Suggest related eBay search keywords for a given product. """
        prompt = f"Suggest 5 short eBay search keywords related to: {product_name}. Output as a comma-separated list."
        resp = self._run_ollama(prompt)
        parts = [x.strip() for x in resp.replace("\n", ",").split(",") if x.strip()]
        return parts[:5] if parts else [resp]

    def analyze_opportunity(self, product_name: str, ebay_data, local_price_myr: float):
        """ Analyze opportunity of selling product_name based on competitor listings and local supplier price. """
        comp_lines = []
        for title, price, url in ebay_data[:5]:
            comp_lines.append(f"- {title} at {price}")
        comps = "\n".join(comp_lines)
        prompt = f""" You are an expert eBay product analyst. Product: {product_name} Local Supplier Price (MYR): {local_price_myr} Competitor Listings: {comps} Question: Is this product a good selling opportunity? Consider margins, demand, and competition. Give a short analysis in 3-5 bullet points.
        """
        return self._run_ollama(prompt)

    def forecast_trends(self, product_names):
        """ Forecast short/long-term demand trends for a list of product names. """
        prompt = "Forecast demand trends for these products.\n\nMark each as Strong, Seasonal, Declining, or Stable:\n\n"
        for name in product_names:
            prompt += f"- {name}\n"
        return self._run_ollama(prompt)

# ---------------- GUI Application ----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI eBay Seller Assistant — Ultimate")
        self.resize(1200, 820)

        # state
        self.local_items: List[LocalItem] = []  # loadable
        self.last_results: List[Tuple[str,str,str]] = []
        self.last_market_currency = "USD"  # 'USD'/'GBP'/'AUD'
        self.fx_cache = {}  # currency -> rate MYR->currency (1 MYR -> X foreign)
        self.shipping_rates = DEFAULT_SHIPPING.copy()
        self.ebay_access_token = None  # after OAuth
        self.ebay_client: Optional[Any] = None
        self.ai = AIHelper(SETTINGS)

        # UI
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)

        # Tab A - Local Items
        self.tab_a = QWidget(); self.tabs.addTab(self.tab_a, "A: Local Items")
        self._build_tab_a()

        # Tab B - Market Research
        self.tab_b = QWidget(); self.tabs.addTab(self.tab_b, "B: Market Research")
        self._build_tab_b()

        # Tab C - Smart Match & AI Rank
        self.tab_c = QWidget(); self.tabs.addTab(self.tab_c, "C: Smart Match")
        self._build_tab_c()

        # Tab E - Trending import / Scraper & Alerts
        self.tab_e = QWidget(); self.tabs.addTab(self.tab_e, "E: Trending / Alerts")
        self._build_tab_e()

        # status/log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(140)
        v.addWidget(self.log)

        # load defaults
        self._load_demo_local_items()
        self._refresh_local_table()

    # ---------- Tab A ----------
    def _build_tab_a(self):
        layout = QVBoxLayout(self.tab_a)
        hl = QHBoxLayout()
        btn_load_csv = QPushButton("Load suppliers CSV")
        btn_load_csv.clicked.connect(self._load_suppliers_csv)
        btn_save_csv = QPushButton("Save local items CSV")
        btn_save_csv.clicked.connect(self._save_local_csv)
        btn_add = QPushButton("Add New Item")
        btn_add.clicked.connect(self._add_local_item_dialog)
        hl.addWidget(btn_load_csv); hl.addWidget(btn_save_csv); hl.addWidget(btn_add)
        layout.addLayout(hl)

        self.local_table = QTableWidget(0, 6)
        self.local_table.setHorizontalHeaderLabels(["SKU","Name","Price MYR","Weight (g)","MOQ","Notes"])
        layout.addWidget(self.local_table)

    def _load_demo_local_items(self):
        # demo data
        self.local_items = [
            LocalItem("BATIK001","Batik Scarf",25.0,120,5,"Handmade"),
            LocalItem("PB001","Powerbank 10000mAh",35.0,220,10,"Electronics"),
            LocalItem("DURCOF1","Durian Coffee Pack",25.0,200,20,"Niche snack"),
        ]

    def _refresh_local_table(self):
        self.local_table.setRowCount(len(self.local_items))
        for i, it in enumerate(self.local_items):
            self.local_table.setItem(i,0,QTableWidgetItem(it.sku))
            self.local_table.setItem(i,1,QTableWidgetItem(it.name))
            self.local_table.setItem(i,2,QTableWidgetItem(str(it.supplier_price_myr)))
            self.local_table.setItem(i,3,QTableWidgetItem(str(it.weight_g)))
            self.local_table.setItem(i,4,QTableWidgetItem(str(it.moq)))
            self.local_table.setItem(i,5,QTableWidgetItem(it.notes))

    def _load_suppliers_csv(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open suppliers CSV", "", "CSV Files (*.csv);;All Files (*)")
        if not p: return
        rows = import_trending_csv(p)
        if rows:
            self.local_items = []
            for r in rows:
                sku = r.get("sku") or r.get("name")[:8].upper()
                self.local_items.append(LocalItem(sku, r.get("name"), r.get("price_myr"), r.get("weight_g",100), 1, r.get("notes","")))
            self._refresh_local_table()
            self._log(f"Loaded {len(self.local_items)} local items from {p}")
        else:
            QMessageBox.warning(self, "Load failed", f"Failed to parse file or file empty: {p}")

    def _save_local_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save local items CSV", "", "CSV Files (*.csv)")
        if not p: return
        rows = []
        for it in self.local_items:
            rows.append({"sku":it.sku,"name":it.name,"price_myr":it.supplier_price_myr,"weight_g":it.weight_g,"moq":it.moq,"notes":it.notes})
        pd.DataFrame(rows).to_csv(p,index=False)
        self._log(f"Exported local items to {p}")

    def _add_local_item_dialog(self):
        sku, ok = QInputDialog.getText(self, "SKU", "Enter SKU:")
        if not ok or not sku: return
        name, ok = QInputDialog.getText(self, "Name", "Enter product name:")
        if not ok or not name: return
        price, ok = QInputDialog.getText(self, "Price MYR", "Enter supplier price (MYR):")
        if not ok or not price: return
        try:
            price_f = float(price)
        except:
            QMessageBox.warning(self,"Invalid","Price is invalid")
            return
        weight, ok = QInputDialog.getInt(self,"Weight (g)","Enter weight in grams:",100,10,100000)
        if not ok: return
        self.local_items.append(LocalItem(sku, name, price_f, weight, 1, ""))
        self._refresh_local_table()

    # ---------- Tab B ----------
    def _build_tab_b(self):
        layout = QVBoxLayout(self.tab_b)
        ctrl = QHBoxLayout()
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("Keyword e.g. 'powerbank'")
        self.market_combo = QComboBox()
        self.market_combo.addItem("US (EBAY-US)", ("EBAY-US","USD","US"))
        self.market_combo.addItem("UK (EBAY-GB)", ("EBAY-GB","GBP","UK"))
        self.market_combo.addItem("AU (EBAY-AU)", ("EBAY-AU","AUD","AU"))
        self.entries_spin = QSpinBox(); self.entries_spin.setRange(1,50); self.entries_spin.setValue(10)
        btn_search = QPushButton("Search eBay"); btn_search.clicked.connect(self._on_search_ebay)
        btn_fx = QPushButton("Refresh FX (live)"); btn_fx.clicked.connect(self._on_refresh_fx)
        ctrl.addWidget(QLabel("Keyword:")); ctrl.addWidget(self.keyword_input)
        ctrl.addWidget(QLabel("Market:")); ctrl.addWidget(self.market_combo)
        ctrl.addWidget(QLabel("Entries:")); ctrl.addWidget(self.entries_spin)
        ctrl.addWidget(btn_search); ctrl.addWidget(btn_fx)
        layout.addLayout(ctrl)

        self.market_table = QTableWidget(0,4)
        self.market_table.setHorizontalHeaderLabels(["Title","Price","Currency","URL"])
        layout.addWidget(self.market_table)

    def _on_refresh_fx(self):
        idx = self.market_combo.currentIndex()
        _, curr, _ = self.market_combo.itemData(idx)
        rate = fetch_exchange_rate(curr)
        if rate:
            self.fx_cache[curr] = rate
            self._log(f"Fetched FX: 1 MYR -> {rate} {curr}")
            QMessageBox.information(self, "FX Updated", f"1 MYR -> {rate} {curr}")
        else:
            QMessageBox.warning(self, "FX Failed", "Failed to fetch FX; using defaults.")

    def _on_search_ebay(self):
        kw = self.keyword_input.text().strip()
        if not kw:
            QMessageBox.warning(self,"Missing","Please enter a keyword")
            return

        # --- AI keyword expansion (Ollama) ---
        if hasattr(self, "ai"):
            try:
                keywords = self.ai.expand_keywords(kw)
                self._log(f" AI suggests related keywords: {', '.join(keywords)}")
            except Exception:
                keywords = [kw]
        idx = self.market_combo.currentIndex()
        global_id, curr, short = self.market_combo.itemData(idx)
        entries = int(self.entries_spin.value())
        self._log(f"Searching eBay {global_id} for '{kw}' ...")
        items = ebay_find_items(kw, entries_per_page=entries, global_id=global_id)
        self.last_results = items
        self.last_market_currency = curr

        # populate table
        self.market_table.setRowCount(len(items))
        for i, it in enumerate(items):
            title, price_str, url = it
            val = parse_price_string(price_str) or 0.0
            self.market_table.setItem(i, 0, QTableWidgetItem(title))
            self.market_table.setItem(i, 1, QTableWidgetItem(str(val)))
            self.market_table.setItem(i, 2, QTableWidgetItem(curr))
            self.market_table.setItem(i, 3, QTableWidgetItem(url))
        self._log(f"Found {len(items)} results")

        # auto-run AI match
        self._run_ai_match(auto=True)

    # ---------- Tab C ----------
    def _build_tab_c(self):
        layout = QVBoxLayout(self.tab_c)
        top = QHBoxLayout()
        self.min_margin_spin = QDoubleSpinBox(); self.min_margin_spin.setRange(-1000,10000); self.min_margin_spin.setValue(0)
        self.min_margin_spin.setSuffix(" %")
        top.addWidget(QLabel("Min margin % filter:")); top.addWidget(self.min_margin_spin)
        btn_run = QPushButton("Run AI Match"); btn_run.clicked.connect(lambda: self._run_ai_match(auto=False))
        btn_export = QPushButton("Export Report (CSV + PNG)"); btn_export.clicked.connect(self._export_report)
        top.addWidget(btn_run); top.addWidget(btn_export)
        layout.addLayout(top)

        self.match_table = QTableWidget(0,10)
        self.match_table.setHorizontalHeaderLabels([
            "Local SKU","Local Name","Local MYR","Weight(g)","Target Title","Target Price (foreign)",
            "Target Curr","Target RM","Margin %","AI Rank"
        ])
        layout.addWidget(self.match_table)

        self.summary_label = QLabel("TOP 5 Picks will appear here...")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # Chart
        self.figure = Figure(figsize=(8,3))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

    def _run_ai_match(self, auto=False):
        if not self.last_results:
            QMessageBox.information(self,"No data","Please run a market search in Tab B first.")
            return

        # ensure FX rate available
        curr = self.last_market_currency
        fx = self.fx_cache.get(curr)
        if fx is None:
            fx = fetch_exchange_rate(curr) or DEFAULT_FX.get(curr, 0.22)
            self.fx_cache[curr] = fx

        # build parsed competitor prices
        competitor_prices = []
        for title, price_str, url in self.last_results:
            v = parse_price_string(price_str)
            if v is not None:
                competitor_prices.append(v)
        if not competitor_prices:
            QMessageBox.warning(self,"No competitor prices","No numeric competitor prices parsed.")
            return

        matches = []
        max_price_foreign = max([p for p in competitor_prices]) if competitor_prices else 0.0

        for it in self.local_items:
            for title, price_str, url in self.last_results:
                pf = parse_price_string(price_str) or 0.0
                target_rm = (pf / fx) if fx != 0 else pf / DEFAULT_FX.get(curr,0.22)
                margin_pct = ((target_rm - it.supplier_price_myr) / it.supplier_price_myr) * 100.0 if it.supplier_price_myr>0 else -999.0
                competition = len(self.last_results)
                price_score = (pf / max_price_foreign) * 100.0 if max_price_foreign>0 else 0.0
                margin_cap = 200.0
                margin_norm = max(0.0, min(100.0, (margin_pct / margin_cap) * 100.0))
                comp_score = max(0.0, min(100.0, (1.0 - min(competition / COMPETITION_MAX, 1.0)) * 100.0))
                score = WEIGHT_MARGIN * margin_norm + WEIGHT_COMPETITION * comp_score + WEIGHT_PRICE * price_score
                stars = 5 if score>=85 else 4 if score>=70 else 3 if score>=50 else 2 if score>=30 else 1
                matches.append({
                    "local_sku": it.sku,
                    "local_name": it.name,
                    "local_myr": it.supplier_price_myr,
                    "weight_g": it.weight_g,
                    "target_title": title,
                    "target_price_foreign": pf,
                    "target_currency": curr,
                    "target_rm": round(target_rm,2),
                    "margin_pct": round(margin_pct,1),
                    "score": round(score,2),
                    "stars": stars,
                    "url": url
                })

        # sort and filter
        matches_sorted = sorted(matches, key=lambda x: x["score"], reverse=True)
        min_margin = float(self.min_margin_spin.value())
        matches_filtered = [m for m in matches_sorted if m["margin_pct"]>=min_margin]

        # populate table
        self.match_table.setRowCount(len(matches_filtered))
        for r, m in enumerate(matches_filtered):
            self.match_table.setItem(r,0,QTableWidgetItem(m["local_sku"]))
            self.match_table.setItem(r,1,QTableWidgetItem(m["local_name"]))
            self.match_table.setItem(r,2,QTableWidgetItem(str(m["local_myr"])))
            self.match_table.setItem(r,3,QTableWidgetItem(str(m["weight_g"])))
            self.match_table.setItem(r,4,QTableWidgetItem(m["target_title"]))
            self.match_table.setItem(r,5,QTableWidgetItem(str(m["target_price_foreign"])))
            self.match_table.setItem(r,6,QTableWidgetItem(m["target_currency"]))
            self.match_table.setItem(r,7,QTableWidgetItem(str(m["target_rm"])))
            self.match_table.setItem(r,8,QTableWidgetItem(str(m["margin_pct"])))
            self.match_table.setItem(r,9,QTableWidgetItem(f"{m['score']} / {m['stars']}★"))

        # top 5 summary
        top5 = matches_filtered[:5]
        summary_lines = [f"TOP 5 Picks for {curr}:"]
        for i, t in enumerate(top5,1):
            summary_lines.append(f"{i}. {t['local_name']} (SKU {t['local_sku']}) → Margin {t['margin_pct']}% — {t['score']} pts — {t['stars']}★")
        self.summary_label.setText("\n".join(summary_lines) if len(summary_lines)>1 else "No matches above filter")

        # chart
        try:
            names = [f"{t['local_name']}" for t in top5]
            margins = [t['margin_pct'] for t in top5]
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            bars = ax.bar(names, margins)
            ax.set_ylabel("Margin %")
            ax.set_title(f"Top 5 Margins - {curr}")
            ax.bar_label(bars, fmt="%.1f%%")
            self.canvas.draw()
        except Exception as e:
            self._log(f"Chart error: {e}")

        # smart alert if top item exceeds threshold
        if top5:
            best = top5[0]
            if best["margin_pct"] >= 200:
                msg = f" High-margin alert: {best['local_name']} SKU {best['local_sku']} margin {best['margin_pct']}% on {curr}"
                send_telegram_alert(msg)
                self._log("Smart alert sent (if Telegram configured).")

        # store matches for report/export
        self._last_matches = matches_filtered

        # --- AI Analysis with Ollama ---
        if hasattr(self, "ai") and top5:
            analysis = self.ai.analyze_opportunity(top5[0]['local_name'], self.last_results, top5[0]['local_myr'])
            self._log(f" AI Analysis:\n{analysis}")

        if auto:
            self.tabs.setCurrentIndex(2)

    def _export_report(self):
        if not hasattr(self,"_last_matches") or not self._last_matches:
            QMessageBox.information(self,"No data","No matches to export.\nRun AI Match first.")
            return
        p, _ = QFileDialog.getSaveFileName(self,"Save report CSV","ai_report.csv","CSV Files (*.csv)")
        if not p: return
        # save CSV
        export_report_csv(p, self._last_matches)
        # save chart PNG
        png_path = p + ".png"
        try:
            self.figure.savefig(png_path)
        except Exception:
            pass
        QMessageBox.information(self,"Exported",f"Report saved to {p} (chart may be at {png_path})")
        self._log(f"Report exported: {p}")

    # ---------- Tab E (Trending / Alerts) ----------
    def _build_tab_e(self):
        layout = QVBoxLayout(self.tab_e)
        hl = QHBoxLayout()
        btn_import = QPushButton("Import trending CSV (Shopee/Lazada export)")
        btn_import.clicked.connect(self._import_trending_csv)
        btn_alerts = QPushButton("Configure Alerts (Telegram)")
        btn_alerts.clicked.connect(self._configure_alerts)
        hl.addWidget(btn_import); hl.addWidget(btn_alerts)
        layout.addLayout(hl)

        self.trending_table = QTableWidget(0,4)
        self.trending_table.setHorizontalHeaderLabels(["Name","Price MYR","Weight g","Notes"])
        layout.addWidget(self.trending_table)

        self.btn_crosscheck = QPushButton("Cross-check trending vs eBay demand (keyword search)")
        self.btn_crosscheck.clicked.connect(self._crosscheck_trending)
        layout.addWidget(self.btn_crosscheck)

        # --- NEW: AI Forecast label ---
        self.trending_forecast_label = QLabel("AI Profitability Forecast will appear here...")
        self.trending_forecast_label.setWordWrap(True)
        layout.addWidget(self.trending_forecast_label)

    def _import_trending_csv(self):
        p,_ = QFileDialog.getOpenFileName(self,"Open trending CSV","","CSV Files (*.csv)")
        if not p: return
        rows = import_trending_csv(p)
        if not rows:
            QMessageBox.warning(self,"Import failed","No rows parsed")
            return
        self.trend_data = rows
        self.trending_table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            self.trending_table.setItem(i,0,QTableWidgetItem(r.get("name","")))
            self.trending_table.setItem(i,1,QTableWidgetItem(str(r.get("price_myr",""))))
            self.trending_table.setItem(i,2,QTableWidgetItem(str(r.get("weight_g",""))))
            self.trending_table.setItem(i,3,QTableWidgetItem(str(r.get("notes",""))))
        self._log(f"Imported trending CSV: {p}")

        # ✅ AI forecast for trending products
        if hasattr(self, "ai") and rows:
            names = [r.get("name","") for r in rows if r.get("name")]
            forecast = self.ai.forecast_trends(names)
            self._log(f" AI Forecast:\n{forecast}")

    def _configure_alerts(self):
        token, ok = QInputDialog.getText(self,"Telegram Bot Token","Enter Telegram Bot Token (or leave blank):")
        if ok:
            chat_id, ok2 = QInputDialog.getText(self,"Telegram Chat ID","Enter Telegram Chat ID (or leave blank):")
            if ok2:
                # persist in memory (not secure in file)
                global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
                TELEGRAM_BOT_TOKEN = token.strip()
                TELEGRAM_CHAT_ID = chat_id.strip()
                self._log("Telegram config updated (in-memory).")

    def _crosscheck_trending(self):
        if not hasattr(self,"trend_data") or not self.trend_data:
            QMessageBox.information(self,"No data","Import trending CSV first.")
            return

        # For each trending local item, search eBay using its name and record top match margin
        report = []
        for r in self.trend_data:
            name = r.get("name","")
            price_myr = r.get("price_myr",0.0)
            self.keyword_input.setText(name)
            # call ebay_find_items for name, 5 entries
            items = ebay_find_items(name, entries_per_page=5, global_id="EBAY-US")
            if not items:
                continue
            # compute simple best margin with US default fx
            curr = "USD"
            fx = fetch_exchange_rate(curr) or DEFAULT_FX.get(curr,0.22)
            comp_prices = [parse_price_string(it[1]) for it in items if parse_price_string(it[1]) is not None]
            if not comp_prices:
                continue
            med = sorted(comp_prices)[len(comp_prices)//2]
            target_rm = med / fx if fx!=0 else med / DEFAULT_FX.get(curr,0.22)
            margin = ((target_rm - price_myr)/price_myr)*100 if price_myr>0 else -999
            report.append({"name":name,"local_myr":price_myr,"comp_med_foreign":med,"comp_med_rm":round(target_rm,2),"margin_pct":round(margin,1)})

        # show report
        if report:
            df = pd.DataFrame(report)
            p, _ = QFileDialog.getSaveFileName(self,"Save Crosscheck CSV","crosscheck.csv","CSV Files (*.csv)")
            if p:
                df.to_csv(p,index=False)
                QMessageBox.information(self,"Saved",f"Crosscheck saved to {p}")
                self._log(f"Saved crosscheck to {p}")

            # --- AI Profitability Forecast (Ollama) ---
            if hasattr(self, "ai"):
                lines = []
                for row in report[:10]:
                    lines.append(f"- {row['name']}: margin {row['margin_pct']}% (local MYR {row['local_myr']}, comp RM {row['comp_med_rm']})")
                prompt = "You are an expert eBay analyst.\n\nCrosscheck results:\n" + "\n".join(lines) + "\n\nQuestion: Which 2-3 products look most profitable and sustainable? Give a short ranked summary."
                forecast = self.ai._run_ollama(prompt)
                self._log(f" AI Profitability Forecast:\n{forecast}")
                self.trending_forecast_label.setText(f" AI Forecast:\n{forecast}")
        else:
            QMessageBox.information(self,"No matches","No crosscheck results found.")

    # ---------- Utilities ----------
    def _log(self,msg:str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        print(f"[{ts}] {msg}")

def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
