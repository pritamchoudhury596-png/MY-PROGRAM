"""
llm_controller.py
-------------------
WastePulse — Autonomous Plant Supervisor (LLM Decision Layer)

Wraps a local Ollama-hosted Llama 3.2 model as the sequential
state-machine "brain" for the industrial waste sorting plant. Given a
full telemetry payload (YOLO vision, packaging camera, 3-layer sensor
array, current stage), it returns a strict, schema-validated JSON
hardware directive.

If Ollama is unreachable, not installed, or the model returns malformed
output, control silently drops to a deterministic rule-based fallback
engine that implements the exact same priority rules in plain Python —
so a flaky LLM call can never leave the plant without a safe next
action (an industrial line should never block, or worse, act on
garbage output, just because an inference call failed).

Dependencies:
    pip install ollama
    (requires a running local Ollama daemon: `ollama pull llama3.2`)
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

logger = logging.getLogger("WastePulse.LLMController")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "llama3.2:3b"
REQUEST_TIMEOUT_S = 15

VALID_ACTIONS = (
    "CMD_CONVEYOR_ON",
    "CMD_CONVEYOR_OFF",
    "CMD_CUTTER_START",
    "CMD_CUTTER_STOP",
    "SORT_BIODEGRADABLE_COMPOST_BIN",
    "SORT_DRY_RECYCLABLE_BIN",
    "SORT_INDUSTRIAL_BIN",
    "EMERGENCY_STOP",
)

VALID_DASHBOARD_STATUS = ("NORMAL", "WARNING", "RED_ALERT")

REQUIRED_KEYS = {"next_action", "target_hardware", "reasoning", "dashboard_status"}

MOISTURE_THRESHOLD = 60.0

SYSTEM_PROMPT = """You are the central autonomous AI Brain of an industrial waste sorting \
and processing plant called WastePulse. You orchestrate sequential hardware states from \
YOLO vision, a packaging camera, and a 3-layer sensor suite (metal, IR, moisture, biohazard) \
routed through an ESP32 bridge.

You will receive a JSON telemetry payload with this shape:
{
  "yolo_data": {"object_detected": bool, "estimated_count": int, "raw_classes": [...]},
  "current_stage": "CONVEYOR_ACTIVE" | "VISION_CHECK" | "SENSOR_SCAN",
  "vision_camera": {"is_packaged": bool, "packaging_type": "plastic"|"cardboard"|"none"},
  "sensors": {
    "moisture_level": float,
    "metal_detected": bool,
    "ir_anomaly": bool,
    "biohazard_detected": bool
  }
}

Apply these rules IN THIS EXACT PRIORITY ORDER:

1. PRIORITY OVERRIDE (any stage): if sensors.biohazard_detected is true, or a hazardous
   chemical anomaly is otherwise indicated -> next_action = "EMERGENCY_STOP",
   dashboard_status = "RED_ALERT". This overrides every other rule below.

2. Stage "CONVEYOR_ACTIVE" (Ingestion): if yolo_data.object_detected is true ->
   next_action = "CMD_CONVEYOR_ON" to start the belt.

3. Stage "VISION_CHECK" (Packaging Check): if vision_camera.is_packaged is true ->
   next_action = "CMD_CUTTER_START" to rip open the packaging. Once cutting is confirmed
   complete -> next_action = "CMD_CUTTER_STOP". If not packaged, treat cutting as already
   complete and issue "CMD_CUTTER_STOP".

4. Stage "SENSOR_SCAN" (Sensor Fusion Layer), evaluated in this exact order:
     a. sensors.metal_detected is true -> "SORT_INDUSTRIAL_BIN"
     b. else sensors.moisture_level > 60.0 -> "SORT_BIODEGRADABLE_COMPOST_BIN"
     c. else -> "SORT_DRY_RECYCLABLE_BIN"

dashboard_status is "RED_ALERT" only for the biohazard override, "WARNING" for any
sensor anomaly worth flagging but not stopping the line (e.g. ir_anomaly true without
biohazard), and "NORMAL" otherwise.

Respond with STRICT JSON ONLY. No markdown code fences, no commentary, no explanation
outside the JSON object. The object must have EXACTLY these keys:
{
  "next_action": "<one of: CMD_CONVEYOR_ON, CMD_CONVEYOR_OFF, CMD_CUTTER_START, \
CMD_CUTTER_STOP, SORT_BIODEGRADABLE_COMPOST_BIN, SORT_DRY_RECYCLABLE_BIN, \
SORT_INDUSTRIAL_BIN, EMERGENCY_STOP>",
  "target_hardware": "<short ESP32 pin/module identifier>",
  "reasoning": "<short technical log, one or two sentences>",
  "dashboard_status": "<NORMAL, WARNING, or RED_ALERT>"
}
"""


# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class PlantDecision:
    """A single validated hardware directive returned by the brain,
    whether it came from the LLM or the deterministic fallback."""
    next_action: str
    target_hardware: str
    reasoning: str
    dashboard_status: str
    used_fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class LLMControllerError(Exception):
    """Raised internally for LLM call/parse failures; always caught by
    WastePulseLLM.decide() before it reaches the caller."""


# ---------------------------------------------------------------------------
# MAIN CONTROLLER
# ---------------------------------------------------------------------------

class WastePulseLLM:
    """
    LLM-backed decision brain for the WastePulse plant. Every call to
    `decide()` returns a validated PlantDecision — either genuinely
    produced by the model, or, if anything about that call fails,
    produced by the deterministic rule engine below (with
    `used_fallback=True` so callers/dashboards can surface that fact).
    """

    def __init__(self, model: str = DEFAULT_MODEL, host: Optional[str] = None):
        self.model = model
        self.host = host
        self._client = None

        if OLLAMA_AVAILABLE:
            try:
                self._client = ollama.Client(host=host) if host else ollama
            except Exception as e:
                logger.warning(f"Could not initialize Ollama client: {e}. LLM calls will fall back.")
                self._client = None
        else:
            logger.warning(
                "The 'ollama' package is not installed — WastePulseLLM will run entirely on "
                "the deterministic fallback engine. Install with `pip install ollama`."
            )

    # -- public API -----------------------------------------------------

    def decide(self, telemetry: Dict[str, Any]) -> PlantDecision:
        """
        Returns a validated PlantDecision for the given telemetry
        payload. Tries the LLM first (when available); falls back to
        the deterministic rule engine on any failure, timeout, or
        malformed/invalid model output.
        """
        if self._client is not None:
            try:
                raw_text = self._query_model(telemetry)
                decision = self._parse_and_validate(raw_text)
                if decision is not None:
                    return decision
                logger.warning("LLM response failed schema validation — using fallback engine.")
            except Exception as e:
                logger.warning(f"LLM call failed ({type(e).__name__}: {e}) — using fallback engine.")

        return self._deterministic_fallback(telemetry)

    # -- LLM plumbing -----------------------------------------------------

    def _query_model(self, telemetry: Dict[str, Any]) -> str:
        response = self._client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(telemetry, indent=2)},
            ],
            options={"temperature": 0},
            format="json",
        )
        try:
            return response["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMControllerError(f"Unexpected Ollama response shape: {e}") from e

    def _parse_and_validate(self, raw_text: str) -> Optional[PlantDecision]:
        json_str = self._extract_json(raw_text)
        if json_str is None:
            return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict) or not REQUIRED_KEYS.issubset(data.keys()):
            return None
        if data["next_action"] not in VALID_ACTIONS:
            return None
        if data["dashboard_status"] not in VALID_DASHBOARD_STATUS:
            return None
        if not isinstance(data["target_hardware"], str) or not isinstance(data["reasoning"], str):
            return None

        return PlantDecision(
            next_action=data["next_action"],
            target_hardware=data["target_hardware"],
            reasoning=data["reasoning"],
            dashboard_status=data["dashboard_status"],
            used_fallback=False,
        )

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Pulls a JSON object out of the model's raw text, tolerating
        markdown code fences or stray commentary around it."""
        if not text:
            return None
        text = text.strip()

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1)

        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0)

        return None

    # -- deterministic safety-net fallback ---------------------------------

    def _deterministic_fallback(self, telemetry: Dict[str, Any]) -> PlantDecision:
        """
        Pure-Python re-implementation of the exact same priority rules
        the system prompt describes. This is what actually keeps the
        line running (safely) whenever the model is unavailable or
        misbehaves — it is NOT a lesser/approximate version of the
        logic, it is the same logic, just guaranteed deterministic.
        """
        sensors = telemetry.get("sensors", {}) or {}
        yolo = telemetry.get("yolo_data", {}) or {}
        vision = telemetry.get("vision_camera", {}) or {}
        stage = telemetry.get("current_stage")

        # Priority 1: biohazard override, applies regardless of stage.
        if sensors.get("biohazard_detected"):
            return PlantDecision(
                next_action="EMERGENCY_STOP",
                target_hardware="ESP32_RELAY_MASTER_ESTOP",
                reasoning="[FALLBACK ENGINE] biohazard_detected=True — emergency stop takes "
                          "priority over all other stage logic.",
                dashboard_status="RED_ALERT",
                used_fallback=True,
            )

        # Stage 1: Ingestion.
        if stage == "CONVEYOR_ACTIVE":
            if yolo.get("object_detected"):
                count = yolo.get("estimated_count", 0)
                classes = yolo.get("raw_classes", [])
                return PlantDecision(
                    next_action="CMD_CONVEYOR_ON",
                    target_hardware="ESP32_RELAY_CONVEYOR_MOTOR",
                    reasoning=f"[FALLBACK ENGINE] YOLO detected {count} object(s) "
                              f"({classes}) — starting conveyor.",
                    dashboard_status="NORMAL",
                    used_fallback=True,
                )
            return PlantDecision(
                next_action="CMD_CONVEYOR_OFF",
                target_hardware="ESP32_RELAY_CONVEYOR_MOTOR",
                reasoning="[FALLBACK ENGINE] No object detected by YOLO — holding conveyor off.",
                dashboard_status="NORMAL",
                used_fallback=True,
            )

        # Stage 2: Packaging / Vision Check.
        if stage == "VISION_CHECK":
            if vision.get("is_packaged"):
                packaging_type = vision.get("packaging_type", "unknown")
                return PlantDecision(
                    next_action="CMD_CUTTER_START",
                    target_hardware="ESP32_GPIO_CUTTER_RELAY",
                    reasoning=f"[FALLBACK ENGINE] Packaging detected (type={packaging_type}) — "
                              f"starting cutter blade.",
                    dashboard_status="NORMAL",
                    used_fallback=True,
                )
            return PlantDecision(
                next_action="CMD_CUTTER_STOP",
                target_hardware="ESP32_GPIO_CUTTER_RELAY",
                reasoning="[FALLBACK ENGINE] No packaging detected (or cut already complete) — "
                          "cutter stays/returns to stopped.",
                dashboard_status="NORMAL",
                used_fallback=True,
            )

        # Stage 3: Sensor Fusion Layer.
        if stage == "SENSOR_SCAN":
            dashboard_status = "WARNING" if sensors.get("ir_anomaly") else "NORMAL"

            if sensors.get("metal_detected"):
                return PlantDecision(
                    next_action="SORT_INDUSTRIAL_BIN",
                    target_hardware="ESP32_RELAY_BIN_GATE_INDUSTRIAL",
                    reasoning="[FALLBACK ENGINE] metal_detected=True — routing to industrial bin.",
                    dashboard_status=dashboard_status,
                    used_fallback=True,
                )

            moisture = sensors.get("moisture_level", 0.0) or 0.0
            if moisture > MOISTURE_THRESHOLD:
                return PlantDecision(
                    next_action="SORT_BIODEGRADABLE_COMPOST_BIN",
                    target_hardware="ESP32_RELAY_BIN_GATE_COMPOST",
                    reasoning=f"[FALLBACK ENGINE] moisture_level={moisture} > "
                              f"{MOISTURE_THRESHOLD} — routing to compost bin.",
                    dashboard_status=dashboard_status,
                    used_fallback=True,
                )

            return PlantDecision(
                next_action="SORT_DRY_RECYCLABLE_BIN",
                target_hardware="ESP32_RELAY_BIN_GATE_DRY",
                reasoning=f"[FALLBACK ENGINE] No metal, moisture_level={moisture} <= "
                          f"{MOISTURE_THRESHOLD} — default dry-recyclable classification.",
                dashboard_status=dashboard_status,
                used_fallback=True,
            )

        # Unknown/missing stage: fail safe rather than fail silent.
        return PlantDecision(
            next_action="EMERGENCY_STOP",
            target_hardware="ESP32_RELAY_MASTER_ESTOP",
            reasoning=f"[FALLBACK ENGINE] Unrecognized or missing current_stage "
                      f"({stage!r}) — halting for operator review.",
            dashboard_status="RED_ALERT",
            used_fallback=True,
        )


# ---------------------------------------------------------------------------
# STANDALONE SMOKE TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_payload = {
        "yolo_data": {"object_detected": True, "estimated_count": 1, "raw_classes": ["plastic_bag"]},
        "current_stage": "VISION_CHECK",
        "vision_camera": {"is_packaged": True, "packaging_type": "plastic"},
        "sensors": {
            "moisture_level": 45.0,
            "metal_detected": False,
            "ir_anomaly": False,
            "biohazard_detected": False,
        },
    }

    brain = WastePulseLLM()
    result = brain.decide(sample_payload)
    print(result.to_json())