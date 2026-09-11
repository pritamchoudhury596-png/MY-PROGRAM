"""
dashboard.py
------------
WastePulse — SPCB Central Monitoring Dashboard
"Smart Sensing, Smarter Living"

Full laptop-widescreen customtkinter dashboard for the "SPCB Admin" role.

EXISTING (preserved, unchanged in behavior):
  - Reads scan records live from the shared `waste_logs.json` file
    (written by waste_maneger.py).
  - HeaderBar branding, BASE_DIR-anchored path resolution.
  - 4 KPI cards (Total Scans, % Biodegradable, % Non-Biodegradable,
    Hazardous Flags).
  - Full LogTable (ttk.Treeview) with all columns, styling, and the
    double-click handler that opens precise Google Maps pins.
  - 5-second auto-refresh polling of waste_logs.json.

THIS REVISION:
  - Now binds to `hardware_integration.py`'s real-hardware-only
    `PlantEngine` (the old mock simulator import is gone).
  - Visual Analytics panel now shows FOUR charts: Category Distribution
    (from waste_logs.json), plus live Moisture, IR Sensor, and Metal
    Detector trend graphs (from the plant engine's real telemetry).
  - Live LLM Card: status badge ("LLM: llama3.2:3b Active" vs
    "Fallback: Deterministic Rule Engine Active") plus a scrolling
    decision-reasoning log.
  - ESP32 Plant Status panel: connection state ("0 Machines Connected /
    Idle / Disconnected" when no board is detected), live numeric
    sensor readouts (Moisture / IR / Metal), and relay indicators for
    the Conveyor Belt, Mechanical Cutter, and the Compost / Industrial /
    Biohazard-Emergency-Stop sorting bins.
  - Zero-state guarantee: when no machine is connected, every sensor
    graph and relay indicator stays visible but renders strictly flat
    at 0 / Idle — dashboard.py never fabricates data itself; it only
    ever displays exactly what PlantEngine reports.
  - A "Sign Out" button in the header bar that safely tears down
    the dashboard's background timers, closes this window, and hands
    off to authentycation.py's Login Screen.
  - PYINSTALLER PATCH: BASE_DIR now honors the WASTEPULSE_BASE_DIR
    environment variable (set by main.py) before falling back to
    __file__, so waste_logs.json and the logo asset resolve correctly
    next to the real .exe even if this module is ever imported inside
    a frozen build instead of run as a plain script. This is a no-op
    when the env var isn't set (i.e. running normally) — everything
    else in this file is untouched.
  - NEW: A floating "Soil Risk Predictor" button that opens
    soil_ai.py's SoilRiskPanel in its own Toplevel window.
    This addition is purely additive — it does not modify, remove, or
    alter any existing widget, layout, or business logic in this file.
    If soil_ai .py isn't present, the button shows a friendly
    notice instead of crashing (same defensive pattern already used
    for PlantEngine / authentycation.py elsewhere in this file).

The panels bind to a `PlantEngine` instance (see hardware_integration.py)
through a small set of thread-safe getter methods. `PlantEngine` owns
and runs its own background `threading.Thread` internally (started
automatically on construction) — dashboard.py never touches threading
directly, it only calls read-only getters from a Tkinter `.after()`
timer on the main thread, so `mainloop()` always stays smooth regardless
of how busy the plant loop is. If hardware_integration.py isn't present
or an engine instance isn't supplied, a built-in no-op stub
(`_NullPlantEngine`) is used automatically so this file still runs
standalone and always shows an honest "Simulation Mode" / "0 Machines
Connected / Idle / Disconnected" state instead of crashing or faking data.

Dependencies:
    pip install customtkinter pillow matplotlib
"""

import os
import json
import webbrowser
from tkinter import ttk, messagebox
from typing import List, Dict, Optional

import customtkinter as ctk
from PIL import Image

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from hardwareintigration import PlantEngine, DISCONNECTED_STATUS_TEXT, DEFAULT_LLM_MODEL
except ImportError:
    PlantEngine = None
    DISCONNECTED_STATUS_TEXT = "0 Machines Connected / Idle / Disconnected"
    DEFAULT_LLM_MODEL = "llama3.2:3b"

# Sign Out hand-off target. dashboard.py (SPCB Admin) hands control back
# to authentycation.py's Login Screen on Sign Out — never to
# waste_manager.py, and never via subprocess/os.system.
try:
    from authentycation import start_login_session
except ImportError:
    start_login_session = None

# NEW: Soil & Landslide Risk Predictor panel (SIH26001) — purely
# additive. Falls back to None if the module isn't present (or
# customtkinter is unavailable inside it), so dashboard.py still runs
# standalone exactly as before; the launcher button below handles the
# None case gracefully instead of crashing.
try:
    from soil_ai import SoilRiskPanel
except ImportError:
    SoilRiskPanel = None


# ---------------------------------------------------------------------------
# 1. THEME — WastePulse brand identity (Light Mode) — UNCHANGED
# ---------------------------------------------------------------------------

class Theme:
    PRIMARY = "#15803D"
    PRIMARY_HOVER = "#166534"
    NAVY = "#0F2537"
    BACKGROUND = "#F8FAFC"
    CARD_BG = "#FFFFFF"
    BORDER = "#E2E8F0"
    SLATE_TEXT = "#334155"
    MUTED_TEXT = "#64748B"
    DANGER = "#B91C1C"
    HAZARD_BG = "#FEF2F2"
    HAZARD_TEXT = "#B91C1C"
    ACCENT = "#0284C7"
    ACCENT_HOVER = "#0369A1"
    WARN_BG = "#FFFBEB"
    WARN_TEXT = "#B45309"
    OFF_GREY = "#94A3B8"
    PANEL_BG = "#F1F5F9"

    FONT_FAMILY = "Segoe UI"
    TITLE_FONT = (FONT_FAMILY, 24, "bold")
    MOTTO_FONT = (FONT_FAMILY, 12, "bold")
    SUBTEXT_FONT = (FONT_FAMILY, 11)
    KPI_VALUE_FONT = (FONT_FAMILY, 30, "bold")
    KPI_LABEL_FONT = (FONT_FAMILY, 12, "bold")
    CARD_HEADER_FONT = (FONT_FAMILY, 15, "bold")
    ROW_FONT = (FONT_FAMILY, 13)
    SMALL_FONT = (FONT_FAMILY, 11)
    BADGE_FONT = (FONT_FAMILY, 12, "bold")
    MONO_FONT = ("Consolas", 11)

    CORNER_RADIUS = 12


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")


# ---------------------------------------------------------------------------
# 2. CONSTANTS — UNCHANGED, plus additions for the sensor panels
# ---------------------------------------------------------------------------

# PYINSTALLER PATCH: prefer the WASTEPULSE_BASE_DIR environment variable
# (exported by main.py, which resolves it via sys.executable when frozen)
# over a plain __file__-based path. Falls back to the original behavior
# untouched when the env var isn't set, so standalone `python dashboard.py`
# runs exactly as before.
BASE_DIR = os.environ.get("WASTEPULSE_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
LOGO_PATH_CANDIDATES = tuple(
    os.path.join(BASE_DIR, name) for name in ("logo.png", "logo.jpg", "logo.jpeg")
)

WASTE_LOGS_PATH = os.path.join(BASE_DIR, "waste_logs.json")
LOGO_SIZE = (64, 64)
AUTO_REFRESH_MS = 5000            # live waste_logs.json table auto-refresh interval
ENGINE_POLL_MS = 2000             # live LLM/hardware/telemetry poll interval
SENSOR_HISTORY_LEN = 30           # points kept on each sensor trend chart
REASONING_LOG_MAX_LINES = 200     # cap on the LLM reasoning textbox

TABLE_COLUMNS = ("timestamp", "user", "location", "maps_link", "category", "confidence", "status")
TABLE_HEADINGS = {
    "timestamp": "Timestamp",
    "user": "User (Public)",
    "location": "Location",
    "maps_link": "Google Maps Pin (double-click to open)",
    "category": "Waste Category",
    "confidence": "Confidence",
    "status": "Status",
}

CATEGORY_BUCKET_BY_LABEL = {
    "Dry Waste": "Non-Biodegradable",
    "Wet Waste": "Biodegradable",
    "Industrial Waste": "Hazardous",
    "Bio-Medical Waste": "Hazardous",
}

CATEGORY_CHART_ORDER = ("Dry Waste", "Wet Waste", "Industrial Waste", "Bio-Medical Waste")
CATEGORY_CHART_COLORS = {
    "Dry Waste": "#0284C7",
    "Wet Waste": "#15803D",
    "Industrial Waste": "#B45309",
    "Bio-Medical Waste": "#B91C1C",
}

# Relay indicator definitions: (internal key, display label). The plant
# engine's get_relay_states() returns a Dict[str, bool] using these same
# keys — see hardware_integration.py's RELAY_KEYS / RELAY_PIN_MAP.
RELAY_INDICATORS = (
    ("conveyor_belt", "Conveyor Motor (Pin 25)"),
    ("mechanical_cutter", "Cutter Blade (Pin 26)"),
    ("bin_compost", "Compost Bin Servo"),
    ("bin_industrial", "Industrial Bin Servo"),
    ("bin_biohazard_estop", "Biohazard Gate / Emergency Stop"),
)


# ---------------------------------------------------------------------------
# 3. WINDOW SIZING HELPER — UNCHANGED
# ---------------------------------------------------------------------------

def maximize_window(window: ctk.CTk, fallback_size: str = "1400x850") -> None:
    """Best-effort full laptop-screen layout across Windows/Linux/macOS."""
    try:
        window.state("zoomed")
        return
    except Exception:
        pass
    try:
        window.attributes("-zoomed", True)
        return
    except Exception:
        pass
    try:
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        window.geometry(f"{screen_w}x{screen_h}+0+0")
    except Exception:
        window.geometry(fallback_size)


# ---------------------------------------------------------------------------
# 4. DATA LAYER — UNCHANGED (waste_logs.json live loading is untouched)
# ---------------------------------------------------------------------------

def load_waste_logs() -> List[Dict]:
    """Reads the shared waste_logs.json file. Missing or corrupt files
    are treated as an empty log set rather than crashing the dashboard."""
    if not os.path.exists(WASTE_LOGS_PATH):
        return []
    try:
        with open(WASTE_LOGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def build_maps_link(lat, lon) -> str:
    if lat is None or lon is None:
        return ""
    return f"https://www.google.com/maps?q={lat},{lon}"


def resolve_maps_link(entry: Dict) -> str:
    existing = entry.get("maps_link")
    if existing:
        return existing
    return build_maps_link(entry.get("lat"), entry.get("lon"))


def compute_kpis(logs: List[Dict]) -> Dict[str, float]:
    total = len(logs)
    if total == 0:
        return {"total": 0, "pct_biodegradable": 0.0, "pct_non_biodegradable": 0.0,
                "hazardous_count": 0}

    biodegradable = 0
    non_biodegradable = 0
    hazardous = 0

    for entry in logs:
        bucket = CATEGORY_BUCKET_BY_LABEL.get(entry.get("category", ""), None)
        if bucket == "Biodegradable":
            biodegradable += 1
        elif bucket == "Non-Biodegradable":
            non_biodegradable += 1
        elif bucket == "Hazardous":
            hazardous += 1

    return {
        "total": total,
        "pct_biodegradable": round(biodegradable / total * 100, 1),
        "pct_non_biodegradable": round(non_biodegradable / total * 100, 1),
        "hazardous_count": hazardous,
    }


def compute_category_distribution(logs: List[Dict]) -> Dict[str, int]:
    counts = {label: 0 for label in CATEGORY_CHART_ORDER}
    for entry in logs:
        label = entry.get("category", "")
        if label in counts:
            counts[label] += 1
    return counts


# ---------------------------------------------------------------------------
# 5. PLANT ENGINE INTEGRATION — thread-safe read side only
#    dashboard.py never drives the plant loop itself; it only polls a
#    handful of getter methods on a background-thread-backed PlantEngine
#    (see hardware_integration.py) on a Tkinter .after() timer, which
#    keeps the UI thread completely non-blocking.
# ---------------------------------------------------------------------------

class _NullPlantEngine:
    """Safe standalone fallback used when hardware_integration.py isn't
    available or no engine instance was supplied. Reports the same
    strictly-zeroed 0 / Idle / Disconnected state real PlantEngine
    reports when no ESP32 is connected, so the dashboard is always
    truthful about what it actually knows — never fabricated."""

    def get_hardware_status_text(self) -> str:
        return DISCONNECTED_STATUS_TEXT

    def is_hardware_connected(self) -> bool:
        return False

    def get_llm_status_text(self) -> str:
        return "Fallback: Deterministic Rule Engine Active"

    def get_relay_states(self) -> Dict[str, bool]:
        return {key: False for key, _ in RELAY_INDICATORS}

    def get_recent_moisture_readings(self, limit: int = SENSOR_HISTORY_LEN) -> List[float]:
        return [0.0] * limit

    def get_recent_ir_readings(self, limit: int = SENSOR_HISTORY_LEN) -> List[float]:
        return [0.0] * limit

    def get_recent_metal_readings(self, limit: int = SENSOR_HISTORY_LEN) -> List[float]:
        return [0.0] * limit

    def get_latest_sensor_snapshot(self) -> Dict[str, object]:
        return {"moisture_level": 0.0, "metal_detected": False,
                "biohazard_detected": False, "ir_anomaly": False}

    def get_latest_reasoning(self) -> Optional[str]:
        return None


def resolve_engine(engine: Optional[object]) -> object:
    """Picks the engine to bind to: an explicitly supplied instance first,
    then a real PlantEngine() if the module is importable, otherwise the
    safe no-op stub."""
    if engine is not None:
        return engine
    if PlantEngine is not None:
        try:
            return PlantEngine()
        except Exception:
            pass
    return _NullPlantEngine()


# ---------------------------------------------------------------------------
# 6. UI COMPONENTS
# ---------------------------------------------------------------------------

class HeaderBar(ctk.CTkFrame):
    """Navy top banner with brand identity, admin context, and refresh.
    UNCHANGED, plus a Sign Out button in the same info column."""

    def __init__(self, master, username: str, on_refresh, on_sign_out=None):
        super().__init__(master, fg_color=Theme.NAVY, corner_radius=0, height=110)
        self.pack_propagate(False)

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="y", padx=28)

        logo_row = ctk.CTkFrame(left, fg_color="transparent")
        logo_row.pack(anchor="w", expand=True)

        self._build_logo(logo_row).pack(side="left", padx=(0, 16))

        text_col = ctk.CTkFrame(logo_row, fg_color="transparent")
        text_col.pack(side="left")
        ctk.CTkLabel(text_col, text="WastePulse — SPCB Central Dashboard",
                     font=Theme.TITLE_FONT, text_color="#FFFFFF").pack(anchor="w")
        ctk.CTkLabel(text_col, text="Smart Sensing, Smarter Living · Government Data Portal",
                     font=Theme.SUBTEXT_FONT, text_color="#CBD5E1").pack(anchor="w")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="right", fill="y", padx=28)

        info_col = ctk.CTkFrame(right, fg_color="transparent")
        info_col.pack(expand=True)

        ctk.CTkLabel(info_col, text=f"Logged in as: {username}", font=Theme.SMALL_FONT,
                     text_color="#CBD5E1").pack(anchor="e", pady=(0, 8))

        ctk.CTkButton(
            info_col, text="⟳ Refresh Data", font=Theme.ROW_FONT, height=36, width=150,
            corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.PRIMARY,
            hover_color=Theme.PRIMARY_HOVER, text_color="#FFFFFF", command=on_refresh
        ).pack(anchor="e")

        ctk.CTkButton(
            info_col, text="⏻ Sign Out", font=Theme.ROW_FONT, height=36, width=150,
            corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.DANGER,
            hover_color="#8f1717", text_color="#FFFFFF", command=on_sign_out
        ).pack(anchor="e", pady=(8, 0))

    def _build_logo(self, master):
        for candidate in LOGO_PATH_CANDIDATES:
            if os.path.exists(candidate):
                try:
                    pil_image = Image.open(candidate).convert("RGBA")
                    pil_image.thumbnail(LOGO_SIZE, Image.LANCZOS)
                    canvas = Image.new("RGBA", LOGO_SIZE, (0, 0, 0, 0))
                    offset = (
                        (LOGO_SIZE[0] - pil_image.width) // 2,
                        (LOGO_SIZE[1] - pil_image.height) // 2,
                    )
                    canvas.paste(pil_image, offset, pil_image)
                    ctk_image = ctk.CTkImage(light_image=canvas, size=LOGO_SIZE)
                    return ctk.CTkLabel(master, image=ctk_image, text="")
                except Exception:
                    continue

        badge = ctk.CTkFrame(master, width=LOGO_SIZE[0], height=LOGO_SIZE[1], corner_radius=16,
                              fg_color=Theme.PRIMARY)
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="WP", font=(Theme.FONT_FAMILY, 18, "bold"),
                     text_color="#FFFFFF").pack(expand=True)
        return badge


class StatusBanner(ctk.CTkFrame):
    """Slim strip directly under the header showing the two headline
    live states — hardware connectivity and LLM engine status — at a
    glance. UNCHANGED in structure."""

    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=12)
        row.grid_columnconfigure((0, 1), weight=1, uniform="banner")

        hw_col = ctk.CTkFrame(row, fg_color="transparent")
        hw_col.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(hw_col, text="HARDWARE", font=Theme.SMALL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="w")
        self.hardware_badge = ctk.CTkLabel(hw_col, text="…", font=Theme.BADGE_FONT,
                                            text_color=Theme.OFF_GREY)
        self.hardware_badge.pack(anchor="w")

        llm_col = ctk.CTkFrame(row, fg_color="transparent")
        llm_col.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(llm_col, text="LLM ENGINE", font=Theme.SMALL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="e")
        self.llm_badge = ctk.CTkLabel(llm_col, text="…", font=Theme.BADGE_FONT,
                                       text_color=Theme.OFF_GREY)
        self.llm_badge.pack(anchor="e")

    def update_status(self, hardware_connected: bool, hardware_text: str, llm_active: bool, llm_text: str):
        self.hardware_badge.configure(
            text=("🟢 " if hardware_connected else "🟡 ") + hardware_text,
            text_color=Theme.PRIMARY if hardware_connected else Theme.WARN_TEXT,
        )
        self.llm_badge.configure(
            text=("🟢 " if llm_active else "🟠 ") + llm_text,
            text_color=Theme.PRIMARY if llm_active else Theme.WARN_TEXT,
        )


class KPICard(ctk.CTkFrame):
    """Single KPI tile: big value + small label, used in a 4-up row.
    UNCHANGED."""

    def __init__(self, master, label: str, accent: str = Theme.PRIMARY):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)
        self.value_label = ctk.CTkLabel(self, text="—", font=Theme.KPI_VALUE_FONT,
                                         text_color=accent)
        self.value_label.pack(anchor="w", padx=20, pady=(18, 0))
        ctk.CTkLabel(self, text=label.upper(), font=Theme.KPI_LABEL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="w", padx=20, pady=(2, 18))

    def set_value(self, value: str):
        self.value_label.configure(text=value)


class AnalyticsCard(ctk.CTkFrame):
    """Embedded Matplotlib panel with FOUR charts in a 2x2 grid:
      - Category Distribution (from waste_logs.json)
      - Moisture Trend (live, from PlantEngine)
      - IR Sensor Trend (live, from PlantEngine)
      - Metal Detector Trend (live, from PlantEngine)

    All three live sensor charts render a flat line at 0 whenever
    PlantEngine has no real ESP32 data yet (disconnected or no reading
    received) — the getters themselves zero-fill, this card just draws
    whatever it's given without adding or masking any behavior.
    Degrades to a plain notice if matplotlib isn't installed."""

    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)

        ctk.CTkLabel(self, text="Visual Analytics", font=Theme.CARD_HEADER_FONT,
                     text_color=Theme.NAVY).pack(anchor="w", padx=18, pady=(16, 8))

        if not MATPLOTLIB_AVAILABLE:
            ctk.CTkLabel(
                self, text="Install matplotlib to enable charts:  pip install matplotlib",
                font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT
            ).pack(padx=18, pady=(0, 18))
            self.figure = None
            return

        self.figure = Figure(figsize=(7.6, 6.0), dpi=100)
        self.figure.subplots_adjust(wspace=0.35, hspace=0.55, left=0.10, right=0.97,
                                     top=0.94, bottom=0.08)
        self.category_ax = self.figure.add_subplot(2, 2, 1)
        self.moisture_ax = self.figure.add_subplot(2, 2, 2)
        self.ir_ax = self.figure.add_subplot(2, 2, 3)
        self.metal_ax = self.figure.add_subplot(2, 2, 4)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self._draw_placeholder()

    def _draw_placeholder(self):
        if self.figure is None:
            return
        self.category_ax.set_title("Category Distribution", fontsize=10)
        self.moisture_ax.set_title("Moisture Trend (live)", fontsize=10)
        self.ir_ax.set_title("IR Sensor Trend (live)", fontsize=10)
        self.metal_ax.set_title("Metal Detector Trend (live)", fontsize=10)
        self.canvas.draw_idle()

    def update_category_chart(self, counts: Dict[str, int]):
        if self.figure is None:
            return
        self.category_ax.clear()
        labels = list(CATEGORY_CHART_ORDER)
        values = [counts.get(lbl, 0) for lbl in labels]
        colors = [CATEGORY_CHART_COLORS[lbl] for lbl in labels]
        short_labels = [lbl.replace(" Waste", "").replace("Bio-Medical", "Bio-Med") for lbl in labels]

        self.category_ax.bar(short_labels, values, color=colors)
        self.category_ax.set_title("Category Distribution", fontsize=10)
        self.category_ax.tick_params(axis="x", labelsize=8)
        self.category_ax.tick_params(axis="y", labelsize=8)
        self.category_ax.set_ylabel("Scans", fontsize=8)
        max_val = max(values) if values else 0
        self.category_ax.set_ylim(0, max(1, max_val + 1))
        self.canvas.draw_idle()

    def _draw_flat_or_live(self, ax, readings: List[float], title: str, ylabel: str,
                            y_max: float, color: str, fill: bool):
        """Shared drawing routine for the three live sensor charts. Draws
        exactly what it's given — a zero-filled list renders as a flat
        line at 0, real readings render as the actual live trend. No
        placeholder text, no fabricated motion."""
        ax.clear()
        x = range(len(readings)) if readings else range(1)
        y = readings if readings else [0.0]
        ax.plot(x, y, color=color, linewidth=2)
        if fill:
            ax.fill_between(x, y, color=color, alpha=0.12)
        ax.set_ylim(0, y_max)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(axis="x", labelsize=8)
        ax.tick_params(axis="y", labelsize=8)

    def update_moisture_chart(self, readings: List[float]):
        if self.figure is None:
            return
        self._draw_flat_or_live(
            self.moisture_ax, readings, "Moisture Trend (live)", "% moisture",
            y_max=100, color=Theme.ACCENT, fill=True,
        )
        self.canvas.draw_idle()

    def update_ir_chart(self, readings: List[float]):
        if self.figure is None:
            return
        self._draw_flat_or_live(
            self.ir_ax, readings, "IR Sensor Trend (live)", "Anomaly (0/1)",
            y_max=1.2, color=Theme.WARN_TEXT, fill=False,
        )
        self.canvas.draw_idle()

    def update_metal_chart(self, readings: List[float]):
        if self.figure is None:
            return
        self._draw_flat_or_live(
            self.metal_ax, readings, "Metal Detector Trend (live)", "Detected (0/1)",
            y_max=1.2, color=Theme.DANGER, fill=False,
        )
        self.canvas.draw_idle()


class LLMCard(ctk.CTkFrame):
    """Live LLM Card — status badge plus a scrolling decision reasoning
    log fed by the plant engine. UNCHANGED aside from the PANEL_BG fix."""

    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", padx=18, pady=(16, 6))
        ctk.CTkLabel(header_row, text="Live LLM Decision Engine", font=Theme.CARD_HEADER_FONT,
                     text_color=Theme.NAVY).pack(side="left")

        self.status_badge = ctk.CTkLabel(self, text="…", font=Theme.BADGE_FONT,
                                          text_color=Theme.OFF_GREY, anchor="w")
        self.status_badge.pack(fill="x", padx=18, pady=(0, 10))

        ctk.CTkLabel(self, text="Decision Reasoning Log", font=Theme.SMALL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="w", padx=18)

        self.log_box = ctk.CTkTextbox(
            self, height=180, corner_radius=8, fg_color=Theme.PANEL_BG,
            font=Theme.MONO_FONT, text_color=Theme.SLATE_TEXT, wrap="word"
        )
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        self.log_box.configure(state="disabled")
        self._line_count = 0

    def set_status(self, active: bool, text: str):
        self.status_badge.configure(
            text=("🟢 " if active else "🟠 ") + text,
            text_color=Theme.PRIMARY if active else Theme.WARN_TEXT,
        )

    def append_reasoning(self, line: str):
        if not line:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line.strip() + "\n")
        self._line_count += 1
        if self._line_count > REASONING_LOG_MAX_LINES:
            self.log_box.delete("1.0", "2.0")
            self._line_count -= 1
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


class HardwareStatusCard(ctk.CTkFrame):
    """ESP32 Plant Status panel — connection state, live numeric sensor
    readouts (Moisture / IR / Metal), and relay indicators. All values
    stay strictly at 0 / Idle / Off until real ESP32 data is received."""

    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)

        ctk.CTkLabel(self, text="ESP32 Plant Status", font=Theme.CARD_HEADER_FONT,
                     text_color=Theme.NAVY).pack(anchor="w", padx=18, pady=(16, 6))

        self.connection_label = ctk.CTkLabel(
            self, text=DISCONNECTED_STATUS_TEXT,
            font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT, anchor="w", wraplength=280
        )
        self.connection_label.pack(fill="x", padx=18, pady=(0, 12))

        # -- Live sensor readouts ------------------------------------
        ctk.CTkLabel(self, text="Live Sensor Readings", font=Theme.SMALL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="w", padx=18)

        sensor_row = ctk.CTkFrame(self, fg_color="transparent")
        sensor_row.pack(fill="x", padx=18, pady=(6, 12))
        sensor_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="sensor")

        self.moisture_readout = self._build_sensor_tile(sensor_row, "Moisture", "0.0%")
        self.moisture_readout.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        self.ir_readout = self._build_sensor_tile(sensor_row, "IR Sensor", "Idle")
        self.ir_readout.grid(row=0, column=1, sticky="nsew", padx=4)

        self.metal_readout = self._build_sensor_tile(sensor_row, "Metal Detector", "Idle")
        self.metal_readout.grid(row=0, column=2, sticky="nsew", padx=(4, 0))

        # -- Relay indicators ------------------------------------------
        ctk.CTkLabel(self, text="Relay / Machine Status", font=Theme.SMALL_FONT,
                     text_color=Theme.MUTED_TEXT).pack(anchor="w", padx=18)

        self.rows_container = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_container.pack(fill="both", expand=True, padx=18, pady=(6, 16))

        self.indicator_dots: Dict[str, ctk.CTkLabel] = {}
        for key, label in RELAY_INDICATORS:
            row = ctk.CTkFrame(self.rows_container, fg_color=Theme.PANEL_BG, corner_radius=8)
            row.pack(fill="x", pady=3)
            dot = ctk.CTkLabel(row, text="●", font=(Theme.FONT_FAMILY, 14), text_color=Theme.OFF_GREY, width=20)
            dot.pack(side="left", padx=(10, 6), pady=8)
            ctk.CTkLabel(row, text=label, font=Theme.ROW_FONT,
                         text_color=Theme.SLATE_TEXT).pack(side="left", pady=8)
            self.indicator_dots[key] = dot

    def _build_sensor_tile(self, master, label: str, initial_value: str) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(master, fg_color=Theme.PANEL_BG, corner_radius=8)
        ctk.CTkLabel(tile, text=label.upper(), font=(Theme.FONT_FAMILY, 10, "bold"),
                     text_color=Theme.MUTED_TEXT).pack(anchor="w", padx=10, pady=(8, 0))
        value_label = ctk.CTkLabel(tile, text=initial_value, font=(Theme.FONT_FAMILY, 15, "bold"),
                                    text_color=Theme.OFF_GREY)
        value_label.pack(anchor="w", padx=10, pady=(0, 8))
        tile.value_label = value_label  # stash for update_sensor_readings()
        return tile

    def update_connection(self, connected: bool, text: str):
        self.connection_label.configure(
            text=text, text_color=Theme.PRIMARY if connected else Theme.MUTED_TEXT
        )

    def update_sensor_readings(self, connected: bool, snapshot: Dict[str, object]):
        """Renders the live numeric readouts. When disconnected, this
        always shows 0.0% / Idle / Idle — it never displays a stale or
        invented value."""
        if not connected:
            self.moisture_readout.value_label.configure(text="0.0%", text_color=Theme.OFF_GREY)
            self.ir_readout.value_label.configure(text="Idle", text_color=Theme.OFF_GREY)
            self.metal_readout.value_label.configure(text="Idle", text_color=Theme.OFF_GREY)
            return

        moisture = snapshot.get("moisture_level")
        moisture_text = f"{moisture:.1f}%" if isinstance(moisture, (int, float)) else "0.0%"
        self.moisture_readout.value_label.configure(text=moisture_text, text_color=Theme.ACCENT)

        ir_active = bool(snapshot.get("ir_anomaly", False))
        self.ir_readout.value_label.configure(
            text="Anomaly" if ir_active else "Clear",
            text_color=Theme.WARN_TEXT if ir_active else Theme.PRIMARY,
        )

        metal_active = bool(snapshot.get("metal_detected", False))
        self.metal_readout.value_label.configure(
            text="Detected" if metal_active else "Clear",
            text_color=Theme.DANGER if metal_active else Theme.PRIMARY,
        )

    def update_relays(self, states: Dict[str, bool]):
        for key, dot in self.indicator_dots.items():
            active = bool(states.get(key, False))
            is_estop = key == "bin_biohazard_estop"
            if active and is_estop:
                dot.configure(text_color=Theme.DANGER)
            elif active:
                dot.configure(text_color=Theme.PRIMARY)
            else:
                dot.configure(text_color=Theme.OFF_GREY)


class LogTable(ctk.CTkFrame):
    """Widescreen ttk.Treeview table listing every scan record, with a
    clickable Google Maps pin column for exact pinpoint navigation.
    UNCHANGED."""

    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
                          border_width=1, border_color=Theme.BORDER)

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(header_row, text="Field Scan Log", font=Theme.CARD_HEADER_FONT,
                     text_color=Theme.NAVY).pack(side="left")
        self.live_indicator = ctk.CTkLabel(
            header_row, text="● Live", font=Theme.SMALL_FONT, text_color=Theme.PRIMARY
        )
        self.live_indicator.pack(side="right")

        hint_row = ctk.CTkFrame(self, fg_color="transparent")
        hint_row.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkLabel(
            hint_row, text="Tip: double-click a row's Google Maps Pin cell to open the exact "
                          "location in your browser.",
            font=Theme.SMALL_FONT, text_color=Theme.MUTED_TEXT
        ).pack(anchor="w")

        self._configure_style()

        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.tree = ttk.Treeview(
            table_frame, columns=TABLE_COLUMNS, show="headings",
            style="WastePulse.Treeview", height=16
        )
        for col in TABLE_COLUMNS:
            self.tree.heading(col, text=TABLE_HEADINGS[col])
            self.tree.column(col, anchor="w", width=180, stretch=True)

        v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=v_scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")

        self.tree.tag_configure("hazardous", background=Theme.HAZARD_BG,
                                 foreground=Theme.HAZARD_TEXT)

        self.tree.bind("<Double-1>", self._handle_double_click)
        self._maps_link_column_index = TABLE_COLUMNS.index("maps_link")

    def _configure_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "WastePulse.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
            foreground=Theme.SLATE_TEXT, rowheight=32, font=(Theme.FONT_FAMILY, 11),
            borderwidth=0
        )
        style.configure(
            "WastePulse.Treeview.Heading", background=Theme.NAVY, foreground="#FFFFFF",
            font=(Theme.FONT_FAMILY, 11, "bold"), relief="flat"
        )
        style.map("WastePulse.Treeview.Heading", background=[("active", Theme.NAVY)])
        style.map("WastePulse.Treeview", background=[("selected", "#DCFCE7")],
                  foreground=[("selected", Theme.NAVY)])

    def _handle_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return

        try:
            col_index = int(col_id.replace("#", "")) - 1
        except ValueError:
            return

        if col_index != self._maps_link_column_index:
            return

        link = self.tree.set(row_id, "maps_link")
        if link:
            webbrowser.open(link)
        else:
            messagebox.showinfo("No Coordinates", "This scan has no precise coordinates on file.")

    def populate(self, logs: List[Dict]):
        self.tree.delete(*self.tree.get_children())
        for entry in reversed(logs):  # most recent first
            bucket = CATEGORY_BUCKET_BY_LABEL.get(entry.get("category", ""), "")
            tags = ("hazardous",) if bucket == "Hazardous" else ()
            self.tree.insert("", "end", values=(
                entry.get("timestamp", ""),
                entry.get("user", ""),
                entry.get("location", ""),
                resolve_maps_link(entry),
                entry.get("category", ""),
                entry.get("confidence", ""),
                entry.get("status", ""),
            ), tags=tags)


# ---------------------------------------------------------------------------
# 7. MAIN APPLICATION
# ---------------------------------------------------------------------------

class SPCBDashboardApp(ctk.CTk):
    """Full laptop-widescreen central monitoring dashboard for SPCB Admins.

    All original behavior (HeaderBar, KPI cards, LogTable, waste_logs.json
    auto-refresh) is preserved. An `engine` argument binds the live
    Analytics / LLM / Hardware panels to a real hardware_integration.py
    PlantEngine instance; omit it to run standalone against the safe
    zero-state stub.

    An optional `on_sign_out` argument lets an external launcher
    override what happens after Sign Out (e.g. a future login screen).
    If not supplied, Sign Out falls back to authentycation.py's login
    screen, or a plain notice if that module isn't present.

    NEW: a floating "Soil Risk Predictor" button opens
    ai_soil_predictor.py's SoilRiskPanel in its own Toplevel window.
    This is purely additive on top of all existing behavior above."""

    def __init__(self, username: str = "SPCB Admin", engine: Optional[object] = None,
                 on_sign_out: Optional[object] = None):
        super().__init__()
        self.username = username
        self.engine = resolve_engine(engine)
        self._external_on_sign_out = on_sign_out

        self._auto_refresh_job = None
        self._engine_poll_job = None
        self._last_log_count = -1

        # NEW: tracks the currently-open Soil Risk Predictor Toplevel
        # (if any), so the launcher button can re-focus an existing
        # window instead of spawning duplicates.
        self._soil_predictor_window = None

        self.title("WastePulse — SPCB Central Monitoring Dashboard")
        maximize_window(self)
        self.configure(fg_color=Theme.BACKGROUND)

        self._build_layout()
        self.refresh_data()
        self._schedule_auto_refresh()

        self.poll_engine()
        self._schedule_engine_poll()

        self.protocol("WM_DELETE_WINDOW", self._handle_close)

    def _build_layout(self):
        HeaderBar(
            self, self.username, on_refresh=self.refresh_data,
            on_sign_out=self._handle_sign_out
        ).pack(fill="x", side="top")

        # NEW: floating launcher button for the Soil & Landslide Risk
        # Predictor. Uses .place() on the root window so it sits on top
        # of the header without altering HeaderBar's internal pack()
        # layout in any way — nothing above this line is touched.
        ctk.CTkButton(
            self, text="🌱 Soil Risk Predictor", font=Theme.ROW_FONT, height=34,
            width=190, corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.ACCENT,
            hover_color=Theme.ACCENT_HOVER, text_color="#FFFFFF",
            command=self._handle_open_soil_predictor
        ).place(relx=1.0, y=8, x=-360, anchor="ne")

        scroll = ctk.CTkScrollableFrame(self, fg_color=Theme.BACKGROUND)
        scroll.pack(fill="both", expand=True, padx=28, pady=24)
        scroll.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")

        # -- Row 0: live status banner ---------------------------------
        self.status_banner = StatusBanner(scroll)
        self.status_banner.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 16))

        # -- Row 1: 4 KPI cards (unchanged) ----------------------------
        self.kpi_total = KPICard(scroll, "Total Scans Logged", accent=Theme.NAVY)
        self.kpi_biodeg = KPICard(scroll, "% Biodegradable", accent=Theme.PRIMARY)
        self.kpi_non_biodeg = KPICard(scroll, "% Non-Biodegradable", accent="#0284C7")
        self.kpi_hazard = KPICard(scroll, "Hazardous Flags", accent=Theme.DANGER)

        for i, card in enumerate((self.kpi_total, self.kpi_biodeg, self.kpi_non_biodeg, self.kpi_hazard)):
            card.grid(row=1, column=i, sticky="nsew", padx=8, pady=(0, 20))

        # -- Row 2: Analytics (left, spans 2 cols) + LLM/Hardware (right) --
        self.analytics_card = AnalyticsCard(scroll)
        self.analytics_card.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(0, 8), pady=(0, 20))

        side_panel = ctk.CTkFrame(scroll, fg_color="transparent")
        side_panel.grid(row=2, column=2, columnspan=2, sticky="nsew", padx=(8, 0), pady=(0, 20))
        side_panel.grid_columnconfigure((0, 1), weight=1, uniform="side")

        self.llm_card = LLMCard(side_panel)
        self.llm_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.hardware_card = HardwareStatusCard(side_panel)
        self.hardware_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # -- Row 3: full-width log table (unchanged) -------------------
        self.log_table = LogTable(scroll)
        self.log_table.grid(row=3, column=0, columnspan=4, sticky="nsew")

    # -- waste_logs.json refresh (unchanged behavior) ----------------------

    def refresh_data(self):
        logs = load_waste_logs()
        kpis = compute_kpis(logs)

        self.kpi_total.set_value(str(kpis["total"]))
        self.kpi_biodeg.set_value(f"{kpis['pct_biodegradable']}%")
        self.kpi_non_biodeg.set_value(f"{kpis['pct_non_biodegradable']}%")
        self.kpi_hazard.set_value(str(kpis["hazardous_count"]))

        self.log_table.populate(logs)
        self.analytics_card.update_category_chart(compute_category_distribution(logs))
        self._last_log_count = len(logs)

    def _schedule_auto_refresh(self):
        self._auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        logs = load_waste_logs()
        if len(logs) != self._last_log_count:
            self.refresh_data()
        self._schedule_auto_refresh()

    # -- plant engine live polling ------------------------------------

    def poll_engine(self):
        """Pulls the latest hardware/LLM/telemetry snapshot from the
        engine's thread-safe getters and pushes it into the panels.
        Runs entirely on the Tkinter main thread's .after() timer — the
        engine itself does all the real work on its own background
        thread, so this call never blocks. Every value shown here comes
        straight from PlantEngine's getters — nothing is generated here."""
        hw_connected = self.engine.is_hardware_connected()
        hw_text = self.engine.get_hardware_status_text()
        llm_text = self.engine.get_llm_status_text()
        llm_active = "Fallback" not in llm_text

        self.status_banner.update_status(
            hardware_connected=hw_connected, hardware_text=hw_text,
            llm_active=llm_active, llm_text=llm_text,
        )

        self.hardware_card.update_connection(hw_connected, hw_text)
        self.hardware_card.update_sensor_readings(hw_connected, self.engine.get_latest_sensor_snapshot())
        self.hardware_card.update_relays(self.engine.get_relay_states())

        self.llm_card.set_status(active=llm_active, text=llm_text)
        latest_reasoning = self.engine.get_latest_reasoning()
        if latest_reasoning:
            self.llm_card.append_reasoning(latest_reasoning)

        self.analytics_card.update_moisture_chart(
            self.engine.get_recent_moisture_readings(SENSOR_HISTORY_LEN)
        )
        self.analytics_card.update_ir_chart(
            self.engine.get_recent_ir_readings(SENSOR_HISTORY_LEN)
        )
        self.analytics_card.update_metal_chart(
            self.engine.get_recent_metal_readings(SENSOR_HISTORY_LEN)
        )

    def _schedule_engine_poll(self):
        self._engine_poll_job = self.after(ENGINE_POLL_MS, self._engine_poll_tick)

    def _engine_poll_tick(self):
        self.poll_engine()
        self._schedule_engine_poll()

    def _handle_close(self):
        if self._auto_refresh_job is not None:
            self.after_cancel(self._auto_refresh_job)
        if self._engine_poll_job is not None:
            self.after_cancel(self._engine_poll_job)
        self.destroy()

    # -- Sign Out -------------------------------------------------

    def _handle_sign_out(self):
        if self._auto_refresh_job is not None:
            self.after_cancel(self._auto_refresh_job)
            self._auto_refresh_job = None
        if self._engine_poll_job is not None:
            self.after_cancel(self._engine_poll_job)
            self._engine_poll_job = None

        self.destroy()

        if self._external_on_sign_out is not None:
            self._external_on_sign_out()
        elif start_login_session is not None:
            start_login_session()
        else:
            messagebox.showinfo(
                "Signed Out",
                "Signed out of the SPCB Dashboard. authentycation.py was not "
                "found, so the login screen could not be relaunched."
            )

    # -- NEW: Soil Risk Predictor launcher -----------------------------

    def _handle_open_soil_predictor(self):
        """Opens ai_soil_predictor.py's SoilRiskPanel in its own
        Toplevel window. Entirely additive: does not touch the main
        content area, the scrollable frame, or any existing widget."""
        if SoilRiskPanel is None:
            messagebox.showinfo(
                "Module Not Found",
                "ai_soil_predictor.py was not found (or customtkinter is "
                "unavailable), so the Soil Risk Predictor panel could not "
                "be loaded."
            )
            return

        # Re-focus an already-open window instead of creating a duplicate.
        if self._soil_predictor_window is not None:
            try:
                self._soil_predictor_window.lift()
                self._soil_predictor_window.focus()
                return
            except Exception:
                self._soil_predictor_window = None  # window was closed; recreate below

        top = ctk.CTkToplevel(self)
        top.title("AI Soil & Landslide Risk Predictor — SIH26001")
        top.geometry("680x780")
        top.configure(fg_color=Theme.BACKGROUND)

        SoilRiskPanel(top, username=self.username).pack(fill="both", expand=True)

        def _on_close():
            self._soil_predictor_window = None
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", _on_close)
        self._soil_predictor_window = top


# ---------------------------------------------------------------------------
# 8. ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    app = SPCBDashboardApp()
    app.mainloop()


if __name__ == "__main__":
    main()