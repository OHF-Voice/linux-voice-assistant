"""System monitoring and metrics for linux-voice-assistant."""

import asyncio
import logging
import psutil
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

_LOGGER = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    """System resource usage metrics."""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    thread_count: int
    open_files: int


@dataclass
class PerformanceMetrics:
    """Performance metrics for key operations."""
    wake_word_detections: int = 0
    wake_word_false_positives: int = 0
    audio_processing_errors: int = 0
    mqtt_reconnections: int = 0
    tts_playbacks: int = 0
    
    # Timing metrics (moving averages)
    avg_wake_word_latency_ms: float = 0.0
    avg_tts_latency_ms: float = 0.0
    
    # Recent error log
    recent_errors: List[str] = field(default_factory=list)


class SystemMonitor:
    """
    Monitors system resources and logs warnings for anomalies.
    
    Runs periodically in the background to track CPU, memory, threads,
    and file descriptors. Logs warnings when thresholds are exceeded.
    """
    
    def __init__(
        self,
        interval_seconds: float = 60.0,
        cpu_threshold: float = 80.0,
        memory_threshold: float = 80.0,
        max_history: int = 60
    ):
        """
        Initialize system monitor.
        
        Args:
            interval_seconds: How often to check system metrics
            cpu_threshold: Log warning if CPU usage exceeds this %
            memory_threshold: Log warning if memory usage exceeds this %
            max_history: Maximum number of metric snapshots to keep
        """
        self.interval = interval_seconds
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.max_history = max_history
        
        self.process = psutil.Process()
        self.history: List[SystemMetrics] = []
        self.performance = PerformanceMetrics()
        
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start monitoring in background."""
        if self._running:
            return
        
        self._running = True
        self._task = loop.create_task(self._monitor_loop())
        _LOGGER.info("System monitor started (interval=%.1fs)", self.interval)
    
    async def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _LOGGER.info("System monitor stopped")
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                metrics = self._collect_metrics()
                self._check_thresholds(metrics)
                self._add_to_history(metrics)
                
                # Log summary periodically (every 10 minutes)
                if len(self.history) % 10 == 0:
                    self._log_summary()
                    
            except Exception as e:
                _LOGGER.warning("Error in monitoring loop: %s", e)
            
            await asyncio.sleep(self.interval)
    
    def _collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        memory_info = self.process.memory_info()
        
        return SystemMetrics(
            timestamp=time.time(),
            cpu_percent=self.process.cpu_percent(interval=0.1),
            memory_percent=self.process.memory_percent(),
            memory_mb=memory_info.rss / (1024 * 1024),
            thread_count=self.process.num_threads(),
            open_files=len(self.process.open_files()),
        )
    
    def _check_thresholds(self, metrics: SystemMetrics) -> None:
        """Check metrics against thresholds and log warnings."""
        if metrics.cpu_percent > self.cpu_threshold:
            _LOGGER.warning(
                "High CPU usage: %.1f%% (threshold: %.1f%%)",
                metrics.cpu_percent,
                self.cpu_threshold
            )
        
        if metrics.memory_percent > self.memory_threshold:
            _LOGGER.warning(
                "High memory usage: %.1f%% (%.1f MB) (threshold: %.1f%%)",
                metrics.memory_percent,
                metrics.memory_mb,
                self.memory_threshold
            )
        
        # Warn about file descriptor leaks
        if metrics.open_files > 100:
            _LOGGER.warning(
                "High number of open files: %d. Possible resource leak.",
                metrics.open_files
            )
        
        # Warn about thread proliferation
        if metrics.thread_count > 50:
            _LOGGER.warning(
                "High number of threads: %d. Possible thread leak.",
                metrics.thread_count
            )
    
    def _add_to_history(self, metrics: SystemMetrics) -> None:
        """Add metrics to history, maintaining max size."""
        self.history.append(metrics)
        if len(self.history) > self.max_history:
            self.history.pop(0)
    
    def _log_summary(self) -> None:
        """Log summary statistics."""
        if not self.history:
            return
        
        recent = self.history[-10:] if len(self.history) >= 10 else self.history
        
        avg_cpu = sum(m.cpu_percent for m in recent) / len(recent)
        avg_memory = sum(m.memory_mb for m in recent) / len(recent)
        avg_threads = sum(m.thread_count for m in recent) / len(recent)
        
        _LOGGER.info(
            "System metrics summary: CPU=%.1f%%, Memory=%.1f MB, Threads=%.0f, "
            "Wake words=%d, TTS plays=%d, MQTT reconnects=%d",
            avg_cpu,
            avg_memory,
            avg_threads,
            self.performance.wake_word_detections,
            self.performance.tts_playbacks,
            self.performance.mqtt_reconnections,
        )
    
    def get_current_metrics(self) -> Optional[SystemMetrics]:
        """Get most recent metrics snapshot."""
        return self.history[-1] if self.history else None
    
    def get_metrics_summary(self) -> Dict[str, float]:
        """Get summary statistics as a dictionary."""
        if not self.history:
            return {}
        
        recent = self.history[-10:] if len(self.history) >= 10 else self.history
        
        return {
            "avg_cpu_percent": sum(m.cpu_percent for m in recent) / len(recent),
            "avg_memory_mb": sum(m.memory_mb for m in recent) / len(recent),
            "avg_memory_percent": sum(m.memory_percent for m in recent) / len(recent),
            "avg_threads": sum(m.thread_count for m in recent) / len(recent),
            "avg_open_files": sum(m.open_files for m in recent) / len(recent),
            "wake_word_detections": self.performance.wake_word_detections,
            "tts_playbacks": self.performance.tts_playbacks,
            "mqtt_reconnections": self.performance.mqtt_reconnections,
        }
    
    def record_wake_word_detection(self) -> None:
        """Record a wake word detection event."""
        self.performance.wake_word_detections += 1
    
    def record_tts_playback(self) -> None:
        """Record a TTS playback event."""
        self.performance.tts_playbacks += 1
    
    def record_mqtt_reconnection(self) -> None:
        """Record an MQTT reconnection event."""
        self.performance.mqtt_reconnections += 1
    
    def record_error(self, error_message: str) -> None:
        """Record an error for tracking."""
        self.performance.recent_errors.append(error_message)
        # Keep only last 20 errors
        if len(self.performance.recent_errors) > 20:
            self.performance.recent_errors.pop(0)
