"""Automatic cleanup utilities for linux-voice-assistant."""

import logging
import time
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)


def cleanup_old_downloads(
    download_dir: Path,
    max_age_days: int = 30,
    dry_run: bool = False
) -> int:
    """
    Remove old wake word downloads to prevent disk space accumulation.
    
    Args:
        download_dir: Directory containing external_wake_words subdirectory
        max_age_days: Remove files older than this many days
        dry_run: If True, only log what would be deleted
    
    Returns:
        Number of files removed
    """
    eww_dir = download_dir / "external_wake_words"
    if not eww_dir.exists():
        _LOGGER.debug("No external_wake_words directory to clean")
        return 0
    
    current_time = time.time()
    max_age_seconds = max_age_days * 24 * 3600
    files_removed = 0
    bytes_freed = 0
    
    try:
        for file_path in eww_dir.glob("*"):
            if not file_path.is_file():
                continue
            
            file_age = current_time - file_path.stat().st_mtime
            
            if file_age > max_age_seconds:
                file_size = file_path.stat().st_size
                
                if dry_run:
                    _LOGGER.info(
                        "Would remove old file: %s (age: %.1f days, size: %.1f KB)",
                        file_path.name,
                        file_age / 86400,
                        file_size / 1024
                    )
                else:
                    _LOGGER.info(
                        "Removing old file: %s (age: %.1f days, size: %.1f KB)",
                        file_path.name,
                        file_age / 86400,
                        file_size / 1024
                    )
                    file_path.unlink()
                    bytes_freed += file_size
                
                files_removed += 1
    
    except Exception as e:
        _LOGGER.error("Error during cleanup: %s", e, exc_info=True)
    
    if files_removed > 0 and not dry_run:
        _LOGGER.info(
            "Cleanup complete: removed %d files, freed %.1f MB",
            files_removed,
            bytes_freed / (1024 * 1024)
        )
    elif files_removed == 0:
        _LOGGER.debug("No old files to clean up")
    
    return files_removed


def cleanup_temp_files(base_dir: Path, pattern: str = "*.tmp") -> int:
    """
    Remove temporary files from a directory.
    
    Args:
        base_dir: Directory to search
        pattern: Glob pattern for temp files
    
    Returns:
        Number of files removed
    """
    files_removed = 0
    
    try:
        for file_path in base_dir.glob(pattern):
            if file_path.is_file():
                _LOGGER.debug("Removing temp file: %s", file_path)
                file_path.unlink()
                files_removed += 1
    except Exception as e:
        _LOGGER.error("Error removing temp files: %s", e)
    
    return files_removed


def check_disk_space(path: Path, min_free_mb: int = 100) -> bool:
    """
    Check if sufficient disk space is available.
    
    Args:
        path: Path to check disk space for
        min_free_mb: Minimum free space required in MB
    
    Returns:
        True if sufficient space available, False otherwise
    """
    try:
        import shutil
        stat = shutil.disk_usage(path)
        free_mb = stat.free / (1024 * 1024)
        
        if free_mb < min_free_mb:
            _LOGGER.warning(
                "Low disk space: %.1f MB free (minimum: %d MB)",
                free_mb,
                min_free_mb
            )
            return False
        
        return True
    except Exception as e:
        _LOGGER.error("Error checking disk space: %s", e)
        return True  # Assume OK if we can't check
