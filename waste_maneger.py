"""
waste_manager.py
-----------------
WastePulse — Public Field Capture Unit
"Smart Sensing, Smarter Living"

PYINSTALLER PATCH: BASE_DIR now honors the WASTEPULSE_BASE_DIR
environment variable (set by main.py) before falling back to __file__,
so waste_logs.json, the captures/ folder, and the logo asset resolve
correctly next to the real .exe even if this module is ever imported
inside a frozen build instead of run as a plain script. This is a
no-op when the env var isn't set (i.e. running normally) — everything
else in this file is untouched.
"""

import os
import json
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import requests
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image

try:
    from authentycation import start_login_session
except ImportError:
    start_login_session = None

# ---------------------------------------------------------------------------
# 1. THEME — WastePulse brand identity (Light Mode)
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
    DANGER_HOVER = "#8f1717"
    SUCCESS = "#15803D"
    PANEL_BG = "#F1F5F9"
    ACCENT = "#0284C7"
    ACCENT_HOVER = "#0369A1"

    FONT_FAMILY = "Segoe UI"
    TITLE_FONT = (FONT_FAMILY, 24, "bold")
    MOTTO_FONT = (FONT_FAMILY, 12, "bold")
    SUBTEXT_FONT = (FONT_FAMILY, 11)
    BUTTON_FONT = (FONT_FAMILY, 17, "bold")
    CARD_HEADER_FONT = (FONT_FAMILY, 15, "bold")
    ROW_FONT = (FONT_FAMILY, 13)
    SMALL_FONT = (FONT_FAMILY, 11)
    LABEL_FONT = (FONT_FAMILY, 12, "bold")

    CORNER_RADIUS = 12

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

# ---------------------------------------------------------------------------
# 2. CONSTANTS
# ---------------------------------------------------------------------------
# PYINSTALLER PATCH: prefer the WASTEPULSE_BASE_DIR environment variable
# (exported by main.py, which resolves it via sys.executable when frozen)
# over a plain __file__-based path. Falls back to the original behavior
# untouched when the env var isn't set, so standalone `python waste_maneger.py`
# runs exactly as before.
BASE_DIR = os.environ.get("WASTEPULSE_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
LOGO_PATH_CANDIDATES = tuple(
    os.path.join(BASE_DIR, name) for name in ("logo.png", "logo.jpg", "logo.jpeg")
)

WASTE_LOGS_PATH = os.path.join(BASE_DIR, "waste_logs.json")
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")
CAMERA_PREVIEW_SIZE = (880, 480)

IP_GEOLOCATION_URL = "http://ip-api.com/json/"

WASTE_CATEGORIES = {
    "dry": "Dry Waste (सूखा कचरा)",
    "wet": "Wet Waste (गीला कचरा)",
    "industrial": "Industrial Waste (इंडस्ट्रियल कचरा)",
    "biomedical": "Bio-Medical Waste (बायो-केमिकल कचरा)",
}

CATEGORY_LOG_LABELS = {
    "dry": "Dry Waste",
    "wet": "Wet Waste",
    "industrial": "Industrial Waste",
    "biomedical": "Bio-Medical Waste",
}

MAX_CATEGORY_COUNT = 12
STATUS_SENT = "Sent to SPCB"

# ---------------------------------------------------------------------------
# 3. WINDOW SIZING HELPER
# ---------------------------------------------------------------------------
def maximize_window(window: ctk.CTk, fallback_size: str = "1400x850") -> None:
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
# 4. DATA MODEL
# ---------------------------------------------------------------------------
@dataclass
class CapturePayload:
    frame_path: str
    timestamp: str
    location_text: str
    raw_location: dict
    maps_link: str = ""
    category_counts: Dict[str, int] = field(default_factory=dict)
    dominant_category: Optional[str] = None
    confidence: float = 0.0

    def to_log_entry(self, user: str) -> dict:
        category_label = (
            CATEGORY_LOG_LABELS.get(self.dominant_category, "Unclassified")
            if self.dominant_category else "Unclassified"
        )
        return {
            "timestamp": self.timestamp,
            "user": user,
            "location": self.location_text,
            "lat": self.raw_location.get("lat"),
            "lon": self.raw_location.get("lon"),
            "maps_link": self.maps_link,
            "category": category_label,
            "confidence": f"{self.confidence:.1f}%",
            "status": STATUS_SENT,
        }

# ---------------------------------------------------------------------------
# 5. LOCATION
# ---------------------------------------------------------------------------
def fetch_dynamic_location() -> dict:
    try:
        response = requests.get(IP_GEOLOCATION_URL, timeout=6)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "success":
            return {
                "city": data.get("city", ""),
                "region": data.get("regionName", ""),
                "country": data.get("country", ""),
                "lat": data.get("lat"),
                "lng": data.get("lon"),
                "ip": data.get("query", ""),
                "source": "live-ip-geolocation",
                "ok": True,
            }
    except Exception:
        pass
    return {"city": "", "region": "", "country": "", "lat": None, "lng": None, "ip": "", "source": "unavailable", "ok": False}

def build_maps_link(lat: Optional[float], lng: Optional[float]) -> str:
    if lat is None or lng is None:
        return ""
    return f"https://www.google.com/maps?q={lat},{lng}"

def format_location(location: dict) -> str:
    if not location.get("ok"):
        return "Location unavailable — please enter manually"
    place = ", ".join(part for part in (location.get("city", ""), location.get("region", ""), location.get("country", "")) if part)
    lat, lng = location.get("lat"), location.get("lng")
    if lat is not None and lng is not None:
        return f"{place} ({lat:.6f}, {lng:.6f})"
    return place or "Location unavailable — please enter manually"

# ---------------------------------------------------------------------------
# 6. FRAME STAMPING
# ---------------------------------------------------------------------------
def stamp_frame(frame, timestamp: str, location_text: str):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 58), (w, h), (15, 37, 55), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    cv2.putText(frame, f"Timestamp: {timestamp}", (14, h - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Location: {location_text}", (14, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return frame

# ---------------------------------------------------------------------------
# 7. RULE-BASED VALIDATION GUARDRAIL (GATEKEEPER - SKIN TONE & TEXTURE)
# ---------------------------------------------------------------------------
MIN_TEXTURE_STD = 15.0
MIN_EDGE_DENSITY = 0.005
MIN_BRIGHTNESS = 15.0
MAX_BRIGHTNESS = 245.0

MSG_NO_FRAME = "Invalid Object — No Image Data Received."
MSG_FACE_DETECTED = "Face/Skin Detected — Please Scan a Valid Waste Item Only."
MSG_BLANK_FRAME = "Invalid Object — Image is Blank, a Wall, or Too Uniform."
MSG_BAD_LIGHTING = "Invalid Object — Unusable Lighting Conditions."

def validate_capture(frame) -> Tuple[bool, str]:
    if frame is None or getattr(frame, "size", 0) == 0:
        return False, MSG_NO_FRAME

    # त्वचा का रंग (Skin Tone Detection) ताकि चेहरा या शरीर आते ही ब्लॉक हो जाए
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    lower_skin = np.array([0, 133, 77], dtype=np.uint8)
    upper_skin = np.array([255, 173, 127], dtype=np.uint8)
    skin_mask = cv2.inRange(ycrcb, lower_skin, upper_skin)
    skin_ratio = np.sum(skin_mask > 0) / (frame.shape[0] * frame.shape[1])
    
    if skin_ratio > 0.15:
        return False, MSG_FACE_DETECTED

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    brightness = float(np.mean(gray))
    if brightness < MIN_BRIGHTNESS or brightness > MAX_BRIGHTNESS:
        return False, MSG_BAD_LIGHTING

    texture = float(np.std(gray))
    if texture < MIN_TEXTURE_STD:
        return False, MSG_BLANK_FRAME
        
    edges = cv2.Canny(gray, 50, 150)
    edge_density = np.sum(edges) / 255.0 / (gray.shape[0] * gray.shape[1])
    if edge_density < MIN_EDGE_DENSITY:
        return False, MSG_BLANK_FRAME

    return True, ""

# ---------------------------------------------------------------------------
# 8. WASTE CLASSIFICATION (Strict Object/Contour Check)
# ---------------------------------------------------------------------------
def estimate_waste_categories(frame) -> Dict[str, int]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # अगर फ्रेम में ठोस ऑब्जेक्ट्स या कचरे के किनारे नहीं मिले, तो सब 0 रखो
    valid_contours = [c for c in contours if cv2.contourArea(c) > 800]
    if len(valid_contours) == 0:
        return {"dry": 0, "wet": 0, "industrial": 0, "biomedical": 0}

    item_count = min(len(valid_contours), MAX_CATEGORY_COUNT)
    avg_b, avg_g, avg_r = [float(c) for c in cv2.mean(frame)[:3]]
    texture = float(np.std(gray))
    brightness = (avg_r + avg_g + avg_b) / 3.0
    channel_spread = max(avg_r, avg_g, avg_b) - min(avg_r, avg_g, avg_b)

    counts = {"dry": 0, "wet": 0, "industrial": 0, "biomedical": 0}

    if brightness > 175 and channel_spread < 18:
        counts["biomedical"] = item_count
    elif avg_g >= avg_r and avg_g >= avg_b and texture < 40:
        counts["wet"] = item_count
    elif brightness > 140 and texture > 45:
        counts["industrial"] = item_count
    else:
        counts["dry"] = item_count

    return counts

def determine_dominant_category(counts: Dict[str, int]) -> Tuple[Optional[str], float]:
    total = sum(counts.values())
    if total == 0:
        return None, 0.0
    dominant_key = max(counts, key=counts.get)
    raw_ratio = counts[dominant_key] / total
    confidence = round(min(99.0, max(55.0, raw_ratio * 100)), 1)
    return dominant_key, confidence

# ---------------------------------------------------------------------------
# 9. JSON LOGGING
# ---------------------------------------------------------------------------
def load_waste_logs() -> list:
    if not os.path.exists(WASTE_LOGS_PATH):
        return []
    try:
        with open(WASTE_LOGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def append_waste_log(entry: dict) -> None:
    logs = load_waste_logs()
    logs.append(entry)
    with open(WASTE_LOGS_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)

# ---------------------------------------------------------------------------
# 10. UI COMPONENTS
# ---------------------------------------------------------------------------
class Sidebar(ctk.CTkFrame):
    def __init__(self, master, username: str, on_exit, on_sign_out=None):
        super().__init__(master, fg_color=Theme.NAVY, corner_radius=0, width=260)
        self.pack_propagate(False)
        self._build_logo().pack(pady=(30, 12))
        ctk.CTkLabel(self, text="WastePulse", font=Theme.TITLE_FONT, text_color="#FFFFFF").pack()
        ctk.CTkLabel(self, text="Smart Sensing, Smarter Living", font=Theme.MOTTO_FONT, text_color="#86EFAC").pack(pady=(4, 2))
        ctk.CTkLabel(self, text="Public Field Capture Unit", font=Theme.SUBTEXT_FONT, text_color="#CBD5E1").pack(pady=(0, 30))
        ctk.CTkFrame(self, height=1, fg_color="#1F3B52").pack(fill="x", padx=24, pady=(0, 20))
        ctk.CTkLabel(self, text="LOGGED IN AS", font=Theme.SMALL_FONT, text_color="#94A3B8").pack(padx=24, anchor="w")
        ctk.CTkLabel(self, text=username, font=Theme.CARD_HEADER_FONT, text_color="#FFFFFF", wraplength=210, justify="left").pack(padx=24, anchor="w", pady=(2, 30))
        ctk.CTkButton(self, text="Exit Application", font=Theme.ROW_FONT, height=42, corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.DANGER, hover_color=Theme.DANGER_HOVER, text_color="#FFFFFF", command=on_exit).pack(side="bottom", fill="x", padx=20, pady=24)
        ctk.CTkButton(self, text="⏻ Sign Out", font=Theme.ROW_FONT, height=42, corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.ACCENT, hover_color=Theme.ACCENT_HOVER, text_color="#FFFFFF", command=on_sign_out).pack(side="bottom", fill="x", padx=20, pady=(0, 10))

    def _build_logo(self):
        for candidate in LOGO_PATH_CANDIDATES:
            if os.path.exists(candidate):
                try:
                    pil_image = Image.open(candidate).convert("RGBA")
                    pil_image.thumbnail((64, 64), Image.LANCZOS)
                    canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                    offset = ((64 - pil_image.width) // 2, (64 - pil_image.height) // 2)
                    canvas.paste(pil_image, offset, pil_image)
                    return ctk.CTkLabel(self, image=ctk.CTkImage(light_image=canvas, size=(64, 64)), text="")
                except Exception:
                    continue
        badge = ctk.CTkFrame(self, width=64, height=64, corner_radius=18, fg_color=Theme.PRIMARY)
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="WP", font=(Theme.FONT_FAMILY, 20, "bold"), text_color="#FFFFFF").pack(expand=True)
        return badge

class LocationCard(ctk.CTkFrame):
    def __init__(self, master, on_refresh, on_open_maps):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS, border_width=1, border_color=Theme.BORDER)
        self.on_refresh = on_refresh
        self.on_open_maps = on_open_maps
        ctk.CTkLabel(self, text="Field Location", font=Theme.CARD_HEADER_FONT, text_color=Theme.NAVY).pack(anchor="w", padx=18, pady=(16, 8))
        
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 6))
        row.grid_columnconfigure(0, weight=1)
        
        self.location_var = ctk.StringVar(value="Fetching current location…")
        self.location_entry = ctk.CTkEntry(row, textvariable=self.location_var, height=40, corner_radius=8, font=Theme.ROW_FONT, border_color=Theme.BORDER)
        self.location_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        ctk.CTkButton(row, text="⟳ Refresh Location", font=Theme.SMALL_FONT, height=40, width=160, corner_radius=8, fg_color=Theme.ACCENT, hover_color=Theme.ACCENT_HOVER, text_color="#FFFFFF", command=self.on_refresh).grid(row=0, column=1, padx=(0, 10))
        self.maps_btn = ctk.CTkButton(row, text="📍 Open in Maps", font=Theme.SMALL_FONT, height=40, width=150, corner_radius=8, fg_color=Theme.NAVY, hover_color="#16344a", text_color="#FFFFFF", command=self.on_open_maps, state="disabled")
        self.maps_btn.grid(row=0, column=2)
        
        self.maps_link_label = ctk.CTkLabel(self, text="Google Maps pin: unavailable", font=Theme.SMALL_FONT, text_color=Theme.MUTED_TEXT, anchor="w")
        self.maps_link_label.pack(anchor="w", padx=18, pady=(0, 16))

    def set_location_text(self, text: str):
        self.location_var.set(text)

    def get_location_text(self) -> str:
        return self.location_entry.get().strip()

    def set_maps_link(self, link: str):
        if link:
            self.maps_link_label.configure(text=f"Google Maps pin: {link}")
            self.maps_btn.configure(state="normal")
        else:
            self.maps_link_label.configure(text="Google Maps pin: unavailable")
            self.maps_btn.configure(state="disabled")

class ResultsCard(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS, border_width=1, border_color=Theme.BORDER)
        ctk.CTkLabel(self, text="Estimated Waste Breakdown", font=Theme.CARD_HEADER_FONT, text_color=Theme.NAVY).pack(anchor="w", padx=18, pady=(16, 8))
        self.rows_container = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_container.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.clear()

    def clear(self):
        for child in self.rows_container.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.rows_container, text="No capture yet. Take a live photo.", font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT).pack(anchor="w", pady=6)

    def update_results(self, category_counts: Dict[str, int]):
        for child in self.rows_container.winfo_children():
            child.destroy()
        visible_rows = {k: v for k, v in category_counts.items() if v > 0}
        if not visible_rows:
            ctk.CTkLabel(self.rows_container, text="No waste categories detected.", font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT).pack(anchor="w", pady=6)
            return
        for key, count in visible_rows.items():
            row = ctk.CTkFrame(self.rows_container, fg_color=Theme.PANEL_BG, corner_radius=8)
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(row, text=WASTE_CATEGORIES.get(key, key.title()), font=Theme.ROW_FONT, text_color=Theme.SLATE_TEXT).pack(side="left", padx=14, pady=10)
            ctk.CTkLabel(row, text=f"× {count}", font=Theme.CARD_HEADER_FONT, text_color=Theme.PRIMARY).pack(side="right", padx=14, pady=10)

class MetadataCard(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS, border_width=1, border_color=Theme.BORDER)
        ctk.CTkLabel(self, text="Capture Metadata", font=Theme.CARD_HEADER_FONT, text_color=Theme.NAVY).pack(anchor="w", padx=18, pady=(16, 6))
        self.value_label = ctk.CTkLabel(self, text="Awaiting capture…", font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT, justify="left")
        self.value_label.pack(anchor="w", padx=18, pady=(0, 16))

    def clear(self, message: str = "Awaiting capture…", color: str = Theme.MUTED_TEXT):
        self.value_label.configure(text=message, text_color=color)

    def update_metadata(self, payload: CapturePayload):
        category_text = CATEGORY_LOG_LABELS.get(payload.dominant_category, "Unclassified") if payload.dominant_category else "Unclassified"
        self.value_label.configure(
            text=(f"Timestamp:     {payload.timestamp}\n"
                  f"Dominant type: {category_text}  ({payload.confidence:.1f}% confidence)\n"
                  f"Saved file:    {os.path.basename(payload.frame_path)}"),
            text_color=Theme.SLATE_TEXT
        )

# ---------------------------------------------------------------------------
# 11. MAIN APPLICATION
# ---------------------------------------------------------------------------
class WasteClassifierApp(ctk.CTk):
    def __init__(self, username: str = "Public User"):
        super().__init__()
        self.username = username
        self.title("WastePulse — Public Field Capture Unit")
        maximize_window(self)
        self.configure(fg_color=Theme.BACKGROUND)
        os.makedirs(CAPTURE_DIR, exist_ok=True)

        self.last_payload: Optional[CapturePayload] = None
        self.current_frame = None
        self.video_capture = None
        self._preview_job = None
        self._last_raw_location: dict = {"ok": False}
        self._last_maps_link: str = ""

        self._build_layout()
        self._start_camera()
        self._refresh_location()
        self.protocol("WM_DELETE_WINDOW", self.handle_exit)

    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        Sidebar(self, self.username, on_exit=self.handle_exit, on_sign_out=self._handle_sign_out).grid(row=0, column=0, sticky="ns")
        main = ctk.CTkScrollableFrame(self, fg_color=Theme.BACKGROUND)
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        main.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(main, text="Live capture only — validations active.", font=Theme.SMALL_FONT, text_color=Theme.MUTED_TEXT, anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 12))

        preview_frame = ctk.CTkFrame(main, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS, border_width=1, border_color=Theme.BORDER)
        preview_frame.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.preview_label = ctk.CTkLabel(preview_frame, text="Initializing camera…", font=Theme.ROW_FONT, text_color=Theme.MUTED_TEXT, width=CAMERA_PREVIEW_SIZE[0], height=CAMERA_PREVIEW_SIZE[1])
        self.preview_label.pack(padx=16, pady=16)

        self.location_card = LocationCard(main, on_refresh=self._refresh_location, on_open_maps=self._open_maps_link)
        self.location_card.grid(row=2, column=0, sticky="ew", pady=(0, 16))

        self.capture_btn = ctk.CTkButton(main, text="📷  Capture Live Garbage Photo", font=Theme.BUTTON_FONT, fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER, text_color="#FFFFFF", corner_radius=Theme.CORNER_RADIUS, height=64, command=self.handle_capture)
        self.capture_btn.grid(row=3, column=0, sticky="ew", pady=(0, 14))

        self.status_label = ctk.CTkLabel(main, text="Status: Idle — ready to capture.", font=Theme.SMALL_FONT, text_color=Theme.MUTED_TEXT, anchor="w", wraplength=900, justify="left")
        self.status_label.grid(row=4, column=0, sticky="ew", pady=(0, 14))

        cards_row = ctk.CTkFrame(main, fg_color="transparent")
        cards_row.grid(row=5, column=0, sticky="ew", pady=(0, 16))
        cards_row.grid_columnconfigure((0, 1), weight=1, uniform="cards")

        self.metadata_card = MetadataCard(cards_row)
        self.metadata_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.results_card = ResultsCard(cards_row)
        self.results_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.submit_btn = ctk.CTkButton(main, text="Send to SPCB Dashboard", font=Theme.ROW_FONT, height=46, corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.NAVY, hover_color="#16344a", text_color="#FFFFFF", state="disabled", command=self.handle_submit)
        self.submit_btn.grid(row=6, column=0, sticky="ew")

    def _refresh_location(self):
        self.location_card.set_location_text("Fetching current location…")
        self.location_card.set_maps_link("")
        self.update_idletasks()
        loc_data = fetch_dynamic_location()
        self._last_raw_location = loc_data
        
        if loc_data["ok"]:
            link = build_maps_link(loc_data["lat"], loc_data["lng"])
            self._last_maps_link = link
            self.location_card.set_location_text(format_location(loc_data))
            self.location_card.set_maps_link(link)
        else:
            self._last_maps_link = ""
            self.location_card.set_location_text("Location unavailable — please enter manually")

    def _open_maps_link(self):
        import webbrowser
        if self._last_maps_link:
            webbrowser.open(self._last_maps_link)

    def _start_camera(self):
        self.video_capture = cv2.VideoCapture(0)
        self._update_preview()

    def _update_preview(self):
        if self.video_capture and self.video_capture.isOpened():
            ret, frame = self.video_capture.read()
            if ret:
                self.current_frame = frame

                # --- OpenCV Garbage Detection: 800px minimum contour area threshold ---
                display_frame = frame.copy()
                gray_detect = cv2.cvtColor(display_frame, cv2.COLOR_BGR2GRAY)
                blurred_detect = cv2.GaussianBlur(gray_detect, (5, 5), 0)
                edges_detect = cv2.Canny(blurred_detect, 50, 150)
                contours_detect, _ = cv2.findContours(edges_detect, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                sufficient_garbage_found = False
                for contour in contours_detect:
                    area = cv2.contourArea(contour)
                    if area < 800:
                        continue
                    sufficient_garbage_found = True
                    x, y, box_w, box_h = cv2.boundingRect(contour)
                    cv2.rectangle(display_frame, (x, y), (x + box_w, y + box_h), (21, 128, 61), 2)

                if sufficient_garbage_found:
                    self.capture_btn.configure(state="normal")
                    if self.status_label.cget("text") == "Not Sufficient Amount of Garbage":
                        self.status_label.configure(text="Status: Idle — ready to capture.", text_color=Theme.MUTED_TEXT)
                else:
                    self.capture_btn.configure(state="disabled")
                    self.status_label.configure(text="Not Sufficient Amount of Garbage", text_color=Theme.DANGER)
                # --- end OpenCV garbage detection block ---

                rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb_frame).resize(CAMERA_PREVIEW_SIZE, Image.LANCZOS)
                self.preview_label.configure(image=ctk.CTkImage(light_image=pil_img, size=CAMERA_PREVIEW_SIZE), text="")
        self._preview_job = self.after(30, self._update_preview)

    def handle_capture(self):
        if self.current_frame is None:
            messagebox.showerror("Error", "No camera feed available.")
            return
            
        frame_copy = self.current_frame.copy()
        is_valid, msg = validate_capture(frame_copy)
        
        if not is_valid:
            self.status_label.configure(text=f"Status: {msg}", text_color=Theme.DANGER)
            messagebox.showwarning("Invalid Capture", msg)
            return
            
        counts = estimate_waste_categories(frame_copy)
        dom_cat, conf = determine_dominant_category(counts)
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        loc_text = self.location_card.get_location_text()
        stamped_frame = stamp_frame(frame_copy, timestamp, loc_text)
        
        filename = f"capture_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(CAPTURE_DIR, filename)
        cv2.imwrite(filepath, stamped_frame)
        
        self.last_payload = CapturePayload(
            frame_path=filepath, timestamp=timestamp, location_text=loc_text,
            raw_location=self._last_raw_location, maps_link=self._last_maps_link,
            category_counts=counts, dominant_category=dom_cat, confidence=conf
        )
        
        self.results_card.update_results(counts)
        self.metadata_card.update_metadata(self.last_payload)
        self.submit_btn.configure(state="normal", fg_color=Theme.PRIMARY)
        self.status_label.configure(text="Status: Capture successful. Ready to send.", text_color=Theme.SUCCESS)

    def handle_submit(self):
        if not self.last_payload:
            return
        self.last_payload.location_text = self.location_card.get_location_text()
        append_waste_log(self.last_payload.to_log_entry(self.username))
        
        self.status_label.configure(text="Status: Successfully sent to SPCB Dashboard.", text_color=Theme.PRIMARY)
        self.submit_btn.configure(state="disabled", fg_color=Theme.NAVY)
        self.results_card.clear()
        self.metadata_card.clear("Sent successfully.", Theme.PRIMARY)
        self.last_payload = None
        messagebox.showinfo("Success", "Waste data securely logged.")

    def handle_exit(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        if self.video_capture:
            self.video_capture.release()
        self.destroy()

    def _handle_sign_out(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
            self._preview_job = None
        if self.video_capture:
            self.video_capture.release()
            self.video_capture = None

        self.destroy()

        if start_login_session is not None:
            start_login_session()

if __name__ == "__main__":
    app = WasteClassifierApp()
    app.mainloop()