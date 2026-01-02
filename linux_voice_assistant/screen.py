"""Cross-platform screen control (WSL PowerShell and X11 xset)."""

import logging
import os
import subprocess

_LOGGER = logging.getLogger(__name__)

# Detect environment once at module load
_IS_WSL = False
try:
    with open("/proc/version", "r", encoding="utf-8") as f:
        _IS_WSL = "microsoft" in f.read().lower()
except Exception:
    pass


# ============================================================================
# WSL - Windows PowerShell Commands
# ============================================================================

def _wsl_screen_off(timeout: int = 10) -> bool:
    """Put Windows monitor to sleep via PowerShell after a 10-second delay.
    
    Args:
        timeout: Ignored for WSL (always uses 10 seconds)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        cmd = [
            "powershell.exe",
            "-Command",
            "Start-Sleep -Seconds 10; "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' "
            "-Name 'Win32SendMessage' -Namespace 'Win32' -PassThru)::SendMessage(0xffff, 0x0112, 0xf170, 2)"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception as e:
        _LOGGER.debug("Failed to turn screen off via WSL: %s", e)
        return False


def _wsl_screen_on() -> bool:
    """Wake Windows monitor and set 10-minute display timeout via PowerShell."""
    try:
        # First, wake the screen by simulating mouse movement
        wake_cmd = [
            "powershell.exe",
            "-Command",
            "$pos = [System.Windows.Forms.Cursor]::Position; "
            "[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($pos.X, $pos.Y + 1); "
            "[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($pos.X, $pos.Y)"
        ]
        result = subprocess.run(wake_cmd, capture_output=True, timeout=5)
        
        # Set display timeout to 10 minutes (600 seconds) using powercfg
        # This sets the AC power display timeout
        timeout_cmd = [
            "powershell.exe",
            "-Command",
            "powercfg /change monitor-timeout-ac 10"
        ]
        subprocess.run(timeout_cmd, capture_output=True, timeout=5)
        
        return result.returncode == 0
    except Exception as e:
        _LOGGER.debug("Failed to turn screen on via WSL: %s", e)
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
        subprocess.run(
            ["/usr/bin/xset", "dpms", str(timeout), str(timeout), str(timeout), "+dpms"],
            env=env,
            check=True,
            capture_output=True,
        )
        return True
    except Exception as e:
        _LOGGER.debug("Failed to set X11 screen timeout: %s", e)
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
        # Force screen on immediately
        subprocess.run(
            ["/usr/bin/xset", "dpms", "force", "on"],
            env=env,
            check=True,
            capture_output=True,
        )
        # Set stay-awake timeout (10 minutes)
        subprocess.run(
            ["/usr/bin/xset", "dpms", "600", "600", "600", "+dpms"],
            env=env,
            check=True,
            capture_output=True,
        )
        return True
    except Exception as e:
        _LOGGER.debug("Failed to wake X11 screen: %s", e)
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
