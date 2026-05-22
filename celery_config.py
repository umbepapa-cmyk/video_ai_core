"""
PHASE 2 SPRINT 1: Celery Configuration
=======================================
Redis-backed Celery configuration for async video generation.

Configuration includes:
- Redis broker and result backend
- Task routing and queues
- Worker settings for optimal performance
- Task execution limits and timeouts
"""

import os
from kombu import Queue

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Celery broker and backend
broker_url = REDIS_URL
result_backend = REDIS_URL

# Serialization (JSON for security and compatibility)
task_serializer = 'json'
accept_content = ['json']
result_serializer = 'json'
timezone = 'UTC'
enable_utc = True

# Result backend settings
result_expires = 3600  # Results expire after 1 hour
result_persistent = True  # Persist results to Redis
result_extended = True  # Store task args/kwargs in result

# Task routing
task_routes = {
    'tasks.generate_video_task': {'queue': 'video_generation'},
    'tasks.debug_task': {'queue': 'default'},
    'tasks.cleanup_task': {'queue': 'maintenance'}
}

# Define queues
task_queues = (
    Queue('default', routing_key='default'),
    Queue('video_generation', routing_key='video.#'),
    Queue('maintenance', routing_key='maintenance.#'),
)

# Worker settings
worker_prefetch_multiplier = 1  # One task at a time per worker (important for GPU tasks)
worker_max_tasks_per_child = 10  # Restart worker after 10 tasks (prevents memory leaks)
worker_max_memory_per_child = 2000000  # 2GB limit before restart

# Task execution settings
task_acks_late = True  # Acknowledge task after completion (not on receipt)
task_reject_on_worker_lost = True  # Reject task if worker dies
task_time_limit = 600  # 10 minutes hard limit (SIGKILL)
task_soft_time_limit = 540  # 9 minutes soft limit (exception raised)

# Retry settings
task_default_max_retries = 3
task_default_retry_delay = 60  # Wait 60 seconds before retry

# Beat scheduler (for periodic tasks)
beat_schedule = {
    'cleanup-expired-jobs': {
        'task': 'tasks.cleanup_task',
        'schedule': 3600.0,  # Run every hour
        'options': {'queue': 'maintenance'}
    }
}

# Logging
worker_log_format = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'
worker_task_log_format = '[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s'

# Event settings (for Flower monitoring)
worker_send_task_events = True
task_send_sent_event = True

# Security
task_always_eager = False  # Never run tasks synchronously (except in tests)
