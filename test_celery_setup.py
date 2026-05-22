"""
PHASE 2 SPRINT 1: Celery Setup Test Script
===========================================

Quick test script to verify Celery + Redis setup is working correctly.

Usage:
    python test_celery_setup.py
"""

import sys
import time
from celery_app import celery_app, debug_task, health_check

def test_redis_connection():
    """Test Redis connection."""
    print("\n" + "="*60)
    print("TEST 1: Redis Connection")
    print("="*60)
    
    try:
        # Try to ping broker
        result = celery_app.control.inspect().ping()
        
        if result:
            print("✅ Redis connection successful")
            print(f"   Workers online: {list(result.keys())}")
            return True
        else:
            print("⚠️  No workers responding (Redis OK, but no workers running)")
            print("   Start workers with: bash worker_start.sh")
            return False
            
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print("   Start Redis with: docker-compose -f docker-compose.redis.yml up -d")
        return False


def test_worker_availability():
    """Test if Celery workers are available."""
    print("\n" + "="*60)
    print("TEST 2: Worker Availability")
    print("="*60)
    
    try:
        # Inspect active workers
        inspect = celery_app.control.inspect()
        
        active = inspect.active()
        registered = inspect.registered()
        stats = inspect.stats()
        
        if not active:
            print("⚠️  No active workers found")
            print("   Start workers with: bash worker_start.sh")
            return False
        
        print(f"✅ Workers available: {len(active)}")
        
        for worker_name, worker_tasks in active.items():
            print(f"\n   Worker: {worker_name}")
            print(f"   - Active tasks: {len(worker_tasks)}")
            
            if worker_name in registered:
                print(f"   - Registered tasks: {len(registered[worker_name])}")
            
            if worker_name in stats:
                worker_stats = stats[worker_name]
                print(f"   - Pool: {worker_stats.get('pool', {}).get('implementation', 'unknown')}")
                print(f"   - Concurrency: {worker_stats.get('pool', {}).get('max-concurrency', 'unknown')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Worker inspection failed: {e}")
        return False


def test_task_execution():
    """Test task execution with debug task."""
    print("\n" + "="*60)
    print("TEST 3: Task Execution")
    print("="*60)
    
    try:
        print("Submitting debug task...")
        
        # Submit task
        result = debug_task.apply_async()
        task_id = result.id
        
        print(f"✅ Task submitted: {task_id}")
        print(f"   Task state: {result.state}")
        
        # Wait for result (timeout 10s)
        print("   Waiting for result...")
        
        task_result = result.get(timeout=10)
        
        print("✅ Task completed successfully!")
        print(f"   Result: {task_result}")
        
        return True
        
    except Exception as e:
        print(f"❌ Task execution failed: {e}")
        print("   Check worker logs: tail -f logs/celery_worker.log")
        return False


def test_health_check():
    """Test health check task."""
    print("\n" + "="*60)
    print("TEST 4: Health Check Task")
    print("="*60)
    
    try:
        print("Submitting health check task...")
        
        result = health_check.apply_async()
        health_result = result.get(timeout=10)
        
        print("✅ Health check completed!")
        print(f"   Celery: {health_result.get('celery', 'unknown')}")
        print(f"   Redis: {health_result.get('redis', 'unknown')}")
        print(f"   Worker: {health_result.get('worker', 'unknown')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False


def test_queue_routing():
    """Test queue routing configuration."""
    print("\n" + "="*60)
    print("TEST 5: Queue Routing")
    print("="*60)
    
    try:
        from celery_config import task_routes, task_queues
        
        print("✅ Task routes configured:")
        for task_name, route_config in task_routes.items():
            print(f"   {task_name} → {route_config['queue']}")
        
        print("\n✅ Queues defined:")
        for queue in task_queues:
            print(f"   - {queue.name} (routing: {queue.routing_key})")
        
        return True
        
    except Exception as e:
        print(f"❌ Queue routing check failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "#"*60)
    print("# PHASE 2 SPRINT 1: Celery Setup Test")
    print("#"*60)
    
    results = {
        "Redis Connection": test_redis_connection(),
        "Worker Availability": test_worker_availability(),
        "Task Execution": test_task_execution(),
        "Health Check": test_health_check(),
        "Queue Routing": test_queue_routing()
    }
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Celery setup is working correctly.")
        print("\nNext steps:")
        print("  1. Test video generation endpoint")
        print("  2. Monitor with Flower: bash monitor_celery.sh")
        print("  3. Check logs: tail -f logs/celery_worker.log")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above for details.")
        print("\nTroubleshooting:")
        print("  1. Start Redis: docker-compose -f docker-compose.redis.yml up -d")
        print("  2. Start workers: bash worker_start.sh 4")
        print("  3. Check logs: tail -f logs/celery_worker.log")
        return 1


if __name__ == "__main__":
    sys.exit(main())
