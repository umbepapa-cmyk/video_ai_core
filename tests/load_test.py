"""
WEEK 4 - DAY 25-26: Load Testing with Locust
==============================================
Simulates concurrent users generating videos and polling job status.

Run:
    locust -f tests/load_test.py --host=http://localhost:8000

Or headless:
    locust -f tests/load_test.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=5m --headless
"""

import random
import uuid
import json
from typing import Optional

from locust import HttpUser, task, between, events
from locust.exception import RescheduleTask


class VideoGenerationUser(HttpUser):
    """
    Simulates a user interacting with AppVideoAI:
    1. Login/authentication
    2. Submit video generation job
    3. Poll job status until completion
    4. Check credit balance
    """
    
    # Wait between tasks (simulates user think time)
    wait_time = between(5, 15)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token: Optional[str] = None
        self.user_email: Optional[str] = None
        self.job_ids: list = []
    
    def on_start(self):
        """
        Called when a simulated user starts.
        Performs authentication.
        """
        # Generate unique test user
        user_num = random.randint(1, 1000)
        self.user_email = f"test_user_{user_num}@loadtest.com"
        
        # In a real scenario, you would authenticate here
        # For PoC, we simulate having a token
        self.token = f"fake_token_{uuid.uuid4().hex[:16]}"
        
        # Create test user profile in database (if needed)
        # This would be done through your auth system
        
        self.client.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })
    
    @task(1)
    def check_health(self):
        """Check if server is healthy (low weight)."""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")
    
    @task(3)
    def submit_video_generation_job(self):
        """
        Submit a video generation job.
        This is a core user action (medium weight).
        """
        prompts = [
            "A woman dancing in a modern studio",
            "A man walking down a city street",
            "A person doing yoga on a beach",
            "Someone cooking in a professional kitchen",
            "A dancer performing contemporary moves"
        ]
        
        job_data = {
            "user_id": str(uuid.uuid4()),  # Simulated user ID
            "prompt": random.choice(prompts),
            "credits_required": random.choice([10, 20, 30]),
            "duration_seconds": random.choice([5, 10])
        }
        
        with self.client.post(
            "/api/v1/generate-video",
            json=job_data,
            catch_response=True,
            name="/api/v1/generate-video [POST]"
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    job_id = data.get("job_id")
                    
                    if job_id:
                        self.job_ids.append(job_id)
                        response.success()
                    else:
                        response.failure("No job_id in response")
                        
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            
            elif response.status_code == 402:
                # Insufficient credits - this is expected behavior
                response.success()
            
            else:
                response.failure(f"Job submission failed: {response.status_code}")
    
    @task(10)
    def poll_job_status(self):
        """
        Poll job status (high weight - users poll frequently).
        """
        if not self.job_ids:
            # No jobs to poll, reschedule this task
            raise RescheduleTask()
        
        # Poll a random job from our list
        job_id = random.choice(self.job_ids)
        
        with self.client.get(
            f"/api/v1/jobs/{job_id}",
            catch_response=True,
            name="/api/v1/jobs/{id} [GET]"
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    status = data.get("status")
                    
                    if status in ["completed", "failed"]:
                        # Remove completed jobs from polling list
                        self.job_ids.remove(job_id)
                    
                    response.success()
                    
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            
            elif response.status_code == 404:
                # Job not found - remove from list
                self.job_ids.remove(job_id)
                response.success()
            
            else:
                response.failure(f"Job status check failed: {response.status_code}")
    
    @task(2)
    def check_credits(self):
        """
        Check user credit balance (medium-low weight).
        """
        # This would call your credits endpoint
        # For now, we simulate it
        pass


class StressTestUser(HttpUser):
    """
    Aggressive stress testing user.
    Submits many jobs quickly to test rate limiting and concurrent processing.
    """
    
    wait_time = between(1, 3)  # Shorter wait time
    
    @task(5)
    def rapid_fire_jobs(self):
        """Submit jobs rapidly."""
        job_data = {
            "user_id": str(uuid.uuid4()),
            "prompt": "Stress test job",
            "credits_required": 10,
            "duration_seconds": 5
        }
        
        with self.client.post(
            "/api/v1/generate-video",
            json=job_data,
            catch_response=True,
            name="/api/v1/generate-video [STRESS]"
        ) as response:
            # Accept any response (including rate limit errors)
            response.success()


# ============================================================================
# Custom Event Handlers
# ============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when the load test starts."""
    print("\n" + "="*70)
    print("🚀 AppVideoAI Load Test Starting")
    print("="*70)
    print(f"Target: {environment.host}")
    print(f"Users: {environment.runner.target_user_count if hasattr(environment.runner, 'target_user_count') else 'N/A'}")
    print("="*70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when the load test stops."""
    print("\n" + "="*70)
    print("✅ AppVideoAI Load Test Completed")
    print("="*70)
    
    # Print summary stats
    stats = environment.stats
    
    print(f"\nTotal Requests: {stats.total.num_requests}")
    print(f"Total Failures: {stats.total.num_failures}")
    print(f"Failure Rate: {stats.total.fail_ratio * 100:.2f}%")
    print(f"Average Response Time: {stats.total.avg_response_time:.2f}ms")
    print(f"Max Response Time: {stats.total.max_response_time:.2f}ms")
    print(f"Requests/sec: {stats.total.total_rps:.2f}")
    
    print("="*70 + "\n")


# ============================================================================
# Usage Examples
# ============================================================================

if __name__ == "__main__":
    print("""
AppVideoAI Load Testing Suite
==============================

Usage Examples:

1. Interactive Web UI:
   locust -f tests/load_test.py --host=http://localhost:8000

2. Headless Mode (100 concurrent users):
   locust -f tests/load_test.py \\
       --host=http://localhost:8000 \\
       --users=100 \\
       --spawn-rate=10 \\
       --run-time=5m \\
       --headless \\
       --csv=results/load_test

3. Stress Test (500 concurrent users):
   locust -f tests/load_test.py \\
       --host=http://localhost:8000 \\
       --users=500 \\
       --spawn-rate=50 \\
       --run-time=10m \\
       --headless

4. Custom User Class:
   locust -f tests/load_test.py \\
       --host=http://localhost:8000 \\
       --user StressTestUser \\
       --users=200

Metrics to Monitor:
- Response time p50, p95, p99
- Request failure rate (should be < 1%)
- Database connection pool saturation
- Memory usage (ephemeral storage)
- Credit transaction conflicts

Expected Performance:
- P95 response time < 5s for job submission
- P95 response time < 500ms for job status checks
- Failure rate < 1% under normal load
- Support 100+ concurrent users
    """)
