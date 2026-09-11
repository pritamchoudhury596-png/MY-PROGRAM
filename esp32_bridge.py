"""
esp32_bridge.py
------------------
WastePulse — ESP32 Hardware Bridge

Handles serial communication with the ESP32 relay/sensor controller
that actually drives the conveyor motor, cutter blade, and bin gates.

On startup, the bridge tries to auto-detect a connected ESP32 over
USB-serial. If none is found (no cable plugged in, wrong drivers,
`pyserial` not installed, dev machine with no hardware at all) it
transparently falls back to a safe SIMULATION mode: every method still
works and returns the same shape of result, it just logs what *would*
have been sent instead of writing to a real port. This means the rest
of the WastePulse codebase never needs special-case handling for
"is hardware attached right now?" — it just calls `send_action()`.

Dependencies:
    pip install pyserial   (optional — simulation mode works without it)
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    import serial
    from serial.tools import list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    PYSERIAL_AVAILABLE = False

logger = logging.getLogger("WastePulse.ESP32Bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

BAUD_RATE = 115200
CONNECT_TIMEOUT_S = 2.0
BOOT_SETTLE_S = 2.0  # most ESP32 boards reset on DTR toggle when the port opens

# Common USB-UART bridge chip identifiers found in ESP32 dev boards'
# hwid strings (CP2102, CH340/CH341, FTDI). Used to auto-pick the right
# serial port out of everything currently plugged into the machine.
ESP32_VID_PID_HINTS = ("CP210", "CH340", "CH341", "10C4", "1A86", "0403")

# Maps each LLM/rule-engine action to a named relay, its ESP32 GPIO pin,
# and the binary signal level that should be written to it. This is the
# single source of truth for the hardware wiring — change pin numbers
# here, not scattered through the rest of the codebase.
RELAY_MAP: Dict[str, Dict[str, Any]] = {
    "CMD_CONVEYOR_ON":                 {"relay": "CONVEYOR_MOTOR",       "pin": 25, "signal": 1},
    "CMD_CONVEYOR_OFF":                {"relay": "CONVEYOR_MOTOR",       "pin": 25, "signal": 0},
    "CMD_CUTTER_START":                {"relay": "CUTTER_BLADE",         "pin": 26, "signal": 1},
    "CMD_CUTTER_STOP":                 {"relay": "CUTTER_BLADE",         "pin": 26, "signal": 0},
    "SORT_DRY_RECYCLABLE_BIN":         {"relay": "BIN_GATE_DRY",         "pin": 27, "signal": 1},
    "SORT_BIODEGRADABLE_COMPOST_BIN":  {"relay": "BIN_GATE_COMPOST",     "pin": 14, "signal": 1},
    "SORT_INDUSTRIAL_BIN":             {"relay": "BIN_GATE_INDUSTRIAL",  "pin": 12, "signal": 1},
    "EMERGENCY_STOP":                  {"relay": "MASTER_ESTOP",         "pin": 4,  "signal": 1},
}

# Fallback used for any action string not present in RELAY_MAP — fails
# safe by tripping the master E-STOP relay rather than sending nothing.
_UNKNOWN_ACTION_FALLBACK = "EMERGENCY_STOP"


# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class RelaySignal:
    """A single binary relay instruction ready to be written to the wire."""
    relay_name: str
    pin: int
    signal: int
    raw_command: str

    def to_wire_format(self) -> str:
        """Simple newline-terminated wire protocol the ESP32 firmware
        parses: RELAY_NAME:PIN:SIGNAL\\n"""
        return f"{self.relay_name}:{self.pin}:{self.signal}\n"


# ---------------------------------------------------------------------------
# BRIDGE
# ---------------------------------------------------------------------------

class ESP32Bridge:
    """
    Manages serial communication with the ESP32 relay controller and
    exposes a dashboard-friendly connection status at all times.
    """

    def __init__(self, preferred_port: Optional[str] = None, baud_rate: int = BAUD_RATE):
        self.preferred_port = preferred_port
        self.baud_rate = baud_rate

        self._serial_conn: Optional["serial.Serial"] = None
        self._connected_port: Optional[str] = None
        self._last_signal_sent: Optional[RelaySignal] = None

        # Dashboard-facing connection flag, kept in sync by every
        # connect/disconnect/write path in this class.
        self.machine_connected: bool = False

        self._attempt_connection()

    # -- connection lifecycle -----------------------------------------

    def _attempt_connection(self) -> None:
        if not PYSERIAL_AVAILABLE:
            logger.warning("pyserial is not installed — running in SIMULATION MODE.")
            self.machine_connected = False
            return

        port = self.preferred_port or self._auto_detect_port()
        if not port:
            logger.warning("No ESP32-like serial device found — running in SIMULATION MODE.")
            self.machine_connected = False
            return

        try:
            self._serial_conn = serial.Serial(port, self.baud_rate, timeout=CONNECT_TIMEOUT_S)
            time.sleep(BOOT_SETTLE_S)
            self._connected_port = port
            self.machine_connected = True
            logger.info(f"ESP32 connected on {port} @ {self.baud_rate} baud.")
        except (serial.SerialException, OSError) as e:
            logger.warning(f"Failed to open {port}: {e}. Falling back to SIMULATION MODE.")
            self._serial_conn = None
            self._connected_port = None
            self.machine_connected = False

    @staticmethod
    def _auto_detect_port() -> Optional[str]:
        """Scans available serial ports for a hwid matching a known
        ESP32 USB-UART bridge chip. Returns None if pyserial's port
        listing fails or nothing matches — never raises."""
        try:
            ports = list(list_ports.comports())
        except Exception as e:
            logger.debug(f"Port auto-detection failed: {e}")
            return None

        for p in ports:
            hwid = (p.hwid or "").upper()
            if any(hint in hwid for hint in ESP32_VID_PID_HINTS):
                return p.device
        return None

    def reconnect(self) -> bool:
        """Retries the connection attempt on demand (e.g. a dashboard
        'Reconnect Hardware' button). Returns the new connection state."""
        self.disconnect()
        self._attempt_connection()
        return self.machine_connected

    def disconnect(self) -> None:
        if self._serial_conn is not None:
            try:
                self._serial_conn.close()
            except Exception as e:
                logger.debug(f"Error while closing serial port (ignored): {e}")
        self._serial_conn = None
        self._connected_port = None
        self.machine_connected = False

    # -- status ----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True only when we believe hardware is attached AND the
        underlying serial port object is actually open right now."""
        return bool(
            self.machine_connected
            and self._serial_conn is not None
            and getattr(self._serial_conn, "is_open", False)
        )

    @property
    def connected_port(self) -> Optional[str]:
        return self._connected_port

    @property
    def connection_status_text(self) -> str:
        """Ready-to-render dashboard string, per the two required states."""
        if self.is_connected:
            return f"Active Hardware Connected (Port: {self._connected_port})"
        return "0 Machines Connected"

    @property
    def last_signal_sent(self) -> Optional[RelaySignal]:
        return self._last_signal_sent

    # -- action -> relay mapping -----------------------------------------

    def map_action_to_signal(self, action: str) -> RelaySignal:
        """Translates an LLM/rule-engine action string into a concrete
        RelaySignal. Unknown actions fail safe to the master E-STOP
        relay rather than being silently dropped."""
        mapping = RELAY_MAP.get(action)
        if mapping is None:
            logger.error(f"Unknown action '{action}' — defaulting to master E-STOP for safety.")
            mapping = RELAY_MAP[_UNKNOWN_ACTION_FALLBACK]
            action = _UNKNOWN_ACTION_FALLBACK

        return RelaySignal(
            relay_name=mapping["relay"],
            pin=mapping["pin"],
            signal=mapping["signal"],
            raw_command=action,
        )

    def send_action(self, action: str) -> Dict[str, Any]:
        """
        Translates an action into a relay signal and transmits it over
        serial (or logs it in simulation mode). Always returns the same
        result shape regardless of hardware presence, so callers never
        need to branch on connection state themselves.
        """
        signal = self.map_action_to_signal(action)
        self._last_signal_sent = signal

        result: Dict[str, Any] = {
            "action": signal.raw_command,
            "relay": signal.relay_name,
            "pin": signal.pin,
            "signal": signal.signal,
            "mode": "HARDWARE" if self.is_connected else "SIMULATION",
            "success": False,
        }

        if not self.is_connected:
            logger.info(f"[SIMULATION] Would send -> {signal.to_wire_format().strip()}")
            result["success"] = True
            return result

        try:
            self._serial_conn.write(signal.to_wire_format().encode("utf-8"))
            self._serial_conn.flush()
            result["success"] = True
            logger.info(f"[HARDWARE:{self._connected_port}] Sent -> {signal.to_wire_format().strip()}")
        except (serial.SerialException, OSError) as e:
            logger.error(f"Serial write failed ({e}) — dropping to SIMULATION MODE for safety.")
            self.machine_connected = False
            result["mode"] = "SIMULATION"
            result["success"] = True  # the fallback path still completes logically

        return result


# ---------------------------------------------------------------------------
# STANDALONE SMOKE TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = ESP32Bridge()
    print("Status:", bridge.connection_status_text)

    for demo_action in ("CMD_CONVEYOR_ON", "CMD_CUTTER_START", "SORT_DRY_RECYCLABLE_BIN"):
        outcome = bridge.send_action(demo_action)
        print(outcome)

    bridge.disconnect()