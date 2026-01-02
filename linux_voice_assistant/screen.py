"""Cross-platform screen control (WSL PowerShell and X11 xset)."""

import logging
import os
import shutil
import subprocess

_LOGGER = logging.getLogger(__name__)

# Detect environment once at module load
_IS_WSL = False
try:
    with open("/proc/version", "r", encoding="utf-8") as f:
        _IS_WSL = "microsoft" in f.read().lower()
except Exception:
    pass

# Locate PowerShell on WSL
_POWERSHELL = shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


# ============================================================================
# WSL - Windows PowerShell Commands
# ============================================================================

def _wsl_screen_off(timeout: int = 10) -> bool:
    """Put Windows monitor to sleep via PowerShell DLL import after a 10-second delay.
    
    Args:
        timeout: Ignored for WSL (always uses 10 seconds)
    
    Returns:
        True if successful, False otherwise
    """
    if not _POWERSHELL:
        _LOGGER.warning("WSL screen off failed: powershell.exe not found")
        return False
    try:
        # Sleep then send monitor off via DLL import (simpler, works reliably)
        cmd = [
            _POWERSHELL,
            "-NoProfile",
            "-Command",
            "Start-Sleep -Seconds 10; "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' "
            "-Name 'Win32SendMessage' -Namespace 'Win32' -PassThru)::SendMessage(0xffff, 0x0112, 0xf170, 2)"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=25)
        if result.returncode != 0:
            _LOGGER.warning(
                "WSL screen off failed (rc=%s): %s %s",
                result.returncode,
                result.stdout.decode(errors="ignore").strip(),
                result.stderr.decode(errors="ignore").strip(),
            )
            return False
        _LOGGER.info("WSL screen off command succeeded")
        return True
    except Exception as e:
        _LOGGER.warning("Failed to turn screen off via WSL: %s", e)
        return False


def _wsl_screen_on() -> bool:
    """Set 10-minute display timeout to prevent sleep during voice interaction.
    
    Note: Automatic screen wake not supported on this system. User must manually 
    wake screen (move mouse or press key) before speaking. This function prevents 
    screen sleep during the conversation once wake word is detected.
    
    Returns:
        True if successful, False otherwise
    """
    if not _POWERSHELL:
        _LOGGER.warning("WSL screen timeout failed: powershell.exe not found")
        return False
    try:
        # Set display timeout to 10 minutes (prevents sleep during interaction)
        timeout_cmd = [
            _POWERSHELL,
            "-NoProfile",
            "-Command",
            "powercfg /change monitor-timeout-ac 10"
        ]
        timeout_result = subprocess.run(timeout_cmd, capture_output=True, timeout=5)
        if timeout_result.returncode != 0:
            _LOGGER.warning(
                "WSL set timeout failed (rc=%s): %s %s",
                timeout_result.returncode,
                timeout_result.stdout.decode(errors="ignore").strip(),
                timeout_result.stderr.decode(errors="ignore").strip(),
            )
            return False
        
        _LOGGER.info("WSL display timeout set to 10 minutes (manual wake required)")
        return True
    except Exception as e:
        _LOGGER.warning("Failed to set WSL screen timeout: %s", e)
        return False


# ============================================================================
# X11 - xset DPMS Commands
# ============================================================================

def _x11_screen_off(timeout: int = 10, display: str = ":0") -> bool:
    """Turn off screen after timeout using X11 DPMS.
    
    Args:
        timeout: Seconds until screen turns off
        display: X display to target
    
    Returns:
        True if successful, False otherwise
    """
    try:
        env = os.environ.copy()
        env["DISPLAY"] = display
        xset_path = shutil.which("xset") or "/usr/bin/xset"
        result = subprocess.run(
            [xset_path, "dpms", str(timeout), str(timeout), str(timeout), "+dpms"],
            env=env,
            capture_output=True,
        )
        if result.returncode != 0:
            _LOGGER.warning(
                "X11 screen off failed (rc=%s): %s %s",
                result.returncode,
                result.stdout.decode(errors="ignore").strip(),
                result.stderr.decode(errors="ignore").strip(),
            )
            return False
        _LOGGER.info("X11 screen timeout set to %s seconds on display %s", timeout, display)
        return True
    except Exception as e:
        _LOGGER.warning("Failed to set X11 screen timeout: %s", e)
        return False


def _x11_screen_on(display: str = ":0") -> bool:
    """Wake screen immediately using X11 DPMS.
    
    Args:
        display: X display to target
    
    Returns:
        True if successful, False otherwise
    """
    try:
        env = os.environ.copy()
        env["DISPLAY"] = display
        xset_path = shutil.which("xset") or "/usr/bin/xset"
        # Force screen on immediately
        on_result = subprocess.run(
            [xset_path, "dpms", "force", "on"],
            env=env,
            capture_output=True,
        )
        if on_result.returncode != 0:
            _LOGGER.warning(
                "X11 screen on failed (rc=%s): %s %s",
                on_result.returncode,
                on_result.stdout.decode(errors="ignore").strip(),
                on_result.stderr.decode(errors="ignore").strip(),
            )
            return False
        # Set stay-awake timeout (10 minutes)
        timeout_result = subprocess.run(
            [xset_path, "dpms", "600", "600", "600", "+dpms"],
            env=env,
            capture_output=True,
        )
        if timeout_result.returncode != 0:
            _LOGGER.warning(
                "X11 screen timeout set failed (rc=%s): %s %s",
                timeout_result.returncode,
                timeout_result.stdout.decode(errors="ignore").strip(),
                timeout_result.stderr.decode(errors="ignore").strip(),
            )
            return False
        _LOGGER.info("X11 screen on and timeout set (10 minutes) on display %s", display)
        return True
    except Exception as e:
        _LOGGER.warning("Failed to wake X11 screen: %s", e)
        return False


# ============================================================================
# Unified Public Interface
# ============================================================================

def screen_off(timeout: int = 10, display: str = ":0") -> bool:
    """Turn screen off (cross-platform).
    
    Args:
        timeout: Seconds until screen turns off (delay on WSL, DPMS setting on X11)
        display: X display to target (X11 only)
    
    Returns:
        True if successful, False otherwise
    """
    _LOGGER.info("Screen off requested (wsl=%s, timeout=%s, display=%s)", _IS_WSL, timeout, display)
    if _IS_WSL:
        return _wsl_screen_off(timeout)
    else:
        return _x11_screen_off(timeout, display)


def screen_on(display: str = ":0") -> bool:
    """Wake screen immediately (cross-platform).
    
    Args:
        display: X display to target (X11 only)
    
    Returns:
        True if successful, False otherwise
    """
    _LOGGER.info("Screen on requested (wsl=%s, display=%s)", _IS_WSL, display)
    if _IS_WSL:
        return _wsl_screen_on()
    else:
        return _x11_screen_on(display)


def is_wsl() -> bool:
    """Check if running under WSL.
    
    Returns:
        True if WSL environment detected, False otherwise
    """
    return _IS_WSL
