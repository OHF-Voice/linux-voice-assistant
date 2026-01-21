"""Error handling utilities for linux-voice-assistant."""

import logging
from typing import Optional, Callable, Any
from functools import wraps
import time

_LOGGER = logging.getLogger(__name__)


class RecoverableError(Exception):
    """Base class for errors that can be recovered from."""
    pass


class FatalError(Exception):
    """Base class for errors that require shutdown."""
    pass


class AudioDeviceError(RecoverableError):
    """Audio device temporarily unavailable."""
    pass


class NetworkError(RecoverableError):
    """Network operation failed, can be retried."""
    pass


class HardwareError(FatalError):
    """Hardware initialization failed."""
    pass


class ConfigurationError(FatalError):
    """Configuration is invalid."""
    pass


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry a function with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch and retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        _LOGGER.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            func.__name__,
                            attempt + 1,
                            max_retries + 1,
                            e,
                            delay,
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                    else:
                        _LOGGER.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_retries + 1,
                            e,
                            exc_info=True,
                        )
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


async def async_retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Async decorator to retry a coroutine with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch and retry
    """
    import asyncio
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        _LOGGER.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            func.__name__,
                            attempt + 1,
                            max_retries + 1,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                    else:
                        _LOGGER.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_retries + 1,
                            e,
                            exc_info=True,
                        )
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def handle_error_safely(
    error: Exception,
    context: str,
    fatal_types: tuple = (FatalError, KeyboardInterrupt, SystemExit),
    recoverable_types: tuple = (RecoverableError,)
) -> bool:
    """
    Categorize and log errors appropriately.
    
    Args:
        error: The exception that occurred
        context: Description of what was being done
        fatal_types: Tuple of exception types considered fatal
        recoverable_types: Tuple of exception types that are recoverable
    
    Returns:
        True if error is recoverable, False if fatal
    """
    if isinstance(error, fatal_types):
        _LOGGER.critical(
            "Fatal error in %s: %s",
            context,
            error,
            exc_info=True,
        )
        return False
    elif isinstance(error, recoverable_types):
        _LOGGER.warning(
            "Recoverable error in %s: %s",
            context,
            error,
        )
        return True
    else:
        # Unknown error type - log with full trace for debugging
        _LOGGER.error(
            "Unexpected error in %s: %s",
            context,
            error,
            exc_info=True,
        )
        return True  # Assume recoverable by default
