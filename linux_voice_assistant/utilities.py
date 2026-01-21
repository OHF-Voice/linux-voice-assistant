"""Utility functions for linux-voice-assistant."""

import logging
from typing import TypeVar, Union

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T', int, float)


def clamp_0_1(name: str, value: Union[float, str, object], default: float = 0.5) -> float:
    """
    Clamp a float value to [0.0, 1.0] with warnings.
    
    Args:
        name: Descriptive name for logging
        value: Value to clamp (will be converted to float)
        default: Default value if conversion fails
    
    Returns:
        Clamped float value in range [0.0, 1.0]
    """
    try:
        v = float(value)
    except (ValueError, TypeError):
        _LOGGER.warning(
            "%s is not a number (%r); using default %.2f",
            name,
            value,
            default
        )
        return float(default)

    if v < 0.0:
        _LOGGER.warning("%s < 0.0; clamping to 0.0 (was %s)", name, v)
        return 0.0
    if v > 1.0:
        _LOGGER.warning("%s > 1.0; clamping to 1.0 (was %s)", name, v)
        return 1.0
    return v


def clamp_0_100(name: str, value: Union[int, str, object], default: int = 100) -> int:
    """
    Clamp an int value to [0, 100] with warnings.
    
    Args:
        name: Descriptive name for logging
        value: Value to clamp (will be converted to int)
        default: Default value if conversion fails
    
    Returns:
        Clamped int value in range [0, 100]
    """
    try:
        v = int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        _LOGGER.warning(
            "%s is not an int (%r); using default %d",
            name,
            value,
            default
        )
        return int(default)

    if v < 0:
        _LOGGER.warning("%s < 0; clamping to 0 (was %s)", name, v)
        return 0
    if v > 100:
        _LOGGER.warning("%s > 100; clamping to 100 (was %s)", name, v)
        return 100
    return v


def clamp_value(value: T, minimum: T, maximum: T, name: str = "value") -> T:
    """
    Generic clamp function for any comparable type.
    
    Args:
        value: Value to clamp
        minimum: Minimum allowed value
        maximum: Maximum allowed value
        name: Descriptive name for logging
    
    Returns:
        Clamped value in range [minimum, maximum]
    """
    if value < minimum:
        _LOGGER.warning("%s < %s; clamping to %s (was %s)", name, minimum, minimum, value)
        return minimum
    if value > maximum:
        _LOGGER.warning("%s > %s; clamping to %s (was %s)", name, maximum, maximum, value)
        return maximum
    return value
