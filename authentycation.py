"""
authentycation.py
------------------
WastePulse — Authentication Portal
"Smart Sensing, Smarter Living"

Full laptop-widescreen login/registration GUI built with customtkinter.
Persists user profiles to a local `users_db.json` file (salted
PBKDF2-HMAC-SHA256 password hashes only — never plaintext).

SPCB Staff registration requires a Master Administrative Password and a
sub-role selection:

    Senior Official (Admin) -> auto-generated Unique System ID "SAS-####"
                                 -> resolved login role: "admin"
                                    (Full access including LLM)
    Field Worker             -> auto-generated Unique System ID "SAW-####"
                                 -> resolved login role: "field_worker"
                                    (Location & Google Maps access only)

ROLE-BASED VIEW SWITCHING (in-process, no subprocess for SPCB Staff):
    On successful login, this file resolves the user's role from their
    Unique System ID prefix and opens a `DashboardContainer` frame
    *inside this same window*, replacing the login/register card:

        'SAS-' (Senior Official / Admin) -> the FULL dashboard view:
            StatusBanner, KPI cards, AnalyticsCard, LLMCard,
            HardwareStatusCard, and the full LogTable.

        'SAW-' (Field Worker) -> ONLY the Google Maps / location
            tracking view — dashboard.py's LogTable component (the
            widget that exposes the "Google Maps Pin (double-click to
            open)" column). No LLM card, no telemetry/hardware panel,
            and no admin analytics panel are ever instantiated for
            this role.

    IMPORTANT: `dashboard.py` is never modified. Its `SPCBDashboardApp`
    class is itself a `ctk.CTk` root window and therefore cannot be
    embedded as a child frame — Tkinter does not support nesting one
    root window inside another. Instead, this file imports and reuses
    dashboard.py's individual CTkFrame components and pure data
    functions directly, and re-assembles them inside a CTkFrame that
    lives in this window. Public users are unaffected and still route
    to `waste_maneger.py` exactly as before.

    Clicking "Sign Out" (visible to every role) tears down whichever
    background `.after()` polling jobs are running, destroys the
    DashboardContainer, and re-shows the Login frame inside this same
    AuthPortal window.

Dependencies:
    pip install customtkinter pillow
    (matplotlib is required by dashboard.py's AnalyticsCard; if it is
    not installed, that panel degrades to a text notice exactly as
    dashboard.py already handles on its own.)
"""

import os
import json
import hmac
import hashlib
import secrets
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Callable

import customtkinter as ctk
from tkinter import messagebox
from PIL import Image


# -----------------  ----------------------------------------------------------
# 1. THEME — WastePulse brand identity (Light Mode)
# ---------------------------------------------------------------------------

class Theme:
    PRIMARY = "#15803D"         # leaf green — primary actions
    PRIMARY_HOVER = "#166534"
    NAVY = "#0F2537"            # deep navy — headers / secondary accent
    BACKGROUND = "#F8FAFC"      # off-white app background
    CARD_BG = "#FFFFFF"         # pure white cards
    BORDER = "#E2E8F0"
    TEXT_PRIMARY = "#0F2537"
    TEXT_MUTED = "#64748B"
    DANGER = "#B91C1C"
    DANGER_BG = "#FEF2F2"
    SUCCESS = "#15803D"
    SUCCESS_BG = "#F0FDF4"
    TOGGLE_INACTIVE = "#E2E8F0"

    FONT_FAMILY = "Segoe UI"
    BRAND_FONT = (FONT_FAMILY, 26, "bold")
    MOTTO_FONT = (FONT_FAMILY, 13, "bold")
    SUBTEXT_FONT = (FONT_FAMILY, 11)
    TOGGLE_FONT = (FONT_FAMILY, 14, "bold")
    LABEL_FONT = (FONT_FAMILY, 12, "bold")
    ENTRY_FONT = (FONT_FAMILY, 13)
    BUTTON_FONT = (FONT_FAMILY, 15, "bold")
    ERROR_FONT = (FONT_FAMILY, 11)

    CORNER_RADIUS = 14


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")


# ---------------------------------------------------------------------------
# 2. CONSTANTS
# ---------------------------------------------------------------------------

import sys
import os

# PyInstaller safe path handler
if getattr(sys, 'frozen', False):
    # जब .exe चलेगी तो यह उसका अपना फोल्डर देखेगा
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# डेटाबेस का पक्का पाथ
USERS_DB_PATH = os.path.join(BASE_DIR, "users_db.json")

# लोगो का पक्का पाथ (कैंडिडेट्स)
LOGO_PATH_CANDIDATES = tuple(
    os.path.join(BASE_DIR, name) for name in ("logo.jpeg", "logo.png", "logo.jpg")
)

ROLE_PUBLIC = "Public"
ROLE_SPCB_ADMIN = "SPCB Admin"
ROLES = (ROLE_PUBLIC, ROLE_SPCB_ADMIN)

PBKDF2_ITERATIONS = 260_000

AUTH_CARD_WIDTH = 460

# --- SPCB Staff sub-role / Unique System ID configuration -----------------

SYSTEM_ID_PREFIX_ADMIN = "SAS-"          # Senior Official (Admin)
SYSTEM_ID_PREFIX_FIELD_WORKER = "SAW-"   # Field Worker

SPCB_SUBROLE_ADMIN = "admin"                 # Full access including LLM
SPCB_SUBROLE_FIELD_WORKER = "field_worker"   # Location & Google Maps access only

SPCB_SUBROLE_LABEL_SENIOR = "Senior Official (Admin)"
SPCB_SUBROLE_LABEL_FIELD_WORKER = "Field Worker"

SPCB_SUBROLE_LABELS = {
    SPCB_SUBROLE_LABEL_SENIOR: SYSTEM_ID_PREFIX_ADMIN,
    SPCB_SUBROLE_LABEL_FIELD_WORKER: SYSTEM_ID_PREFIX_FIELD_WORKER,
}

# NOTE: In a production deployment this should be pulled from a secrets
# manager / environment variable and never hardcoded in source. Only a
# SHA-256 digest of the master password is kept here, and comparisons are
# performed with a constant-time check (hmac.compare_digest) so the master
# password itself is never stored or compared in plaintext.
_MASTER_ADMIN_PASSWORD_HASH = hashlib.sha256(b"SPCB@Admin#2024").hexdigest()


def _verify_master_password(candidate_password: str) -> bool:
    """Constant-time verification of the SPCB Master Administrative Password."""
    if not candidate_password:
        return False
    candidate_hash = hashlib.sha256(candidate_password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate_hash, _MASTER_ADMIN_PASSWORD_HASH)


def resolve_spcb_role(identifier: str) -> Optional[str]:
    """
    Resolves an SPCB staff member's access role strictly from their Unique
    System ID prefix:
        'SAS-...' -> 'admin'         (Full access including LLM)
        'SAW-...' -> 'field_worker'  (Location & Google Maps access only)
    Returns None if the identifier does not match a known prefix.
    """
    if not identifier:
        return None
    ident = identifier.strip().upper()
    if ident.startswith(SYSTEM_ID_PREFIX_ADMIN):
        return SPCB_SUBROLE_ADMIN
    if ident.startswith(SYSTEM_ID_PREFIX_FIELD_WORKER):
        return SPCB_SUBROLE_FIELD_WORKER
    return None


# ---------------------------------------------------------------------------
# 3. DASHBOARD COMPONENT IMPORTS (from dashboard.py — file is NEVER modified)
# ---------------------------------------------------------------------------
#
# NOTE ON CIRCULAR IMPORTS: dashboard.py itself does
# `from authentycation import start_login_session` (wrapped in its own
# try/except ImportError). Importing dashboard.py from here therefore
# creates a circular import. Python resolves this safely because that
# statement in dashboard.py is import-guarded: worst case it fails to
# resolve `start_login_session` and dashboard.py falls back to
# `start_login_session = None` internally — which never affects the
# code path in this file, since we don't use dashboard.py's own
# `SPCBDashboardApp` shell here at all, only its individual CTkFrame
# components and pure data functions (see DashboardContainer below).
try:
    from dashboard import (
        StatusBanner,
        KPICard,
        AnalyticsCard,
        LLMCard,
        HardwareStatusCard,
        LogTable,
        resolve_engine,
        load_waste_logs,
        compute_kpis,
        compute_category_distribution,
        SENSOR_HISTORY_LEN,
        ENGINE_POLL_MS,
        AUTO_REFRESH_MS,
    )
    # 1. यहाँ सोइल मॉड्यूलर क्लास इम्पोर्ट कर
    from soil_ai import SoilRiskPanel  
    DASHBOARD_AVAILABLE = True

except ImportError:
    DASHBOARD_AVAILABLE = False
    SoilRiskPanel = None  # Safe Fallback
    StatusBanner = KPICard = AnalyticsCard = LLMCard = HardwareStatusCard = LogTable = None
    resolve_engine = load_waste_logs = compute_kpis = compute_category_distribution = None
    SENSOR_HISTORY_LEN = 30
    ENGINE_POLL_MS = 2000
    AUTO_REFRESH_MS = 5000


# ---------------------------------------------------------------------------
# 4. WINDOW SIZING HELPER
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
# 5. DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class UserRecord:
    full_name: str
    username: str
    role: str
    salt: str
    password_hash: str
    system_id: Optional[str] = None
    sub_role: Optional[str] = None

    def public_dict(self) -> dict:
        return {
            "full_name": self.full_name,
            "username": self.username,
            "role": self.role,
            "system_id": self.system_id,
            "sub_role": self.sub_role,
        }


# ---------------------------------------------------------------------------
# 6. PERSISTENCE + AUTH LOGIC
# ---------------------------------------------------------------------------

class UserStore:
    def __init__(self, db_path: str = USERS_DB_PATH):
        self.db_path = db_path

    def _load_raw(self) -> Dict[str, dict]:
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_raw(self, data: Dict[str, dict]) -> None:
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _hash_password(password: str, salt: Optional[str] = None) -> tuple:
        salt = salt or secrets.token_hex(16)
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
        )
        return salt, derived.hex()

    def username_exists(self, username: str) -> bool:
        return username.strip().lower() in self._load_raw()

    def _generate_unique_system_id(self, prefix: str, data: Optional[Dict[str, dict]] = None) -> str:
        """
        Auto-generates a Unique System ID like 'SAS-1042' / 'SAW-5081' that
        does not collide with any existing username key or stored system_id.
        """
        data = data if data is not None else self._load_raw()
        existing_keys = set(data.keys())
        existing_system_ids = {
            (record.get("system_id") or "").upper() for record in data.values()
        }

        while True:
            candidate = f"{prefix}{secrets.randbelow(9000) + 1000}"
            if candidate.lower() not in existing_keys and candidate.upper() not in existing_system_ids:
                return candidate

    def register(self, full_name: str, username: str, password: str, role: str) -> UserRecord:
        """Standard registration flow for Public users (no master password required)."""
        full_name = full_name.strip()
        username_key = username.strip().lower()

        if not full_name or not username.strip() or not password:
            raise ValueError("Full name, username, and password are all required.")
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        data = self._load_raw()
        if username_key in data:
            raise ValueError(f"Username '{username}' is already registered.")

        salt, password_hash = self._hash_password(password)
        record = UserRecord(
            full_name=full_name, username=username.strip(), role=role,
            salt=salt, password_hash=password_hash,
        )
        data[username_key] = asdict(record)
        self._save_raw(data)
        return record

    def register_spcb(self, full_name: str, password: str, sub_role_label: str) -> UserRecord:
        """
        SPCB Staff registration flow. Assumes the Master Administrative
        Password has ALREADY been verified by the caller via
        `_verify_master_password`. Auto-generates a Unique System ID based
        on the selected sub-role and uses it as the login username.
        """
        full_name = full_name.strip()

        if not full_name or not password:
            raise ValueError("Full name and password are required.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        if sub_role_label not in SPCB_SUBROLE_LABELS:
            raise ValueError("Please select a valid SPCB sub-role.")

        prefix = SPCB_SUBROLE_LABELS[sub_role_label]
        sub_role_code = (
            SPCB_SUBROLE_ADMIN if prefix == SYSTEM_ID_PREFIX_ADMIN else SPCB_SUBROLE_FIELD_WORKER
        )

        data = self._load_raw()
        system_id = self._generate_unique_system_id(prefix, data)

        salt, password_hash = self._hash_password(password)
        record = UserRecord(
            full_name=full_name, username=system_id, role=ROLE_SPCB_ADMIN,
            salt=salt, password_hash=password_hash,
            system_id=system_id, sub_role=sub_role_code,
        )
        data[system_id.lower()] = asdict(record)
        self._save_raw(data)
        return record

    def authenticate(self, username: str, password: str) -> UserRecord:
        data = self._load_raw()
        record_dict = data.get(username.strip().lower())
        if record_dict is None:
            raise ValueError("Invalid username or password.")

        _, computed_hash = self._hash_password(password, record_dict["salt"])
        if not hmac.compare_digest(computed_hash, record_dict["password_hash"]):
            raise ValueError("Invalid username or password.")

        return UserRecord(**record_dict)


# ---------------------------------------------------------------------------
# 7. REUSABLE UI COMPONENTS
# ---------------------------------------------------------------------------

class BrandHeader(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._build_logo().pack(pady=(0, 10))

        ctk.CTkLabel(self, text="WastePulse", font=Theme.BRAND_FONT,
                     text_color=Theme.NAVY).pack()
        ctk.CTkLabel(self, text="Smart Sensing, Smarter Living",
                     font=Theme.MOTTO_FONT, text_color=Theme.PRIMARY).pack(pady=(2, 6))
        ctk.CTkLabel(self, text="Official SPCB · Government Waste Data Portal",
                     font=Theme.SUBTEXT_FONT, text_color=Theme.TEXT_MUTED).pack()

    def _build_logo(self):
        for candidate in LOGO_PATH_CANDIDATES:
            if os.path.exists(candidate):
                try:
                    pil_image = Image.open(candidate)
                    ctk_image = ctk.CTkImage(light_image=pil_image, size=(72, 72))
                    return ctk.CTkLabel(self, image=ctk_image, text="")
                except Exception:
                    continue

        badge = ctk.CTkFrame(self, width=72, height=72, corner_radius=20,
                             fg_color=Theme.PRIMARY)
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="WP", font=(Theme.FONT_FAMILY, 22, "bold"),
                     text_color="#FFFFFF").pack(expand=True)
        return badge


class LabeledEntry(ctk.CTkFrame):
    def __init__(self, master, label_text: str, show: Optional[str] = None, placeholder: str = ""):
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(self, text=label_text, font=Theme.LABEL_FONT,
                     text_color=Theme.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

        self.entry = ctk.CTkEntry(
            self, height=40, corner_radius=8, font=Theme.ENTRY_FONT,
            border_color=Theme.BORDER, placeholder_text=placeholder, show=show or ""
        )
        self.entry.pack(fill="x")

    def get(self) -> str:
        return self.entry.get()

    def clear(self):
        self.entry.delete(0, "end")


class ToggleSwitcher(ctk.CTkFrame):
    def __init__(self, master, on_change: Callable[[str], None]):
        super().__init__(master, fg_color=Theme.TOGGLE_INACTIVE, corner_radius=10)
        self.on_change = on_change
        self.active = "Login"

        self.login_btn = ctk.CTkButton(
            self, text="Login", font=Theme.TOGGLE_FONT, corner_radius=8,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color="#FFFFFF", command=lambda: self._select("Login")
        )
        self.register_btn = ctk.CTkButton(
            self, text="Register", font=Theme.TOGGLE_FONT, corner_radius=8,
            fg_color=Theme.TOGGLE_INACTIVE, hover_color=Theme.BORDER,
            text_color=Theme.TEXT_PRIMARY, command=lambda: self._select("Register")
        )
        self.login_btn.pack(side="left", expand=True, fill="both", padx=4, pady=4)
        self.register_btn.pack(side="left", expand=True, fill="both", padx=4, pady=4)

    def _select(self, choice: str):
        if choice == self.active:
            return
        self.active = choice
        if choice == "Login":
            self.login_btn.configure(fg_color=Theme.PRIMARY, text_color="#FFFFFF")
            self.register_btn.configure(fg_color=Theme.TOGGLE_INACTIVE, text_color=Theme.TEXT_PRIMARY)
        else:
            self.register_btn.configure(fg_color=Theme.PRIMARY, text_color="#FFFFFF")
            self.login_btn.configure(fg_color=Theme.TOGGLE_INACTIVE, text_color=Theme.TEXT_PRIMARY)
        self.on_change(choice)

    def set_active(self, choice: str):
        self._select(choice)


def show_toast(parent: ctk.CTk, message: str, success: bool = True, duration_ms: int = 2200):
    bg = Theme.SUCCESS_BG if success else Theme.DANGER_BG
    fg = Theme.SUCCESS if success else Theme.DANGER

    toast = ctk.CTkToplevel(parent)
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
    toast.configure(fg_color=bg)

    frame = ctk.CTkFrame(toast, fg_color=bg, corner_radius=10, border_width=1, border_color=fg)
    frame.pack(padx=2, pady=2)
    ctk.CTkLabel(frame, text=message, font=Theme.ERROR_FONT, text_color=fg,
                 wraplength=280, justify="left").pack(padx=16, pady=10)

    parent.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() // 2) - 150
    py = parent.winfo_rooty() + 60
    toast.geometry(f"300x60+{px}+{py}")
    toast.after(duration_ms, toast.destroy)


# ---------------------------------------------------------------------------
# 8. LOGIN / REGISTER FRAMES
# ---------------------------------------------------------------------------

class LoginFrame(ctk.CTkFrame):
    def __init__(self, master, user_store: UserStore, on_success: Callable[[UserRecord], None]):
        super().__init__(master, fg_color="transparent")
        self.user_store = user_store
        self.on_success = on_success

        self.username_field = LabeledEntry(
            self, "Username / Employee ID",
            placeholder="Username, Employee ID, or System ID (SAS-/SAW-)"
        )
        self.username_field.pack(fill="x", pady=(0, 14))

        self.password_field = LabeledEntry(self, "Password", show="•", placeholder="Enter your password")
        self.password_field.pack(fill="x", pady=(0, 6))
        self.password_field.entry.bind("<Return>", lambda _e: self._handle_login())

        self.error_label = ctk.CTkLabel(self, text="", font=Theme.ERROR_FONT,
                                         text_color=Theme.DANGER, wraplength=320, justify="left")
        self.error_label.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            self, text="Login", font=Theme.BUTTON_FONT, height=46, corner_radius=Theme.CORNER_RADIUS,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER, text_color="#FFFFFF",
            command=self._handle_login
        ).pack(fill="x", pady=(6, 0))

    def _handle_login(self):
        self.error_label.configure(text="")
        username = self.username_field.get()
        password = self.password_field.get()

        if not username or not password:
            self.error_label.configure(text="Please enter both username/Employee ID and password.")
            return

        try:
            record = self.user_store.authenticate(username, password)
        except ValueError as e:
            self.error_label.configure(text=str(e))
            return

        self.password_field.clear()
        self.on_success(record)


class RegisterFrame(ctk.CTkFrame):
    def __init__(self, master, user_store: UserStore, on_success: Callable[[Optional[str]], None]):
        super().__init__(master, fg_color="transparent")
        self.user_store = user_store
        self.on_success = on_success

        self.name_field = LabeledEntry(self, "Full Name", placeholder="e.g. Aditi Sharma")
        self.name_field.pack(fill="x", pady=(0, 12))

        # Visible only for Public registration — SPCB Staff get an
        # auto-generated Unique System ID instead of choosing a username.
        self.username_field = LabeledEntry(
            self, "User ID / Employee ID", placeholder="Choose a unique username or Employee ID"
        )
        self.username_field.pack(fill="x", pady=(0, 12))

        self.password_field = LabeledEntry(self, "Password", show="•", placeholder="Minimum 6 characters")
        self.password_field.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(self, text="Register As", font=Theme.LABEL_FONT,
                     text_color=Theme.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))
        self.role_var = ctk.StringVar(value=ROLE_PUBLIC)
        self.role_menu = ctk.CTkOptionMenu(
            self, values=list(ROLES), variable=self.role_var, height=40, corner_radius=8,
            fg_color="#FFFFFF", text_color=Theme.TEXT_PRIMARY, button_color=Theme.PRIMARY,
            button_hover_color=Theme.PRIMARY_HOVER, dropdown_fg_color="#FFFFFF",
            command=self._on_role_change
        )
        self.role_menu.pack(fill="x", pady=(0, 6))

        # --- SPCB-only extra fields (hidden unless "SPCB Admin" is chosen) ---
        self.spcb_extra_frame = ctk.CTkFrame(self, fg_color="transparent")

        ctk.CTkLabel(self.spcb_extra_frame, text="SPCB Sub-Role", font=Theme.LABEL_FONT,
                     text_color=Theme.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))
        self.sub_role_var = ctk.StringVar(value=SPCB_SUBROLE_LABEL_SENIOR)
        self.sub_role_menu = ctk.CTkOptionMenu(
            self.spcb_extra_frame,
            values=[SPCB_SUBROLE_LABEL_SENIOR, SPCB_SUBROLE_LABEL_FIELD_WORKER],
            variable=self.sub_role_var, height=40, corner_radius=8,
            fg_color="#FFFFFF", text_color=Theme.TEXT_PRIMARY, button_color=Theme.PRIMARY,
            button_hover_color=Theme.PRIMARY_HOVER, dropdown_fg_color="#FFFFFF",
        )
        self.sub_role_menu.pack(fill="x", pady=(0, 12))

        self.master_password_field = LabeledEntry(
            self.spcb_extra_frame, "Master Administrative Password", show="•",
            placeholder="Required for SPCB Staff registration"
        )
        self.master_password_field.pack(fill="x", pady=(0, 6))
        # spcb_extra_frame itself is NOT packed yet — shown by _on_role_change.

        self.error_label = ctk.CTkLabel(self, text="", font=Theme.ERROR_FONT,
                                         text_color=Theme.DANGER, wraplength=320, justify="left")
        self.error_label.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            self, text="Create Account", font=Theme.BUTTON_FONT, height=46, corner_radius=Theme.CORNER_RADIUS,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER, text_color="#FFFFFF",
            command=self._handle_register
        ).pack(fill="x", pady=(6, 0))

    def _on_role_change(self, choice: str):
        self.error_label.configure(text="")
        if choice == ROLE_PUBLIC:
            self.spcb_extra_frame.pack_forget()
            if not self.username_field.winfo_ismapped():
                self.username_field.pack(fill="x", pady=(0, 12), before=self.password_field)
        else:
            self.username_field.pack_forget()
            if not self.spcb_extra_frame.winfo_ismapped():
                self.spcb_extra_frame.pack(fill="x", pady=(0, 6), before=self.error_label)

    def _reset_fields(self):
        self.name_field.clear()
        self.username_field.clear()
        self.password_field.clear()
        self.master_password_field.clear()
        self.role_var.set(ROLE_PUBLIC)
        self.sub_role_var.set(SPCB_SUBROLE_LABEL_SENIOR)
        self._on_role_change(ROLE_PUBLIC)

    def _handle_register(self):
        self.error_label.configure(text="")
        full_name = self.name_field.get()
        password = self.password_field.get()
        role = self.role_var.get()

        if role == ROLE_PUBLIC:
            username = self.username_field.get()
            try:
                self.user_store.register(full_name, username, password, role)
            except ValueError as e:
                self.error_label.configure(text=str(e))
                return

            self._reset_fields()
            self.on_success(None)
            return

        # --- SPCB Staff registration path -------------------------------
        sub_role_label = self.sub_role_var.get()
        master_password = self.master_password_field.get()

        if not _verify_master_password(master_password):
            self.error_label.configure(text="Invalid Master Administrative Password.")
            return

        try:
            record = self.user_store.register_spcb(full_name, password, sub_role_label)
        except ValueError as e:
            self.error_label.configure(text=str(e))
            return

        self._reset_fields()
        self.on_success(record.system_id)


# ---------------------------------------------------------------------------
# 9. ROLE-BASED ROUTING — Public users only (SPCB Staff use the embedded
#    DashboardContainer in Section 10 instead of a subprocess hand-off).
# ---------------------------------------------------------------------------

import subprocess
import sys

def route_authenticated_user(auth_window: "AuthPortal", user: UserRecord) -> None:
    """
    Routes a Public user to waste_maneger.py in a separate Python process
    (kept as a subprocess launch to avoid Tkinter image collisions between
    the two independently-styled apps), then closes the auth window.

    SPCB Staff ('SAS-'/'SAW-') no longer go through this function — they
    are handled in-process by DashboardContainer (Section 10) so the
    dashboard can be shown as a frame switch inside this same window.
    """
    if user.role != ROLE_PUBLIC:
        messagebox.showerror(
            "Routing Error",
            "route_authenticated_user() is only used for Public users; "
            "SPCB Staff are routed via the embedded dashboard view."
        )
        return

    target_script = "waste_maneger.py"
    launch_env = os.environ.copy()
    launch_env["WASTEPULSE_USER_ROLE"] = "public"
    launch_env["WASTEPULSE_USER_NAME"] = user.full_name

    if not os.path.exists(target_script):
        messagebox.showerror("File Not Found", f"Could not find destination script: {target_script}")
        return

    try:
        subprocess.Popen([sys.executable, target_script], env=launch_env)
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to start the destination app.\n\n{e}")
        return

    auth_window.destroy()


# ---------------------------------------------------------------------------
# 10. EMBEDDED SPCB DASHBOARD CONTAINER — in-process frame switching
# ---------------------------------------------------------------------------

class DashboardContainer(ctk.CTkFrame):
    """
    Role-aware dashboard view embedded directly inside the AuthPortal
    window after a successful SPCB Staff login. Reuses dashboard.py's
    actual CTkFrame components and pure data functions — dashboard.py's
    file is never modified or touched.

    A fixed top header ("Logged in as: <name>" + Sign Out) is always
    shown, regardless of role. Below it, the body is built according to
    the resolved role:

        SPCB_SUBROLE_ADMIN ('SAS-')       -> full dashboard: StatusBanner,
            4 KPI cards, AnalyticsCard, LLMCard, HardwareStatusCard, and
            the full LogTable.

        SPCB_SUBROLE_FIELD_WORKER ('SAW-') -> ONLY dashboard.py's LogTable
            (the Google Maps Pin / location tracking view). LLMCard,
            AnalyticsCard, and HardwareStatusCard are never created for
            this role.
    """

    def __init__(self, master, user: UserRecord, resolved_role: str,
                 on_sign_out: Callable[[], None]):
        super().__init__(master, fg_color=Theme.BACKGROUND)
        self.user = user
        self.resolved_role = resolved_role
        self.on_sign_out = on_sign_out

        self.engine = None
        self._auto_refresh_job = None
        self._engine_poll_job = None
        self._last_log_count = -1
        self._refresh_data: Callable[[], None] = lambda: None

        self._build_header()

        self.body = ctk.CTkScrollableFrame(self, fg_color=Theme.BACKGROUND)
        self.body.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.body.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")

        if not DASHBOARD_AVAILABLE:
            ctk.CTkLabel(
                self.body,
                text="dashboard.py could not be imported — the dashboard view is unavailable.",
                font=Theme.SUBTEXT_FONT, text_color=Theme.DANGER, wraplength=900, justify="left"
            ).grid(row=0, column=0, columnspan=4, sticky="w", pady=20)
            return

        if self.resolved_role == SPCB_SUBROLE_ADMIN:
            self._build_admin_view()
        else:
            self._build_field_worker_view()

    # -- Fixed top header (visible to ALL roles) -------------------------

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=Theme.NAVY, corner_radius=0, height=88)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="y", padx=28)
        ctk.CTkLabel(left, text="WastePulse", font=Theme.BRAND_FONT,
                     text_color="#FFFFFF").pack(anchor="w", expand=True)
        ctk.CTkLabel(left, text="Smart Sensing, Smarter Living",
                     font=Theme.SUBTEXT_FONT, text_color="#CBD5E1").pack(anchor="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", fill="y", padx=28)
        info_col = ctk.CTkFrame(right, fg_color="transparent")
        info_col.pack(expand=True)

        role_label = (
            "Senior Official (Admin)" if self.resolved_role == SPCB_SUBROLE_ADMIN else "Field Worker"
        )
        ctk.CTkLabel(
            info_col, text=f"Logged in as: {self.user.full_name}  ·  {role_label}",
            font=Theme.SUBTEXT_FONT, text_color="#CBD5E1"
        ).pack(anchor="e", pady=(0, 8))

        ctk.CTkButton(
            info_col, text="⏻ Sign Out", font=Theme.LABEL_FONT, height=34, width=140,
            corner_radius=Theme.CORNER_RADIUS, fg_color=Theme.DANGER, hover_color="#8f1717",
            text_color="#FFFFFF", command=self._handle_sign_out
        ).pack(anchor="e")

    # -- Admin ('SAS-'): FULL dashboard view -----------------------------

    def _build_admin_view(self):
        self.engine = resolve_engine(None)

        self.status_banner = StatusBanner(self.body)
        self.status_banner.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(16, 16))

        self.kpi_total = KPICard(self.body, "Total Scans Logged", accent=Theme.NAVY)
        self.kpi_biodeg = KPICard(self.body, "% Biodegradable", accent=Theme.PRIMARY)
        self.kpi_non_biodeg = KPICard(self.body, "% Non-Biodegradable", accent="#0284C7")
        self.kpi_hazard = KPICard(self.body, "Hazardous Flags", accent=Theme.DANGER)
        for i, card in enumerate((self.kpi_total, self.kpi_biodeg, self.kpi_non_biodeg, self.kpi_hazard)):
            card.grid(row=1, column=i, sticky="nsew", padx=8, pady=(0, 20))

        self.analytics_card = AnalyticsCard(self.body)
        self.analytics_card.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(0, 8), pady=(0, 20))

        side_panel = ctk.CTkFrame(self.body, fg_color="transparent")
        side_panel.grid(row=2, column=2, columnspan=2, sticky="nsew", padx=(8, 0), pady=(0, 20))
        side_panel.grid_columnconfigure((0, 1), weight=1, uniform="side")

        self.llm_card = LLMCard(side_panel)
        self.llm_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.hardware_card = HardwareStatusCard(side_panel)
        self.hardware_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.log_table = LogTable(self.body)
        self.log_table.grid(row=3, column=0, columnspan=4, sticky="nsew")
        try:
            if DASHBOARD_AVAILABLE and SoilRiskPanel:
                self.soil_panel = SoilRiskPanel(self.body)
                self.soil_panel.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=10)
        except Exception as e:
            print("Soil Panel Load Error:", e)
         
        self._refresh_data = self._refresh_admin_data
        self._refresh_data()
        self._schedule_auto_refresh()

        self._poll_engine()
        self._schedule_engine_poll()
        

    def _refresh_admin_data(self):
        logs = load_waste_logs()
        kpis = compute_kpis(logs)
        self.kpi_total.set_value(str(kpis["total"]))
        self.kpi_biodeg.set_value(f"{kpis['pct_biodegradable']}%")
        self.kpi_non_biodeg.set_value(f"{kpis['pct_non_biodegradable']}%")
        self.kpi_hazard.set_value(str(kpis["hazardous_count"]))
        self.log_table.populate(logs)
        self.analytics_card.update_category_chart(compute_category_distribution(logs))
        self._last_log_count = len(logs)

    def _poll_engine(self):
        if self.engine is None:
            return
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

        self.analytics_card.update_moisture_chart(self.engine.get_recent_moisture_readings(SENSOR_HISTORY_LEN))
        self.analytics_card.update_ir_chart(self.engine.get_recent_ir_readings(SENSOR_HISTORY_LEN))
        self.analytics_card.update_metal_chart(self.engine.get_recent_metal_readings(SENSOR_HISTORY_LEN))

    def _schedule_engine_poll(self):
        self._engine_poll_job = self.after(ENGINE_POLL_MS, self._engine_poll_tick)

    def _engine_poll_tick(self):
        self._poll_engine()
        self._schedule_engine_poll()

    # -- Field Worker ('SAW-'): ONLY the maps/location log ---------------

    def _build_field_worker_view(self):
        ctk.CTkLabel(
            self.body, text="Field Location Log — Google Maps Pins",
            font=Theme.LABEL_FONT, text_color=Theme.NAVY
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(16, 4))

        ctk.CTkLabel(
            self.body,
            text="Field Worker access is restricted to location/scan tracking only. "
                 "LLM decision tools, live sensor telemetry, and admin analytics are "
                 "not available on this account.",
            font=Theme.SUBTEXT_FONT, text_color=Theme.TEXT_MUTED, wraplength=900, justify="left"
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 12))

        self.log_table = LogTable(self.body)
        self.log_table.grid(row=2, column=0, columnspan=4, sticky="nsew")

        self._refresh_data = self._refresh_field_worker_data
        self._refresh_data()
        self._schedule_auto_refresh()

    def _refresh_field_worker_data(self):
        logs = load_waste_logs()
        self.log_table.populate(logs)
        self._last_log_count = len(logs)

    # -- Shared waste_logs.json polling (both roles) ---------------------

    def _schedule_auto_refresh(self):
        self._auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        logs = load_waste_logs()
        if len(logs) != self._last_log_count:
            self._refresh_data()
        self._schedule_auto_refresh()

    # -- Sign Out (visible to ALL roles) ---------------------------------

    def _handle_sign_out(self):
        if self._auto_refresh_job is not None:
            self.after_cancel(self._auto_refresh_job)
            self._auto_refresh_job = None
        if self._engine_poll_job is not None:
            self.after_cancel(self._engine_poll_job)
            self._engine_poll_job = None
        self.on_sign_out()


# ---------------------------------------------------------------------------
# 11. MAIN PORTAL WINDOW
# ---------------------------------------------------------------------------

class AuthPortal(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("WastePulse — Authentication Portal")
        maximize_window(self)
        self.configure(fg_color=Theme.BACKGROUND)

        self.user_store = UserStore()
        self.dashboard_container: Optional[DashboardContainer] = None

        self._build_layout()

    def _build_layout(self):
        self.auth_card = ctk.CTkFrame(
            self, fg_color=Theme.CARD_BG, corner_radius=Theme.CORNER_RADIUS,
            border_width=1, border_color=Theme.BORDER
        )
        self.auth_card.place(relx=0.5, rely=0.5, anchor="center")

        card_inner = ctk.CTkFrame(self.auth_card, fg_color="transparent", width=AUTH_CARD_WIDTH)
        card_inner.pack(padx=30, pady=30)

        BrandHeader(card_inner).pack(pady=(0, 24))

        self.switcher = ToggleSwitcher(card_inner, on_change=self._on_tab_change)
        self.switcher.pack(fill="x", pady=(0, 22))

        self.form_container = ctk.CTkFrame(card_inner, fg_color="transparent")
        self.form_container.pack(fill="both", expand=True)

        self.login_frame = LoginFrame(self.form_container, self.user_store,
                                      on_success=self._handle_login_success)
        self.register_frame = RegisterFrame(self.form_container, self.user_store,
                                            on_success=self._handle_register_success)

        self._show_frame(self.login_frame)

    def _show_frame(self, frame: ctk.CTkFrame):
        for child in self.form_container.winfo_children():
            child.pack_forget()
        frame.pack(fill="both", expand=True)

    def _on_tab_change(self, choice: str):
        if choice == "Login":
            self._show_frame(self.login_frame)
        else:
            self._show_frame(self.register_frame)

    def _handle_register_success(self, system_id: Optional[str] = None):
        if system_id:
            show_toast(
                self,
                f"SPCB account created. Your Unique System ID is {system_id}. "
                f"Please save it — you'll need it to log in.",
                success=True, duration_ms=4500
            )
        else:
            show_toast(self, "Account created successfully. Please log in.", success=True)
        self.switcher.set_active("Login")

    def _handle_login_success(self, user: UserRecord):
        show_toast(self, f"Welcome, {user.full_name}. Redirecting…", success=True)
        self.after(700, lambda: self._route_after_login(user))

    def _route_after_login(self, user: UserRecord):
        """
        Public users are launched as a separate process exactly as
        before. SPCB Staff ('SAS-'/'SAW-') are switched, in-process,
        into the embedded DashboardContainer frame — no subprocess,
        same window.
        """
        if user.role == ROLE_PUBLIC:
            route_authenticated_user(self, user)
            return

        resolved_role = resolve_spcb_role(user.system_id or user.username)
        if resolved_role is None:
            messagebox.showerror(
                "Access Error",
                f"Could not resolve an SPCB access role for ID '{user.username}'."
            )
            return

        self._open_spcb_dashboard(user, resolved_role)

    def _open_spcb_dashboard(self, user: UserRecord, resolved_role: str):
        self.auth_card.place_forget()

        self.dashboard_container = DashboardContainer(
            self, user=user, resolved_role=resolved_role,
            on_sign_out=self._handle_dashboard_sign_out
        )
        self.dashboard_container.pack(fill="both", expand=True)

    def _handle_dashboard_sign_out(self):
        """Destroys the embedded dashboard frame and returns to the
        Login frame inside this same AuthPortal window."""
        if self.dashboard_container is not None:
            self.dashboard_container.destroy()
            self.dashboard_container = None

        self.login_frame.password_field.clear()
        self.switcher.set_active("Login")
        self.auth_card.place(relx=0.5, rely=0.5, anchor="center")


# ------------------------------------------------###
# 12. ENTRY POINT
# ---------------------------------------------------------------------------
def start_login_session():
    """
    Hand-off target for other WastePulse windows that need to route
    control back to the Login Screen (e.g. dashboard.py's "Sign Out"
    button, when dashboard.py's own SPCBDashboardApp shell is used
    standalone). Simply constructs and runs a fresh AuthPortal
    in-process — no subprocess, no os.system, nothing else launched.
    """
    app = AuthPortal()
    app.mainloop()
def main():
    app = AuthPortal()
    app.mainloop()


if __name__ == "__main__":
    main()