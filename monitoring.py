"""
WEEK 4 - DAY 30: Monitoring and Metrics Module
===============================================
Sentry integration, performance metrics, and error tracking.

Features:
- Sentry error tracking
- Performance monitoring
- Custom metrics collector
- Request/response tracking
- Business metrics (jobs, credits, blocks)
"""

import os
import logging
import time
from typing import Dict, Optional, Any
from datetime import datetime
from functools import wraps
from dataclasses import dataclass, field
from enum import Enum

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Environment(str, Enum):
    """Deployment environment."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class MetricValue:
    """Container for a metric value with metadata."""
    value: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tags: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """
    Collects and tracks application metrics.
    
    Tracks:
    - Job submissions and completions
    - Credit transactions
    - Security blocks (age, celebrity)
    - Performance metrics
    """
    
    def __init__(self):
        """Initialize metrics collector."""
        self.metrics: Dict[str, int] = {
            # Job metrics
            "jobs_submitted": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_processing": 0,
            
            # Credit metrics
            "credits_consumed": 0,
            "credits_purchased": 0,
            "credits_refunded": 0,
            
            # Security metrics
            "age_blocks": 0,
            "celebrity_blocks": 0,
            "security_violations": 0,
            
            # Payment metrics
            "payments_received": 0,
            "payment_errors": 0,
            
            # Performance metrics
            "requests_total": 0,
            "requests_failed": 0,
        }
        
        self.timings: Dict[str, list] = {
            "job_submission": [],
            "job_completion": [],
            "age_verification": [],
            "celebrity_check": [],
        }
        
        self.start_time = datetime.utcnow()
        logger.info("MetricsCollector initialized")
    
    def increment(self, metric: str, value: int = 1):
        """
        Increment a counter metric.
        
        Args:
            metric: Metric name
            value: Increment value (default 1)
        """
        if metric in self.metrics:
            self.metrics[metric] += value
        else:
            logger.warning(f"Unknown metric: {metric}")
    
    def decrement(self, metric: str, value: int = 1):
        """
        Decrement a counter metric.
        
        Args:
            metric: Metric name
            value: Decrement value (default 1)
        """
        if metric in self.metrics:
            self.metrics[metric] = max(0, self.metrics[metric] - value)
        else:
            logger.warning(f"Unknown metric: {metric}")
    
    def record_timing(self, metric: str, duration_ms: float):
        """
        Record a timing metric.
        
        Args:
            metric: Metric name
            duration_ms: Duration in milliseconds
        """
        if metric in self.timings:
            self.timings[metric].append(duration_ms)
        else:
            logger.warning(f"Unknown timing metric: {metric}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get all current metrics.
        
        Returns:
            Dictionary of all metrics
        """
        # Calculate derived metrics
        uptime = (datetime.utcnow() - self.start_time).total_seconds()
        
        total_jobs = self.metrics["jobs_submitted"]
        success_rate = (
            (self.metrics["jobs_completed"] / total_jobs * 100)
            if total_jobs > 0 else 0
        )
        
        return {
            "counters": dict(self.metrics),
            "timings": {
                key: {
                    "count": len(values),
                    "avg": sum(values) / len(values) if values else 0,
                    "min": min(values) if values else 0,
                    "max": max(values) if values else 0
                }
                for key, values in self.timings.items()
            },
            "derived": {
                "uptime_seconds": uptime,
                "job_success_rate": round(success_rate, 2),
                "requests_per_second": self.metrics["requests_total"] / uptime if uptime > 0 else 0,
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def get_summary(self) -> str:
        """
        Get human-readable metrics summary.
        
        Returns:
            Formatted summary string
        """
        metrics = self.get_metrics()
        
        summary = []
        summary.append("=" * 70)
        summary.append("AppVideoAI Metrics Summary")
        summary.append("=" * 70)
        summary.append("")
        
        summary.append("Jobs:")
        summary.append(f"  Submitted: {metrics['counters']['jobs_submitted']}")
        summary.append(f"  Completed: {metrics['counters']['jobs_completed']}")
        summary.append(f"  Failed: {metrics['counters']['jobs_failed']}")
        summary.append(f"  Success Rate: {metrics['derived']['job_success_rate']}%")
        summary.append("")
        
        summary.append("Credits:")
        summary.append(f"  Consumed: {metrics['counters']['credits_consumed']}")
        summary.append(f"  Purchased: {metrics['counters']['credits_purchased']}")
        summary.append(f"  Refunded: {metrics['counters']['credits_refunded']}")
        summary.append("")
        
        summary.append("Security:")
        summary.append(f"  Age Blocks: {metrics['counters']['age_blocks']}")
        summary.append(f"  Celebrity Blocks: {metrics['counters']['celebrity_blocks']}")
        summary.append(f"  Total Violations: {metrics['counters']['security_violations']}")
        summary.append("")
        
        summary.append("System:")
        summary.append(f"  Uptime: {metrics['derived']['uptime_seconds']:.0f}s")
        summary.append(f"  Requests/sec: {metrics['derived']['requests_per_second']:.2f}")
        summary.append("")
        
        return "\n".join(summary)
    
    def reset(self):
        """Reset all metrics to zero."""
        for key in self.metrics:
            self.metrics[key] = 0
        
        for key in self.timings:
            self.timings[key].clear()
        
        self.start_time = datetime.utcnow()
        logger.info("Metrics reset")


# Global metrics instance
metrics = MetricsCollector()


def init_monitoring(
    environment: Environment = Environment.PRODUCTION,
    sentry_dsn: Optional[str] = None,
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1
):
    """
    Initialize Sentry monitoring.
    
    Args:
        environment: Deployment environment
        sentry_dsn: Sentry DSN (or from env SENTRY_DSN)
        traces_sample_rate: Fraction of transactions to trace (0-1)
        profiles_sample_rate: Fraction of transactions to profile (0-1)
    """
    dsn = sentry_dsn or os.getenv("SENTRY_DSN")
    
    if not dsn:
        logger.warning("SENTRY_DSN not configured. Error tracking disabled.")
        return
    
    try:
        # Configure logging integration
        logging_integration = LoggingIntegration(
            level=logging.INFO,        # Capture info and above as breadcrumbs
            event_level=logging.ERROR  # Send errors as events
        )
        
        sentry_sdk.init(
            dsn=dsn,
            environment=environment.value,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=[
                FastApiIntegration(),
                logging_integration,
            ],
            # Set release version
            release=os.getenv("APP_VERSION", "1.0.0"),
            
            # Add server name
            server_name=os.getenv("HOSTNAME", "appvideoai-server"),
            
            # Customize before send
            before_send=_before_send_handler,
        )
        
        logger.info(f"Sentry monitoring initialized (env: {environment.value})")
        
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")


def _before_send_handler(event: Dict, hint: Dict) -> Optional[Dict]:
    """
    Hook to modify events before sending to Sentry.
    
    Args:
        event: Sentry event dict
        hint: Additional context
    
    Returns:
        Modified event or None to drop
    """
    # Filter out specific errors you don't want to track
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        
        # Example: Don't send InsufficientCreditsError to Sentry (expected error)
        if exc_type.__name__ == "InsufficientCreditsError":
            return None
    
    # Add custom tags
    event.setdefault("tags", {})
    event["tags"]["app"] = "appvideoai"
    
    return event


def capture_exception(
    error: Exception,
    context: Optional[Dict] = None,
    level: str = "error"
):
    """
    Capture and send exception to Sentry with context.
    
    Args:
        error: Exception to capture
        context: Additional context dict
        level: Error level (error, warning, info)
    """
    with sentry_sdk.push_scope() as scope:
        if context:
            for key, value in context.items():
                scope.set_context(key, value)
        
        scope.level = level
        sentry_sdk.capture_exception(error)
        
        logger.error(f"Exception captured: {error}", exc_info=True)


def track_performance(metric_name: str):
    """
    Decorator to track function execution time.
    
    Args:
        metric_name: Name for the timing metric
    
    Usage:
        @track_performance("job_submission")
        def submit_job(...):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.time() - start_time) * 1000
                metrics.record_timing(metric_name, duration_ms)
                
                logger.debug(f"{func.__name__} took {duration_ms:.2f}ms")
        
        return wrapper
    return decorator


def track_request():
    """
    Decorator to track API requests.
    
    Usage:
        @app.get("/api/endpoint")
        @track_request()
        async def endpoint():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            metrics.increment("requests_total")
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                metrics.increment("requests_failed")
                capture_exception(e, context={"function": func.__name__})
                raise
        
        return wrapper
    return decorator


# ============================================================================
# Health Check Helpers
# ============================================================================

def get_system_health() -> Dict[str, Any]:
    """
    Get system health status.
    
    Returns:
        Health status dict
    """
    import psutil
    
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": os.getenv("APP_VERSION", "1.0.0"),
            "environment": os.getenv("ENVIRONMENT", "unknown"),
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3)
            },
            "metrics": metrics.get_metrics()
        }
    except ImportError:
        # psutil not installed
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": os.getenv("APP_VERSION", "1.0.0"),
            "metrics": metrics.get_metrics()
        }


# ============================================================================
# Usage Example
# ============================================================================

if __name__ == "__main__":
    print("Monitoring Module - Week 4, Day 30")
    print("=" * 60)
    
    # Initialize monitoring
    init_monitoring(Environment.DEVELOPMENT)
    
    # Simulate some metrics
    metrics.increment("jobs_submitted", 10)
    metrics.increment("jobs_completed", 8)
    metrics.increment("jobs_failed", 2)
    metrics.increment("credits_consumed", 150)
    
    metrics.record_timing("job_submission", 1250.5)
    metrics.record_timing("job_submission", 980.3)
    metrics.record_timing("age_verification", 450.2)
    
    # Display summary
    print(metrics.get_summary())
    
    # Test error capture
    try:
        raise ValueError("Test error for Sentry")
    except Exception as e:
        capture_exception(e, context={"test": "example"})
        print(f"\nCaptured exception: {e}")
