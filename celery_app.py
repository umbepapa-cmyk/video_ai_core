"""
PHASE 2 SPRINT 1: Celery App Initialization
============================================
Main Celery application instance for async video generation.

Features:
- Auto-discovery of tasks from tasks module
- Configuration from celery_config.py
- Debug task for testing
- Proper error handling and logging
"""

import logging
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Create Celery instance
celery_app = Celery('appvideoai')

# Load configuration from celery_config.py
celery_app.config_from_object('celery_config')

# Auto-discover tasks from the tasks module
celery_app.autodiscover_tasks(['tasks'])

logger.info("Celery app initialized successfully")


# ============================================================================
# Celery Signals - Lifecycle Hooks
# ============================================================================

@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    """Log task execution start"""
    logger.info(f"Task {task.name}[{task_id}] started")


@task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, **extra):
    """Log task execution completion"""
    logger.info(f"Task {task.name}[{task_id}] completed successfully")


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **extra):
    """Log task execution failure"""
    logger.error(f"Task {sender.name}[{task_id}] failed: {exception}")
    logger.error(f"Traceback: {einfo}")


# ============================================================================
# Debug & Test Tasks
# ============================================================================

@celery_app.task(bind=True, name='celery_app.debug_task')
def debug_task(self):
    """
    Debug task for testing Celery setup.
    
    Usage:
        from celery_app import debug_task
        result = debug_task.delay()
        print(result.get())
    """
    logger.info(f"Debug task executed: {self.request!r}")
    return {
        'status': 'ok',
        'task_id': self.request.id,
        'task_name': self.request.task,
        'message': 'Celery is working correctly!'
    }


@celery_app.task(bind=True, name='celery_app.health_check')
def health_check(self):
    """
    Health check task for monitoring.
    
    Returns:
        dict: Health status information
    """
    import psutil
    import platform
    
    return {
        'status': 'healthy',
        'task_id': self.request.id,
        'hostname': self.request.hostname,
        'system': {
            'platform': platform.system(),
            'cpu_count': psutil.cpu_count(),
            'memory_percent': psutil.virtual_memory().percent
        },
        'timestamp': str(self.request.delivery_info)
    }


if __name__ == '__main__':
    # Start Celery worker (for development)
    celery_app.start()
