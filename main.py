"""
main.py
-------
WastePulse — Application Entry Point / Orchestrator
"Smart Sensing, Smarter Living"

This is the single entry point for the whole WastePulse desktop app.
It does NOT reimplement any login/routing logic — authentycation.py's
`AuthPortal` already:

    1. Shows the login/register screen first.
    2. Verifies credentials against `users_db.json`.
    3. On success, safely closes the login screen and hands off:
         - Public users        -> waste_maneger.py (spawned as its own
                                    process, camera/OpenCV heavy).
         - SPCB Staff ('SAS-'/'SAW-') -> the embedded, role-aware
                                    dashboard view built from
                                    dashboard.py's components, shown
                                    in the SAME window.

`main.py`'s job is everything that needs to happen *before* that GUI
ever appears:

    - Resolve a PyInstaller-safe BASE_DIR using `sys.executable` when
      frozen and `__file__` otherwise, so `users_db.json`,
      `waste_logs.json`, `waste_mgmt.db`, and `logo.jpeg` are always
      found next to the running .exe / script — never inside a
      temp extraction folder.
    - Put BASE_DIR on sys.path and make it the working directory, so
      sibling module imports (`authentycation`, `dashboard`,
      `waste_maneger`, ...) and the relative paths those modules use
      internally (e.g. `subprocess.Popen([sys.executable,
      "waste_maneger.py"])`) resolve correctly regardless of how the
      app was launched (double-click, shortcut, `python main.py` from
      another folder, etc.).
    - Create any missing data files on first run so nothing downstream
      hits a FileNotFoundError / JSONDecodeError on a fresh install.
    - Verify third-party dependencies are installed, with a readable
      error instead of a raw ImportError traceback.
    - Install a global exception hook so an unexpected crash shows a
      real dialog (and gets logged) instead of a packaged .exe just
      vanishing with no explanation.

IMPORTANT CAVEAT (please read):
    authentycation.py already computes its OWN frozen-safe BASE_DIR
    the same way this file does. dashboard.py and waste_maneger.py,
    however, still compute their BASE_DIR as
    `os.path.dirname(os.path.abspath(__file__))`. That is fine when
    those files run as plain .py scripts (which is exactly how
    waste_maneger.py is launched — as a subprocess script, so its
    `__file__` is its real on-disk path). It is NOT fully reliable if
    dashboard.py is ever bundled and imported as a module inside a
    PyInstaller **onefile** build, because a bundled module's
    `__file__` then points inside the temporary `_MEIPASS` extraction
    folder rather than next to the .exe — so a file it writes there
    (e.g. `waste_logs.json`) would vanish once the process exits.
    main.py mitigates this as much as it can from the outside — by
    chdir'ing to BASE_DIR and exporting `WASTEPULSE_BASE_DIR` (and the
    specific file paths) as environment variables that any subprocess
    inherits — but it cannot rewrite `dashboard.py`'s own BASE_DIR
    line without modifying that file. If you ever bundle with
    `--onefile` and see dashboard data not persisting, the fix is a
    one-line change in dashboard.py:
        BASE_DIR = os.environ.get("WASTEPULSE_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
    (same pattern already used by main.py / authentycation.py).

Dependencies:
    pip install customtkinter pillow
"""

import os
import sys
import sqlite3
import logging
from typing import Optional

# NEW: Soil & Landslide Risk Predictor panel (SIH26001) — purely
# additive import. soil_ai.py is untouched; this line only
# makes SoilRiskPanel available to import from main.py. Falls back to
# None if the module (or customtkinter inside it) isn't available, so
# main.py still boots normally either way.
try:
    from soil_ai import SoilRiskPanel
except ImportError:
    SoilRiskPanel = None


# ---------------------------------------------------------------------------
# 1. FROZEN-SAFE BASE_DIR RESOLUTION (PyInstaller-safe)
# ---------------------------------------------------------------------------

def resolve_base_dir() -> str:
    """
    Returns the folder that should be treated as 'home' for all
    WastePulse data files and sibling module imports:

        - Frozen (.exe built by PyInstaller): the folder containing
          the actual executable (sys.executable), NOT the temporary
          _MEIPASS extraction folder — this is what keeps
          users_db.json / waste_mgmt.db / logo.jpeg resolvable and
          persistent next to the shipped .exe.
        - Running as a normal .py script: the folder containing this
          file (__file__), exactly as before.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = resolve_base_dir()

# Make sibling modules importable regardless of the current working
# directory the app happened to be launched from.
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Several existing modules build subprocess targets and asset lookups
# using bare relative names (e.g. "waste_maneger.py", "logo.jpeg").
# Those are only reliable if the process's CWD is BASE_DIR.
os.chdir(BASE_DIR)


# ---------------------------------------------------------------------------
# 2. SHARED ASSET / DATABASE PATHS
# ---------------------------------------------------------------------------

USERS_DB_PATH = os.path.join(BASE_DIR, "users_db.json")
WASTE_LOGS_PATH = os.path.join(BASE_DIR, "waste_logs.json")
WASTE_MGMT_DB_PATH = os.path.join(BASE_DIR, "waste_mgmt.db")
LOGO_PATH_CANDIDATES = tuple(
    os.path.join(BASE_DIR, name) for name in ("logo.jpeg", "logo.png", "logo.jpg")
)

# Exported so any subprocess this app spawns (waste_maneger.py is
# launched as a separate process by authentycation.py) — or any future
# edit to dashboard.py / waste_maneger.py — can read the exact same
# resolved BASE_DIR instead of recomputing it independently.
os.environ["WASTEPULSE_BASE_DIR"] = BASE_DIR
os.environ["WASTEPULSE_USERS_DB_PATH"] = USERS_DB_PATH
os.environ["WASTEPULSE_WASTE_LOGS_PATH"] = WASTE_LOGS_PATH
os.environ["WASTEPULSE_WASTE_MGMT_DB_PATH"] = WASTE_MGMT_DB_PATH


def ensure_data_files_exist() -> None:
    """
    Creates empty-but-valid data files on first run so downstream
    modules never hit a FileNotFoundError / JSONDecodeError just
    because this is a fresh install or a fresh .exe folder. Never
    overwrites a file that already exists.
    """
    if not os.path.exists(USERS_DB_PATH):
        with open(USERS_DB_PATH, "w", encoding="utf-8") as f:
            f.write("{}")

    if not os.path.exists(WASTE_LOGS_PATH):
        with open(WASTE_LOGS_PATH, "w", encoding="utf-8") as f:
            f.write("[]")

    if not os.path.exists(WASTE_MGMT_DB_PATH):
        # sqlite3.connect() alone creates a valid, empty SQLite file
        # on disk — no schema is assumed or created here, since this
        # file doesn't define one.
        conn = sqlite3.connect(WASTE_MGMT_DB_PATH)
        conn.close()


# ---------------------------------------------------------------------------
# 3. LOGGING + GLOBAL CRASH HANDLING
# ---------------------------------------------------------------------------

LOG_PATH = os.path.join(BASE_DIR, "wastepulse.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WastePulse.Main")


def install_global_exception_hook() -> None:
    """
    Ensures an unexpected crash anywhere in the app shows a friendly
    dialog and gets written to wastepulse.log, instead of a packaged
    .exe silently closing with no visible error at all.
    """
    def _handle_uncaught(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

        try:
            from tkinter import messagebox
            messagebox.showerror(
                "WastePulse — Unexpected Error",
                "WastePulse hit an unexpected error and needs to close.\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"Details were written to:\n{LOG_PATH}"
            )
        except Exception:
            # Tkinter may itself be unavailable this late in a crash —
            # fall back to stderr rather than raising a second error.
            print(f"WastePulse crashed: {exc_type.__name__}: {exc_value}", file=sys.stderr)

    sys.excepthook = _handle_uncaught


# ---------------------------------------------------------------------------
# 4. DEPENDENCY PRE-FLIGHT CHECK
# ---------------------------------------------------------------------------

REQUIRED_MODULES = ("customtkinter", "PIL")


def verify_dependencies() -> Optional[str]:
    """
    Returns None if all required third-party packages import cleanly;
    otherwise a human-readable message naming what's missing.
    """
    missing = []
    for module_name in REQUIRED_MODULES:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)

    if missing:
        return (
            "WastePulse is missing required packages: " + ", ".join(missing) + ".\n\n"
            "Install them with:\n    pip install customtkinter pillow"
        )
    return None


# ---------------------------------------------------------------------------
# 5. ORCHESTRATION — login screen first, then hand off to the app itself
# ---------------------------------------------------------------------------

def launch_application() -> None:
    """
    Boots the WastePulse desktop application end to end:

      1. Runs pre-flight checks (dependencies, data files).
      2. Opens authentycation.py's AuthPortal — the login/register
         screen — as the very first thing the user sees.
      3. AuthPortal itself (unmodified by this file) already handles
         everything after that:
             - verifying credentials against users_db.json,
             - safely closing the auth window on success,
             - routing Public users to waste_maneger.py (spawned as
               its own process), and
             - swapping SPCB Staff ('SAS-'/'SAW-') straight into the
               embedded, role-aware dashboard view (built from
               dashboard.py's components) inside the same window.

      main.py deliberately does not re-implement any of that routing —
      duplicating it here would risk the two copies drifting apart.
      Its only responsibilities are the environment guarantees above
      (paths, working directory, data files, dependencies) and making
      sure a crash anywhere in the app is visible instead of silent.
    """
    dependency_error = verify_dependencies()
    if dependency_error:
        try:
            from tkinter import messagebox
            messagebox.showerror("WastePulse — Missing Dependencies", dependency_error)
        except Exception:
            print(dependency_error, file=sys.stderr)
        sys.exit(1)

    ensure_data_files_exist()

    try:
        from authentycation import AuthPortal
    except ImportError as e:
        logger.critical("Could not import authentycation.py", exc_info=True)
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "WastePulse — Startup Error",
                f"Could not load the login screen (authentycation.py).\n\n{e}"
            )
        except Exception:
            print(f"Could not load authentycation.py: {e}", file=sys.stderr)
        sys.exit(1)

    logger.info(f"WastePulse starting. BASE_DIR={BASE_DIR}")
    app = AuthPortal()
    app.mainloop()
    logger.info("WastePulse exited normally.")


def main() -> None:
    install_global_exception_hook()
    launch_application()


if __name__ == "__main__":
    main()