"""
ai_soil_predictor.py
-----------------------
WastePulse — AI Soil & Landslide Risk Predictor
(Smart India Hackathon — SIH26001: AI-Based Early Warning and Landslide
 Risk Monitoring System in NER)

Analyzes post-cleanup soil telemetry (moisture %, rainfall intensity,
soil compaction/stability) from a secondary sensor module and produces
a deterministic, rule-based landslide risk assessment plus a
human-readable mitigation report.

Design principles, matching the rest of WastePulse:
  - The scoring logic is completely GUI-independent (sections 1-5 below)
    so it can be unit-tested, called from a CLI, or wired into any UI
    framework without change.
  - No randomness anywhere in the scoring path — identical input always
    produces an identical risk score and level.
  - A ready-to-embed CustomTkinter panel (section 6) is included for
    convenience, but it's entirely optional — everything above it works
    with zero GUI dependency.

Dependencies:
    Core logic: none beyond the standard library.
    Optional GUI panel: pip install customtkinter
"""

import os
import json
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List


# ---------------------------------------------------------------------------
# 1. CONSTANTS — thresholds and weights
#    Tune these against real field/sensor calibration data; nothing here
#    is hardcoded deeper than this block, so recalibrating the whole
#    model is a one-place edit.
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOIL_LOGS_PATH = os.path.join(BASE_DIR, "soil_logs.json")

# Risk level labels (exact strings used everywhere downstream).
RISK_SAFE = "Safe"
RISK_MODERATE = "Moderate Warning"
RISK_CRITICAL = "Critical / Red Alert"

# Composite score (0-100) thresholds.
SAFE_MAX_SCORE = 34.9
MODERATE_MAX_SCORE = 69.9
# score >= 70 -> Critical / Red Alert

# Relative importance of each factor in the composite score. Must sum to 1.0.
WEIGHT_MOISTURE = 0.40
WEIGHT_RAINFALL = 0.35
WEIGHT_STABILITY = 0.25

# Soil moisture thresholds (%). Saturated soil loses shear strength,
# which is one of the primary landslide preconditions.
MOISTURE_SAFE_MAX = 30.0
MOISTURE_MODERATE_MAX = 60.0
# > MOISTURE_MODERATE_MAX -> high sub-score

# Rainfall intensity thresholds (mm/hr). Intense rainfall is a primary
# landslide trigger, especially on already-saturated slopes.
RAINFALL_SAFE_MAX = 10.0
RAINFALL_MODERATE_MAX = 30.0
RAINFALL_SCORE_CEILING = 60.0  # intensity at/above this maps to a full 100 sub-score

# Soil stability/compaction index (0-100, HIGHER = more stable/compacted).
# Waste removal + stabilization work should push this UP over time.
STABILITY_SAFE_MIN = 70.0
STABILITY_MODERATE_MIN = 45.0
# < STABILITY_MODERATE_MIN -> high sub-score (loose, disturbed, poorly compacted soil)


# ---------------------------------------------------------------------------
# 2. DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class SoilReading:
    """A single soil telemetry snapshot from the secondary sensor
    module — normally taken AFTER waste has been cleared from a
    vulnerable slope, but usable for any point-in-time reading."""
    moisture_pct: float                  # 0-100
    rainfall_intensity_mm_hr: float       # mm/hr, typically 0-100+
    stability_index: float                # 0-100, higher = more stable/compacted
    location: str = "Unknown"
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskAssessment:
    """Deterministic output of calculate_landslide_risk()."""
    risk_score: float                     # 0-100 composite score
    risk_level: str                       # RISK_SAFE / RISK_MODERATE / RISK_CRITICAL
    factor_scores: Dict[str, float]       # per-factor sub-scores, kept for transparency
    recommendations: List[str]
    reading: SoilReading

    def to_dict(self) -> dict:
        return {
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "factor_scores": self.factor_scores,
            "recommendations": self.recommendations,
            "reading": self.reading.to_dict(),
        }


# ---------------------------------------------------------------------------
# 3. RISK SCORING — deterministic, rule-based
#    (swap the body of calculate_landslide_risk for a trained model
#    later; SoilReading in / RiskAssessment out stays the same contract)
# ---------------------------------------------------------------------------

def _score_moisture(moisture_pct: float) -> float:
    """0-100 sub-score for moisture, ramped linearly within each band
    so the score never jumps discontinuously at a threshold boundary."""
    moisture_pct = max(0.0, min(100.0, moisture_pct))
    if moisture_pct <= MOISTURE_SAFE_MAX:
        return (moisture_pct / MOISTURE_SAFE_MAX) * 33.0 if MOISTURE_SAFE_MAX else 0.0
    if moisture_pct <= MOISTURE_MODERATE_MAX:
        span = MOISTURE_MODERATE_MAX - MOISTURE_SAFE_MAX
        progress = (moisture_pct - MOISTURE_SAFE_MAX) / span
        return 33.0 + progress * 33.0
    span = 100.0 - MOISTURE_MODERATE_MAX
    progress = min(1.0, (moisture_pct - MOISTURE_MODERATE_MAX) / span) if span else 1.0
    return 66.0 + progress * 34.0


def _score_rainfall(rainfall_mm_hr: float) -> float:
    rainfall_mm_hr = max(0.0, rainfall_mm_hr)
    if rainfall_mm_hr <= RAINFALL_SAFE_MAX:
        return (rainfall_mm_hr / RAINFALL_SAFE_MAX) * 33.0 if RAINFALL_SAFE_MAX else 0.0
    if rainfall_mm_hr <= RAINFALL_MODERATE_MAX:
        span = RAINFALL_MODERATE_MAX - RAINFALL_SAFE_MAX
        progress = (rainfall_mm_hr - RAINFALL_SAFE_MAX) / span
        return 33.0 + progress * 33.0
    span = RAINFALL_SCORE_CEILING - RAINFALL_MODERATE_MAX
    progress = min(1.0, (rainfall_mm_hr - RAINFALL_MODERATE_MAX) / span) if span else 1.0
    return 66.0 + progress * 34.0


def _score_stability(stability_index: float) -> float:
    """Inverted relationship: LOWER stability_index -> HIGHER risk sub-score."""
    stability_index = max(0.0, min(100.0, stability_index))
    if stability_index >= STABILITY_SAFE_MIN:
        span = 100.0 - STABILITY_SAFE_MIN
        progress = (stability_index - STABILITY_SAFE_MIN) / span if span else 1.0
        return max(0.0, 33.0 - progress * 33.0)
    if stability_index >= STABILITY_MODERATE_MIN:
        span = STABILITY_SAFE_MIN - STABILITY_MODERATE_MIN
        progress = (stability_index - STABILITY_MODERATE_MIN) / span
        return 66.0 - progress * 33.0
    span = STABILITY_MODERATE_MIN
    progress = min(1.0, (STABILITY_MODERATE_MIN - stability_index) / span) if span else 1.0
    return 66.0 + progress * 34.0


def _resolve_risk_level(score: float) -> str:
    if score <= SAFE_MAX_SCORE:
        return RISK_SAFE
    if score <= MODERATE_MAX_SCORE:
        return RISK_MODERATE
    return RISK_CRITICAL


def _build_recommendations(risk_level: str, factor_scores: Dict[str, float]) -> List[str]:
    recs: List[str] = []

    if risk_level == RISK_SAFE:
        recs.append("Slope conditions are within safe limits. Continue routine monitoring.")
    elif risk_level == RISK_MODERATE:
        recs.append("Increase monitoring frequency at this location over the next 48-72 hours.")
    else:
        recs.append("URGENT: Notify the local disaster management authority and consider an "
                     "evacuation advisory for downhill settlements.")

    if factor_scores["moisture"] > 60:
        recs.append("Install or inspect subsurface drainage to relieve soil saturation.")
    if factor_scores["rainfall"] > 60:
        recs.append("Cross-check against the regional rainfall forecast; deploy additional rain "
                     "gauges if coverage is thin near this site.")
    if factor_scores["stability"] > 60:
        recs.append("Schedule slope stabilization work (retaining structures, re-vegetation, "
                     "compaction) — soil disturbed by recent waste clearance may need "
                     "reinforcement before the next rainfall event.")

    return recs


def calculate_landslide_risk(reading: SoilReading) -> RiskAssessment:
    """
    Deterministic landslide risk assessment from a single soil reading.
    The same input always produces the same output.
    """
    moisture_score = round(_score_moisture(reading.moisture_pct), 1)
    rainfall_score = round(_score_rainfall(reading.rainfall_intensity_mm_hr), 1)
    stability_score = round(_score_stability(reading.stability_index), 1)

    composite = (
        moisture_score * WEIGHT_MOISTURE
        + rainfall_score * WEIGHT_RAINFALL
        + stability_score * WEIGHT_STABILITY
    )
    composite = round(min(100.0, max(0.0, composite)), 1)

    factor_scores = {
        "moisture": moisture_score,
        "rainfall": rainfall_score,
        "stability": stability_score,
    }
    risk_level = _resolve_risk_level(composite)
    recommendations = _build_recommendations(risk_level, factor_scores)

    return RiskAssessment(
        risk_score=composite,
        risk_level=risk_level,
        factor_scores=factor_scores,
        recommendations=recommendations,
        reading=reading,
    )


# ---------------------------------------------------------------------------
# 4. MITIGATION REPORT
#    Optionally compares a pre-cleanup baseline reading against the
#    post-cleanup reading to show the measured risk reduction.
# ---------------------------------------------------------------------------

def generate_mitigation_report(post_cleanup: SoilReading,
                                pre_cleanup: Optional[SoilReading] = None) -> str:
    """
    Builds a human-readable mitigation report. If a pre-cleanup baseline
    reading is supplied, the report shows the measured risk change
    attributable to waste removal / stabilization work; otherwise it
    reports the current post-cleanup risk on its own.
    """
    post_assessment = calculate_landslide_risk(post_cleanup)

    lines = [
        "=" * 62,
        " WASTEPULSE — SOIL HEALTH & LANDSLIDE RISK MITIGATION REPORT",
        "=" * 62,
        f" Location: {post_cleanup.location}",
        f" Assessment Timestamp: {post_cleanup.timestamp}",
        "-" * 62,
        f" POST-CLEANUP RISK SCORE : {post_assessment.risk_score}/100",
        f" POST-CLEANUP RISK LEVEL : {post_assessment.risk_level}",
        "-" * 62,
        " Factor Breakdown (0-100 sub-score, higher = more concerning):",
        f"   Soil Moisture   : {post_assessment.factor_scores['moisture']}",
        f"   Rainfall        : {post_assessment.factor_scores['rainfall']}",
        f"   Soil Stability  : {post_assessment.factor_scores['stability']}",
    ]

    if pre_cleanup is not None:
        pre_assessment = calculate_landslide_risk(pre_cleanup)
        score_delta = round(pre_assessment.risk_score - post_assessment.risk_score, 1)
        if score_delta > 0:
            direction = "REDUCED"
        elif score_delta < 0:
            direction = "INCREASED"
        else:
            direction = "UNCHANGED"
        lines += [
            "-" * 62,
            f" PRE-CLEANUP RISK SCORE  : {pre_assessment.risk_score}/100 "
            f"({pre_assessment.risk_level})",
            f" RISK CHANGE AFTER WASTE REMOVAL & STABILIZATION: "
            f"{abs(score_delta)} points {direction}",
        ]

    lines += ["-" * 62, " Recommendations:"]
    for rec in post_assessment.recommendations:
        lines.append(f"   - {rec}")
    lines.append("=" * 62)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. JSON LOGGING
#    Mirrors the waste_logs.json pattern already used elsewhere in
#    WastePulse, so a dashboard can list historical soil assessments
#    the same way it lists waste scans.
# ---------------------------------------------------------------------------

def load_soil_logs() -> List[dict]:
    if not os.path.exists(SOIL_LOGS_PATH):
        return []
    try:
        with open(SOIL_LOGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_soil_log(assessment: RiskAssessment, user: str = "System") -> dict:
    """Appends one risk assessment to soil_logs.json and returns the
    stored entry (handy for immediately reflecting it in a UI table
    without a second read-back)."""
    entry = {
        "timestamp": assessment.reading.timestamp,
        "user": user,
        "location": assessment.reading.location,
        "moisture_pct": assessment.reading.moisture_pct,
        "rainfall_intensity_mm_hr": assessment.reading.rainfall_intensity_mm_hr,
        "stability_index": assessment.reading.stability_index,
        "risk_score": assessment.risk_score,
        "risk_level": assessment.risk_level,
    }
    logs = load_soil_logs()
    logs.append(entry)
    with open(SOIL_LOGS_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)
    return entry


# ---------------------------------------------------------------------------
# 6. OPTIONAL EMBEDDABLE GUI PANEL (CustomTkinter)
#    Everything above this line has zero GUI dependency. This section is
#    purely additive — import it only if/when you want the ready-made
#    panel; the scoring/report/logging functions work standalone.
# ---------------------------------------------------------------------------

try:
    import customtkinter as ctk
    CUSTOMTKINTER_AVAILABLE = True
except ImportError:
    CUSTOMTKINTER_AVAILABLE = False


if CUSTOMTKINTER_AVAILABLE:

    class _PanelColors:
        """Local color set matching WastePulse's existing palette. If
        your main.py already defines a Theme class, feel free to delete
        this and reference Theme.PRIMARY / Theme.NAVY / etc. instead —
        this class exists only so ai_soil_predictor.py has zero import
        dependency on the rest of your project."""
        PRIMARY = "#15803D"
        PRIMARY_HOVER = "#166534"
        NAVY = "#0F2537"
        BACKGROUND = "#F8FAFC"
        CARD_BG = "#FFFFFF"
        BORDER = "#E2E8F0"
        SLATE_TEXT = "#334155"
        MUTED_TEXT = "#64748B"
        WARNING = "#B45309"
        WARNING_BG = "#FFFBEB"
        DANGER = "#B91C1C"
        DANGER_BG = "#FEF2F2"
        SAFE_BG = "#F0FDF4"

        FONT_FAMILY = "Segoe UI"

    RISK_LEVEL_COLORS = {
        RISK_SAFE: (_PanelColors.PRIMARY, _PanelColors.SAFE_BG),
        RISK_MODERATE: (_PanelColors.WARNING, _PanelColors.WARNING_BG),
        RISK_CRITICAL: (_PanelColors.DANGER, _PanelColors.DANGER_BG),
    }

    class SoilRiskPanel(ctk.CTkFrame):
        """
        Self-contained CustomTkinter panel: soil parameter inputs, a
        "Run Risk Assessment" button, a color-coded result card, and a
        "Save to Log" button. Designed to be dropped straight into an
        existing dashboard as a tab or a section — see the module-level
        docstring / accompanying integration notes for exact snippets.
        """

        def __init__(self, master, username: str = "System"):
            super().__init__(master, fg_color=_PanelColors.BACKGROUND)
            self.username = username
            self.last_assessment: Optional[RiskAssessment] = None
            self._build_layout()

        def _build_layout(self):
            title_row = ctk.CTkFrame(self, fg_color="transparent")
            title_row.pack(fill="x", padx=20, pady=(20, 10))
            ctk.CTkLabel(
                title_row, text="AI Soil & Landslide Risk Predictor",
                font=(_PanelColors.FONT_FAMILY, 20, "bold"), text_color=_PanelColors.NAVY
            ).pack(anchor="w")
            ctk.CTkLabel(
                title_row, text="SIH26001 · Post-cleanup soil telemetry -> deterministic risk score",
                font=(_PanelColors.FONT_FAMILY, 11), text_color=_PanelColors.MUTED_TEXT
            ).pack(anchor="w")

            input_card = ctk.CTkFrame(self, fg_color=_PanelColors.CARD_BG, corner_radius=12,
                                       border_width=1, border_color=_PanelColors.BORDER)
            input_card.pack(fill="x", padx=20, pady=(0, 16))
            inner = ctk.CTkFrame(input_card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=18)
            inner.grid_columnconfigure((0, 1), weight=1, uniform="cols")

            self.location_entry = self._labeled_entry(inner, "Location", "e.g. Sector 7 Hillside")
            self.location_entry.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))

            self.moisture_entry = self._labeled_entry(inner, "Soil Moisture (%)", "0 - 100")
            self.moisture_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 12))

            self.rainfall_entry = self._labeled_entry(inner, "Rainfall Intensity (mm/hr)", "0 - 100+")
            self.rainfall_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 12))

            self.stability_entry = self._labeled_entry(
                inner, "Soil Stability / Compaction Index (0-100)", "Higher = more stable"
            )
            self.stability_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))

            ctk.CTkButton(
                inner, text="Run Risk Assessment", font=(_PanelColors.FONT_FAMILY, 14, "bold"),
                height=44, corner_radius=10, fg_color=_PanelColors.PRIMARY,
                hover_color=_PanelColors.PRIMARY_HOVER, text_color="#FFFFFF",
                command=self._handle_run_assessment
            ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))

            self.result_card = ctk.CTkFrame(self, fg_color=_PanelColors.CARD_BG, corner_radius=12,
                                             border_width=1, border_color=_PanelColors.BORDER)
            self.result_card.pack(fill="both", expand=True, padx=20, pady=(0, 20))

            self.result_placeholder = ctk.CTkLabel(
                self.result_card, text="Enter soil parameters above and run an assessment "
                                       "to see the risk report here.",
                font=(_PanelColors.FONT_FAMILY, 13), text_color=_PanelColors.MUTED_TEXT
            )
            self.result_placeholder.pack(padx=18, pady=18)

        @staticmethod
        def _labeled_entry(master, label_text: str, placeholder: str) -> ctk.CTkEntry:
            wrapper = ctk.CTkFrame(master, fg_color="transparent")
            wrapper.grid_configure()
            ctk.CTkLabel(wrapper, text=label_text, font=(_PanelColors.FONT_FAMILY, 12, "bold"),
                         text_color=_PanelColors.SLATE_TEXT).pack(anchor="w", pady=(0, 4))
            entry = ctk.CTkEntry(wrapper, height=38, corner_radius=8,
                                  placeholder_text=placeholder, border_color=_PanelColors.BORDER)
            entry.pack(fill="x")
            wrapper.entry_widget = entry
            # Return the wrapper so .grid() on the caller positions label+entry
            # together, but expose the inner entry as an attribute below.
            wrapper.get = entry.get
            return wrapper

        def _handle_run_assessment(self):
            try:
                moisture = float(self.moisture_entry.entry_widget.get().strip())
                rainfall = float(self.rainfall_entry.entry_widget.get().strip())
                stability = float(self.stability_entry.entry_widget.get().strip())
            except ValueError:
                self._render_error("Please enter valid numbers for moisture, rainfall, and stability.")
                return

            location = self.location_entry.entry_widget.get().strip() or "Unknown"
            reading = SoilReading(
                moisture_pct=moisture, rainfall_intensity_mm_hr=rainfall,
                stability_index=stability, location=location,
            )
            self.last_assessment = calculate_landslide_risk(reading)
            self._render_result(self.last_assessment)

        def _render_error(self, message: str):
            for child in self.result_card.winfo_children():
                child.destroy()
            ctk.CTkLabel(self.result_card, text=message, font=(_PanelColors.FONT_FAMILY, 13),
                         text_color=_PanelColors.DANGER).pack(padx=18, pady=18)

        def _render_result(self, assessment: RiskAssessment):
            for child in self.result_card.winfo_children():
                child.destroy()

            accent, badge_bg = RISK_LEVEL_COLORS[assessment.risk_level]

            header_row = ctk.CTkFrame(self.result_card, fg_color="transparent")
            header_row.pack(fill="x", padx=18, pady=(18, 6))
            ctk.CTkLabel(header_row, text=f"Risk Score: {assessment.risk_score}/100",
                         font=(_PanelColors.FONT_FAMILY, 18, "bold"),
                         text_color=_PanelColors.NAVY).pack(side="left")

            badge = ctk.CTkFrame(header_row, fg_color=badge_bg, corner_radius=8)
            badge.pack(side="right")
            ctk.CTkLabel(badge, text=assessment.risk_level, font=(_PanelColors.FONT_FAMILY, 12, "bold"),
                         text_color=accent).pack(padx=12, pady=6)

            factor_row = ctk.CTkFrame(self.result_card, fg_color="transparent")
            factor_row.pack(fill="x", padx=18, pady=(6, 12))
            for label, key in (("Moisture", "moisture"), ("Rainfall", "rainfall"),
                                ("Stability", "stability")):
                tile = ctk.CTkFrame(factor_row, fg_color="#F1F5F9", corner_radius=8)
                tile.pack(side="left", expand=True, fill="x", padx=4)
                ctk.CTkLabel(tile, text=label.upper(), font=(_PanelColors.FONT_FAMILY, 10, "bold"),
                             text_color=_PanelColors.MUTED_TEXT).pack(pady=(8, 0))
                ctk.CTkLabel(tile, text=f"{assessment.factor_scores[key]}",
                             font=(_PanelColors.FONT_FAMILY, 16, "bold"),
                             text_color=_PanelColors.SLATE_TEXT).pack(pady=(0, 8))

            ctk.CTkLabel(self.result_card, text="Recommendations", font=(_PanelColors.FONT_FAMILY, 12, "bold"),
                         text_color=_PanelColors.NAVY).pack(anchor="w", padx=18)
            rec_text = "\n".join(f"• {r}" for r in assessment.recommendations)
            ctk.CTkLabel(self.result_card, text=rec_text, font=(_PanelColors.FONT_FAMILY, 12),
                         text_color=_PanelColors.SLATE_TEXT, justify="left", wraplength=520
                         ).pack(anchor="w", padx=18, pady=(4, 12))

            ctk.CTkButton(
                self.result_card, text="Save Assessment to Log", font=(_PanelColors.FONT_FAMILY, 12, "bold"),
                height=38, corner_radius=8, fg_color=_PanelColors.NAVY, hover_color="#16344a",
                text_color="#FFFFFF", command=lambda: self._handle_save(assessment)
            ).pack(anchor="w", padx=18, pady=(0, 18))

        def _handle_save(self, assessment: RiskAssessment):
            append_soil_log(assessment, user=self.username)
            self._render_error_free_confirmation()

        def _render_error_free_confirmation(self):
            confirm = ctk.CTkLabel(
                self.result_card, text="Saved to soil_logs.json.", font=(_PanelColors.FONT_FAMILY, 11, "bold"),
                text_color=_PanelColors.PRIMARY
            )
            confirm.pack(anchor="w", padx=18, pady=(0, 10))
            self.after(2500, confirm.destroy)


# ---------------------------------------------------------------------------
# 7. STANDALONE SMOKE TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_pre = SoilReading(moisture_pct=72.0, rainfall_intensity_mm_hr=38.0,
                              stability_index=30.0, location="Sector 7 Hillside")
    sample_post = SoilReading(moisture_pct=45.0, rainfall_intensity_mm_hr=38.0,
                               stability_index=68.0, location="Sector 7 Hillside")

    print(generate_mitigation_report(sample_post, pre_cleanup=sample_pre))

    if CUSTOMTKINTER_AVAILABLE:
        ctk.set_appearance_mode("light")
        root = ctk.CTk()
        root.title("AI Soil & Landslide Risk Predictor — Standalone Preview")
        root.geometry("640x760")
        SoilRiskPanel(root, username="Demo User").pack(fill="both", expand=True)
        root.mainloop() 