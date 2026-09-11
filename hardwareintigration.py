"""
hardware_integration.py
------------------------
WastePulse — Live Hardware Plant Engine (Backend Module)

This is the REAL-HARDWARE-ONLY replacement for the old simulator
(plant_simulator_dashboard.py). Every mock/random telemetry generator
has been removed. PlantEngine now drives its cycle loop exclusively
off real sensor reads coming from the ESP32 over Serial/USB (Moisture,
IR Sensor, Metal Detector) via ESP32Bridge (esp32_bridge.py).

If no ESP32 is connected, PlantEngine never fabricates telemetry or
moves a relay — it reports an honest, strictly zeroed/idle state:
    - 0 machines connected
    - "Idle / Disconnected" status text
    - zero-filled sensor history (flat-line graphs, not fake motion)
    - every relay indicator False

Swap in a different ESP32Bridge implementation (or extend this one) if
your firmware's serial protocol differs — the only hardware-facing
surface this file touches is `self.hardware` (an ESP32Bridge instance),
specifically `is_connected`, `connection_status_text`, `read_sensors()`,
`send_action()`, and `disconnect()`. If your bridge names these
differently, update `_read_live_telemetry()` below — that's the single
call site that talks to hardware for sensor data.

LLM: this engine talks to WastePulseLLM (llm_controller.py) using the
model tag "llama3.2:3b" exactly — keep that exact string everywhere the
model is referenced so dashboard.py's status badge and this engine
never drift out of sync.

Run standalone (headless, no GUI) for a quick connectivity smoke test:
    python hardware_integration.py
"""

import time
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Dict, List, Any, Optional, Deque

from llm_controller import WastePulseLLM, PlantDecision
from esp32_bridge import ESP32Bridge

# Structured service log only — no console dashboard rendering lives here.
logger = logging.getLogger("WastePulse.PlantEngine")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.setLevel(logging.WARNING)

STAGE_SEQUENCE = ("CONVEYOR_ACTIVE", "VISION_CHECK", "SENSOR_SCAN")

# Single source of truth for the model tag. dashboard.py's status badge
# text and this engine's get_llm_status_text() are both driven off this
# constant so the two files can never disagree on the model name.
DEFAULT_LLM_MODEL = "llama3.2:3b"

# Exact strings dashboard.py should display whenever no physical
# machine is connected — kept centralized here so both files agree.
DISCONNECTED_STATUS_TEXT = "0 Machines Connected / Idle / Disconnected"

CYCLE_DELAY_S = 1.0        # delay between stages while actively processing a real item
IDLE_POLL_DELAY_S = 1.0    # delay between connection/telemetry checks while idle

TELEMETRY_LOG_MAXLEN = 500
SENSOR_HISTORY_MAXLEN = 100
REASONING_QUEUE_MAXLEN = 200

# Relay indicator keys — MUST match dashboard.py's RELAY_INDICATORS keys
# exactly, since dashboard.py binds directly to get_relay_states().
RELAY_KEYS = ("conveyor_belt", "mechanical_cutter", "bin_compost",
              "bin_industrial", "bin_biohazard_estop")

RELAY_PIN_MAP = {
    "conveyor_belt": 25,
    "mechanical_cutter": 26,
    "bin_compost": None,
    "bin_industrial": None,
    "bin_biohazard_estop": None,
}


def _relay_name_to_indicator_key(relay_name: str) -> Optional[str]:
    """Maps a free-text relay/action name from ESP32Bridge.send_action()
    onto one of the fixed RELAY_KEYS the UI understands."""
    name = (relay_name or "").lower()
    if "conveyor" in name:
        return "conveyor_belt"
    if "cutter" in name or "blade" in name:
        return "mechanical_cutter"
    if "compost" in name or "wet" in name or "biodegradable" in name:
        return "bin_compost"
    if "industrial" in name or "dry" in name or "recycl" in name:
        return "bin_industrial"
    if "bio" in name or "hazard" in name or "estop" in name or "emergency" in name:
        return "bin_biohazard_estop"
    return None


def _signal_is_active(signal: Any) -> bool:
    """Normalizes whatever ESP32Bridge reports as a relay's 'signal'
    value (bool, 0/1, 'HIGH'/'LOW', 'ON'/'OFF', ...) into a plain bool."""
    if isinstance(signal, bool):
        return signal
    text = str(signal).strip().upper()
    return text in ("1", "HIGH", "ON", "TRUE", "ACTIVE")


def _read_live_telemetry(hardware: ESP32Bridge, stage: str) -> Optional[Dict[str, Any]]:
    """
    Pulls ONE real sensor snapshot from the ESP32 bridge for the given
    stage. Returns None whenever there's nothing legitimate to report
    (not connected, bridge doesn't support live reads, a read error, or
    connected-but-nothing-on-the-line) — callers MUST treat None as
    "stay idle", never as license to invent a reading.

    Expects ESP32Bridge.read_sensors() to return a dict shaped like:
        {
            "object_detected": bool,
            "moisture_level": float | None,   # % from the moisture probe
            "metal_detected": bool,
            "ir_anomaly": bool,
            "biohazard_detected": bool,
            "raw_classes": [...],              # optional, from vision unit
            "is_packaged": bool,               # optional
            "packaging_type": str,             # optional
        }
    If your esp32_bridge.py names this differently, update this one
    call site rather than scattering hardware calls elsewhere.
    """
    if not getattr(hardware, "is_connected", False):
        return None

    reader = getattr(hardware, "read_sensors", None)
    if reader is None:
        logger.warning("ESP32Bridge has no read_sensors(); treating as no data.")
        return None

    try:
        raw = reader()
    except Exception:
        logger.exception("ESP32Bridge.read_sensors() failed; treating as no data.")
        return None

    if not raw or not raw.get("object_detected"):
        return None

    return {
        "yolo_data": {
            "object_detected": True,
            "estimated_count": raw.get("estimated_count", 1),
            "raw_classes": raw.get("raw_classes", []),
        },
        "current_stage": stage,
        "vision_camera": {
            "is_packaged": raw.get("is_packaged", False),
            "packaging_type": raw.get("packaging_type", "none"),
        },
        "sensors": {
            "moisture_level": raw.get("moisture_level"),
            "metal_detected": bool(raw.get("metal_detected", False)),
            "ir_anomaly": bool(raw.get("ir_anomaly", False)),
            "biohazard_detected": bool(raw.get("biohazard_detected", False)),
        },
    }


class PlantEngine:
    """Headless orchestrator driven ENTIRELY by real ESP32 telemetry.

    Owns the LLM brain and the hardware bridge, runs its own background
    `threading.Thread`, and exposes thread-safe getters so a GUI (or
    anything else) can read live state without ever blocking the loop
    or being blocked by it.

    No mock data of any kind is generated anywhere in this class. When
    `self.hardware.is_connected` is False, or the bridge reports no
    object currently on the line, the engine sits idle: it does not
    advance an item, does not touch any relay, and reports the sensor
    histories as empty (which getters below zero-fill for display).
    """

    def __init__(self, model: str = DEFAULT_LLM_MODEL, esp32_port: Optional[str] = None,
                 cycle_delay: float = CYCLE_DELAY_S, idle_poll_delay: float = IDLE_POLL_DELAY_S,
                 num_items: Optional[int] = None, auto_start: bool = True):
        self._model_name = model
        self.brain = WastePulseLLM(model=model)
        self.hardware = ESP32Bridge(preferred_port=esp32_port)

        self.cycle_delay = cycle_delay
        self.idle_poll_delay = idle_poll_delay
        self.num_items = num_items  # None => run indefinitely until stop()

        self._lock = threading.RLock()
        self._telemetry_log: Deque[Dict[str, Any]] = deque(maxlen=TELEMETRY_LOG_MAXLEN)
        self._moisture_history: Deque[float] = deque(maxlen=SENSOR_HISTORY_MAXLEN)
        self._ir_history: Deque[float] = deque(maxlen=SENSOR_HISTORY_MAXLEN)
        self._metal_history: Deque[float] = deque(maxlen=SENSOR_HISTORY_MAXLEN)
        self._reasoning_queue: Deque[str] = deque(maxlen=REASONING_QUEUE_MAXLEN)
        self._relay_states: Dict[str, bool] = {key: False for key in RELAY_KEYS}
        self._latest_sensor_snapshot: Dict[str, Any] = {
            "moisture_level": None, "metal_detected": False,
            "biohazard_detected": False, "ir_anomaly": False,
        }

        self.item_counter = 0
        self.emergency_stopped = False
        self._last_used_fallback: Optional[bool] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if auto_start:
            self.start()

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Starts the background plant loop if it isn't already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, name="PlantEngineThread", daemon=True
            )
            self._thread.start()

    def stop(self, join: bool = False, timeout: float = 5.0) -> None:
        """Signals the background loop to stop. Safe to call from the
        GUI thread at any time."""
        self._stop_event.set()
        if join and self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        try:
            processed = 0
            while not self._stop_event.is_set():
                if self.num_items is not None and processed >= self.num_items:
                    break
                advanced = self._run_item_cycle()
                if advanced:
                    processed += 1
                    time.sleep(self.cycle_delay)
                else:
                    # Disconnected, or connected with nothing on the
                    # line yet — poll again shortly, don't spin the CPU.
                    time.sleep(self.idle_poll_delay)
        except Exception:
            logger.exception("PlantEngine background loop crashed unexpectedly.")
        finally:
            try:
                self.hardware.disconnect()
            except Exception:
                logger.exception("Error while disconnecting hardware bridge on shutdown.")

    # -- core cycle --------------------------------------------------------

    def _run_item_cycle(self) -> bool:
        """
        Attempts to advance ONE item through Ingestion -> Vision Check
        -> Sensor Scan using ONLY real ESP32 telemetry.

        Returns True if at least one stage was actually processed with
        real data (relays/sensors were legitimately updated); returns
        False if hardware is disconnected or nothing real was detected
        this tick — in which case NOTHING was touched or fabricated.
        """
        if not self.is_hardware_connected():
            with self._lock:
                # Strictly zeroed idle state — never invent activity.
                self._relay_states = {key: False for key in RELAY_KEYS}
            return False

        with self._lock:
            next_item_no = self.item_counter + 1

        advanced_any_stage = False
        for stage in STAGE_SEQUENCE:
            if self._stop_event.is_set():
                return advanced_any_stage

            telemetry = _read_live_telemetry(self.hardware, stage)
            if telemetry is None:
                # Connected, but nothing real to report for this stage —
                # stay idle rather than inventing a reading.
                return advanced_any_stage

            decision: PlantDecision = self.brain.decide(telemetry)
            hw_result = self.hardware.send_action(decision.next_action)

            entry = {
                "item": next_item_no,
                "stage": stage,
                "telemetry": telemetry,
                "decision": decision.to_dict(),
                "hardware_result": hw_result,
                "timestamp": datetime.now().isoformat(),
            }

            reasoning_line = (
                f"[Item #{next_item_no} | {stage}] action={decision.next_action} "
                f"status={decision.dashboard_status} :: {decision.reasoning}"
            )
            if decision.used_fallback:
                reasoning_line += "  (fallback rule engine)"

            with self._lock:
                if not advanced_any_stage:
                    self.item_counter = next_item_no
                advanced_any_stage = True

                self._telemetry_log.append(entry)

                sensors = telemetry.get("sensors", {})
                moisture = sensors.get("moisture_level")
                if moisture is not None:
                    self._moisture_history.append(float(moisture))
                self._ir_history.append(1.0 if sensors.get("ir_anomaly") else 0.0)
                self._metal_history.append(1.0 if sensors.get("metal_detected") else 0.0)
                self._latest_sensor_snapshot = dict(sensors)

                self._reasoning_queue.append(reasoning_line)
                self._last_used_fallback = bool(decision.used_fallback)

                indicator_key = _relay_name_to_indicator_key(hw_result.get("relay", ""))
                if indicator_key is not None:
                    for key in self._relay_states:
                        self._relay_states[key] = False
                    self._relay_states[indicator_key] = _signal_is_active(hw_result.get("signal"))

            if decision.dashboard_status == "RED_ALERT":
                with self._lock:
                    self.emergency_stopped = True
                    self._relay_states["bin_biohazard_estop"] = True
                return advanced_any_stage

        return advanced_any_stage

    # -- thread-safe getters — hardware ------------------------------------

    def is_hardware_connected(self) -> bool:
        try:
            return bool(getattr(self.hardware, "is_connected", False))
        except Exception:
            return False

    def get_hardware_status_text(self) -> str:
        if self.is_hardware_connected():
            try:
                status = self.hardware.connection_status_text
            except Exception:
                status = "Live Hardware Stream Active"
            return f"1 Machine Connected — {status}"
        return DISCONNECTED_STATUS_TEXT

    def get_relay_states(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self._relay_states)

    def get_relay_pin_map(self) -> Dict[str, Optional[int]]:
        return dict(RELAY_PIN_MAP)

    # -- thread-safe getters — LLM ------------------------------------------

    def get_llm_status_text(self) -> str:
        with self._lock:
            used_fallback = self._last_used_fallback
        if used_fallback is None:
            # No decision made yet — report the configured model as the
            # intended primary engine, never a fallback that hasn't happened.
            return f"LLM: {self._model_name} Active"
        if used_fallback:
            return "Fallback: Deterministic Rule Engine Active"
        return f"LLM: {self._model_name} Active"

    def get_latest_reasoning(self) -> Optional[str]:
        with self._lock:
            if self._reasoning_queue:
                return self._reasoning_queue.popleft()
            return None

    def get_all_pending_reasoning(self) -> List[str]:
        with self._lock:
            lines = list(self._reasoning_queue)
            self._reasoning_queue.clear()
            return lines

    # -- thread-safe getters — sensor telemetry -----------------------------
    # All three history getters zero-fill when there's no real data yet
    # (disconnected, or connected but nothing received) so the dashboard's
    # graphs render a flat line at 0 instead of fabricating motion or
    # showing nothing at all.

    def get_recent_moisture_readings(self, limit: int = 30) -> List[float]:
        with self._lock:
            history = list(self._moisture_history)
        if not history:
            return [0.0] * limit
        return history[-limit:]

    def get_recent_ir_readings(self, limit: int = 30) -> List[float]:
        with self._lock:
            history = list(self._ir_history)
        if not history:
            return [0.0] * limit
        return history[-limit:]

    def get_recent_metal_readings(self, limit: int = 30) -> List[float]:
        with self._lock:
            history = list(self._metal_history)
        if not history:
            return [0.0] * limit
        return history[-limit:]

    def get_latest_sensor_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            if not self.is_hardware_connected() or not self._telemetry_log:
                return {
                    "moisture_level": 0.0, "metal_detected": False,
                    "biohazard_detected": False, "ir_anomaly": False,
                }
            return dict(self._latest_sensor_snapshot)

    def get_item_counter(self) -> int:
        with self._lock:
            return self.item_counter

    def is_emergency_stopped(self) -> bool:
        with self._lock:
            return self.emergency_stopped

    def reset_emergency_stop(self) -> None:
        """Clears the emergency-stop flag and the biohazard/E-stop relay
        indicator so the line can be manually restarted after an
        operator has verified it's safe to resume."""
        with self._lock:
            self.emergency_stopped = False
            self._relay_states["bin_biohazard_estop"] = False


# ---------------------------------------------------------------------------
# STANDALONE HEADLESS CONNECTIVITY CHECK (no GUI, no mock data)
# ---------------------------------------------------------------------------

def main():
    engine = PlantEngine(model=DEFAULT_LLM_MODEL, esp32_port=None)
    try:
        while True:
            print(engine.get_hardware_status_text(), "| LLM:", engine.get_llm_status_text())
            time.sleep(2)
    except KeyboardInterrupt:
        engine.stop(join=True)


if __name__ == "__main__":
    main()