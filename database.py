"""
FASE 4 + WEEK 3 V2: Database Module
====================================
PostgreSQL/Supabase integration for state management.

This module implements:
- Supabase connection with Service Role Key
- Secure RPC interface for credit management
- Row-level locking to prevent race conditions
- Transactional credit decrementation

Week 3 V2 Extensions (Day 19):
- User profile management (profiles table)
- Job history tracking (job_history table)
- Enhanced credit operations
- Job state persistence
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Exception for database-related errors."""
    pass


class InsufficientCreditsError(DatabaseError):
    """Exception raised when user has insufficient credits."""
    pass


@dataclass
class UserCredits:
    """Data structure for user credit information."""
    user_id: str
    credits: int
    last_updated: str


@dataclass
class UserProfile:
    """Data structure for user profile (Week 3 V2)."""
    user_id: str
    email: str
    credits: int
    total_videos_generated: int
    created_at: str
    updated_at: str


@dataclass
class JobRecord:
    """Data structure for job history record (Week 3 V2)."""
    job_id: str
    user_id: str
    prompt: str
    duration_seconds: int
    credits_consumed: int
    status: str
    video_url: Optional[str]
    error_message: Optional[str]
    created_at: str
    completed_at: Optional[str]


class SupabaseClient:
    """Supabase client for credit management with Service Role Key."""
    
    def __init__(
        self,
        url: Optional[str] = None,
        service_role_key: Optional[str] = None
    ):
        load_dotenv()
        
        self.url = url or os.getenv("SUPABASE_URL")
        self.service_role_key = service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not self.url:
            raise DatabaseError("SUPABASE_URL not provided")
        
        if not self.service_role_key:
            raise DatabaseError("SUPABASE_SERVICE_ROLE_KEY not provided")
        
        self.client: Client = create_client(
            self.url,
            self.service_role_key,
            options=ClientOptions(
                auto_refresh_token=False,
                persist_session=False
            )
        )
        
        logger.info(f"Connected to Supabase: {self.url}")
    
    # ============================================================
    # WEEK 3 V2 - DAY 19: User Profile Management
    # ============================================================
    
    def create_user_profile(
        self,
        email: str,
        initial_credits: int = 100
    ) -> UserProfile:
        """
        Create new user profile with initial credits.
        
        Args:
            email: User email address
            initial_credits: Starting credit balance
        
        Returns:
            UserProfile object
        """
        try:
            response = self.client.table("profiles") \
                .insert({
                    "email": email,
                    "credits": initial_credits
                }) \
                .execute()
            
            data = response.data[0]
            
            logger.info(f"User profile created: {email} with {initial_credits} credits")
            
            return UserProfile(
                user_id=data["user_id"],
                email=data["email"],
                credits=data["credits"],
                total_videos_generated=data["total_videos_generated"],
                created_at=data["created_at"],
                updated_at=data["updated_at"]
            )
            
        except Exception as e:
            logger.error(f"Failed to create user profile: {e}")
            raise DatabaseError(f"Failed to create user profile: {e}")
    
    def get_user_profile(self, user_id: str) -> UserProfile:
        """
        Get user profile by user_id.
        
        Args:
            user_id: User identifier
        
        Returns:
            UserProfile object
        """
        try:
            response = self.client.table("profiles") \
                .select("*") \
                .eq("user_id", user_id) \
                .single() \
                .execute()
            
            data = response.data
            
            if not data:
                raise DatabaseError(f"User profile not found: {user_id}")
            
            return UserProfile(
                user_id=data["user_id"],
                email=data["email"],
                credits=data["credits"],
                total_videos_generated=data["total_videos_generated"],
                created_at=data["created_at"],
                updated_at=data["updated_at"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get user profile: {e}")
            raise DatabaseError(f"Failed to get user profile: {e}")
    
    def get_user_profile_by_email(self, email: str) -> UserProfile:
        """
        Get user profile by email.
        
        Args:
            email: User email address
        
        Returns:
            UserProfile object
        """
        try:
            response = self.client.table("profiles") \
                .select("*") \
                .eq("email", email) \
                .single() \
                .execute()
            
            data = response.data
            
            if not data:
                raise DatabaseError(f"User profile not found for email: {email}")
            
            return UserProfile(
                user_id=data["user_id"],
                email=data["email"],
                credits=data["credits"],
                total_videos_generated=data["total_videos_generated"],
                created_at=data["created_at"],
                updated_at=data["updated_at"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get user profile by email: {e}")
            raise DatabaseError(f"Failed to get user profile by email: {e}")
    
    def get_user_credits_v2(self, user_id: str) -> int:
        """
        Get user credits (Week 3 V2 - uses profiles table).
        
        Args:
            user_id: User identifier
        
        Returns:
            Current credit balance
        """
        try:
            response = self.client.rpc(
                "get_user_credits",
                {"p_user_id": user_id}
            ).execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Failed to get credits: {e}")
            raise DatabaseError(f"Failed to get credits: {e}")
    
    # ============================================================
    # WEEK 3 V2 - DAY 19: Job History Management
    # ============================================================
    
    def create_job_record(
        self,
        user_id: str,
        prompt: str,
        duration_seconds: int,
        credits_consumed: int
    ) -> str:
        """
        Create new job record in history.
        
        Args:
            user_id: User who created the job
            prompt: Generation prompt
            duration_seconds: Video duration
            credits_consumed: Credits used
        
        Returns:
            Job ID (UUID)
        """
        try:
            response = self.client.rpc(
                "create_job",
                {
                    "p_user_id": user_id,
                    "p_prompt": prompt,
                    "p_duration_seconds": duration_seconds,
                    "p_credits_consumed": credits_consumed
                }
            ).execute()
            
            job_id = response.data
            
            logger.info(f"Job record created: {job_id} for user {user_id}")
            
            return job_id
            
        except Exception as e:
            logger.error(f"Failed to create job record: {e}")
            raise DatabaseError(f"Failed to create job record: {e}")
    
    def update_job_status(
        self,
        job_id: str,
        status: str,
        video_url: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        """
        Update job status in history.
        
        Args:
            job_id: Job identifier
            status: New status (pending/processing/completed/failed)
            video_url: Optional result video URL
            error_message: Optional error message
        """
        try:
            self.client.rpc(
                "update_job_status",
                {
                    "p_job_id": job_id,
                    "p_status": status,
                    "p_video_url": video_url,
                    "p_error_message": error_message
                }
            ).execute()
            
            logger.info(f"Job status updated: {job_id} -> {status}")
            
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
            raise DatabaseError(f"Failed to update job status: {e}")
    
    def get_job_record(self, job_id: str) -> JobRecord:
        """
        Get job record by ID.
        
        Args:
            job_id: Job identifier
        
        Returns:
            JobRecord object
        """
        try:
            response = self.client.table("job_history") \
                .select("*") \
                .eq("job_id", job_id) \
                .single() \
                .execute()
            
            data = response.data
            
            if not data:
                raise DatabaseError(f"Job not found: {job_id}")
            
            return JobRecord(
                job_id=data["job_id"],
                user_id=data["user_id"],
                prompt=data["prompt"],
                duration_seconds=data["duration_seconds"],
                credits_consumed=data["credits_consumed"],
                status=data["status"],
                video_url=data.get("video_url"),
                error_message=data.get("error_message"),
                created_at=data["created_at"],
                completed_at=data.get("completed_at")
            )
            
        except Exception as e:
            logger.error(f"Failed to get job record: {e}")
            raise DatabaseError(f"Failed to get job record: {e}")
    
    def get_user_job_history(
        self,
        user_id: str,
        limit: int = 10,
        offset: int = 0
    ) -> list[JobRecord]:
        """
        Get user's job history (paginated).
        
        Args:
            user_id: User identifier
            limit: Maximum records to return
            offset: Records to skip
        
        Returns:
            List of JobRecord objects
        """
        try:
            response = self.client.table("job_history") \
                .select("*") \
                .eq("user_id", user_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .range(offset, offset + limit - 1) \
                .execute()
            
            jobs = []
            for data in response.data:
                jobs.append(JobRecord(
                    job_id=data["job_id"],
                    user_id=data["user_id"],
                    prompt=data["prompt"],
                    duration_seconds=data["duration_seconds"],
                    credits_consumed=data["credits_consumed"],
                    status=data["status"],
                    video_url=data.get("video_url"),
                    error_message=data.get("error_message"),
                    created_at=data["created_at"],
                    completed_at=data.get("completed_at")
                ))
            
            return jobs
            
        except Exception as e:
            logger.error(f"Failed to get job history: {e}")
            raise DatabaseError(f"Failed to get job history: {e}")
    
    # ============================================================
    # LEGACY METHODS (V1 compatibility)
    # ============================================================
    
    def get_credits(self, user_id: str) -> UserCredits:
        """
        Get current credit balance for user.
        
        Args:
            user_id: User identifier
            
        Returns:
            UserCredits object
        """
        try:
            response = self.client.table("user_credits") \
                .select("*") \
                .eq("user_id", user_id) \
                .single() \
                .execute()
            
            data = response.data
            
            if not data:
                raise DatabaseError(f"User not found: {user_id}")
            
            return UserCredits(
                user_id=data["user_id"],
                credits=data["credits"],
                last_updated=data.get("updated_at", "")
            )
            
        except Exception as e:
            logger.error(f"Failed to get credits: {e}")
            raise DatabaseError(f"Failed to get credits: {e}")
    
    def decrement_credits(
        self,
        user_id: str,
        amount: int,
        min_credits: int = 0
    ) -> UserCredits:
        """
        Decrement user credits using secure RPC with row locking.
        
        Calls PostgreSQL RPC function with FOR UPDATE lock to prevent race conditions.
        
        Args:
            user_id: User identifier
            amount: Number of credits to decrement
            min_credits: Minimum credits required before operation
            
        Returns:
            Updated UserCredits object
            
        Raises:
            InsufficientCreditsError: If user has insufficient credits
            DatabaseError: If operation fails
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")
        
        try:
            response = self.client.rpc(
                "decrement_user_credits",
                {
                    "p_user_id": user_id,
                    "p_amount": amount,
                    "p_min_credits": min_credits
                }
            ).execute()
            
            data = response.data
            
            if not data or len(data) == 0:
                raise DatabaseError("RPC returned no data")
            
            result = data[0]
            
            logger.info(f"Credits decremented: {user_id} -= {amount} (new: {result['credits']})")
            
            return UserCredits(
                user_id=result["user_id"],
                credits=result["credits"],
                last_updated=result.get("updated_at", "")
            )
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "insufficient credits" in error_msg:
                raise InsufficientCreditsError(f"User {user_id} has insufficient credits")
            elif "user not found" in error_msg:
                raise DatabaseError(f"User not found: {user_id}")
            else:
                logger.error(f"Failed to decrement credits: {e}")
                raise DatabaseError(f"Failed to decrement credits: {e}")
    
    def initialize_user(
        self,
        user_id: str,
        initial_credits: int = 100
    ) -> UserCredits:
        """
        Initialize a new user with credits.
        
        Args:
            user_id: User identifier
            initial_credits: Initial credit balance
            
        Returns:
            UserCredits object
        """
        try:
            response = self.client.table("user_credits") \
                .insert({
                    "user_id": user_id,
                    "credits": initial_credits
                }) \
                .execute()
            
            data = response.data[0]
            
            logger.info(f"User initialized: {user_id} with {initial_credits} credits")
            
            return UserCredits(
                user_id=data["user_id"],
                credits=data["credits"],
                last_updated=data.get("updated_at", "")
            )
            
        except Exception as e:
            logger.error(f"Failed to initialize user: {e}")
            raise DatabaseError(f"Failed to initialize user: {e}")


class CreditManager:
    """High-level credit management interface."""
    
    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self.client = supabase_client or SupabaseClient()
    
    def check_and_decrement(
        self,
        user_id: str,
        required_credits: int
    ) -> tuple[bool, UserCredits]:
        """
        Check if user has sufficient credits and decrement if so (atomic operation).
        
        Args:
            user_id: User identifier
            required_credits: Number of credits required
            
        Returns:
            Tuple of (success: bool, credits: UserCredits)
        """
        try:
            updated_credits = self.client.decrement_credits(
                user_id,
                required_credits
            )
            return (True, updated_credits)
            
        except InsufficientCreditsError as e:
            logger.warning(f"Insufficient credits for user {user_id}")
            try:
                current_credits = self.client.get_credits(user_id)
                return (False, current_credits)
            except:
                raise e
        
        except DatabaseError as e:
            logger.error(f"Credit check failed: {e}")
            raise


def init_database(
    url: Optional[str] = None,
    service_role_key: Optional[str] = None
) -> SupabaseClient:
    """Initialize database connection."""
    return SupabaseClient(url=url, service_role_key=service_role_key)


def decrement_credits(
    user_id: str,
    amount: int,
    client: Optional[SupabaseClient] = None
) -> UserCredits:
    """Decrement user credits."""
    if client is None:
        client = SupabaseClient()
    
    return client.decrement_credits(user_id, amount)


# Database schema SQL for Supabase setup
SCHEMA_SQL = """
-- NOTE: This is the V1 schema.
-- For Week 3 V2 schema, use setup_database_v2.sql instead.

-- User credits table
CREATE TABLE IF NOT EXISTS user_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    credits INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can only read their own credits
CREATE POLICY "Users can read own credits"
    ON user_credits
    FOR SELECT
    USING (auth.uid() = user_id);

-- Index
CREATE INDEX IF NOT EXISTS idx_user_credits_user_id 
    ON user_credits(user_id);

-- Secure RPC function for credit decrementation with row locking
CREATE OR REPLACE FUNCTION decrement_user_credits(
    p_user_id UUID,
    p_amount INTEGER,
    p_min_credits INTEGER DEFAULT 0
)
RETURNS TABLE (
    user_id UUID,
    credits INTEGER,
    updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_current_credits INTEGER;
BEGIN
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive';
    END IF;
    
    -- Lock row to prevent race conditions
    SELECT user_credits.credits INTO v_current_credits
    FROM user_credits
    WHERE user_credits.user_id = p_user_id
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    IF v_current_credits < p_amount + p_min_credits THEN
        RAISE EXCEPTION 'Insufficient credits: has %, needs %',
            v_current_credits, p_amount + p_min_credits;
    END IF;
    
    -- Decrement credits
    UPDATE user_credits
    SET credits = user_credits.credits - p_amount,
        updated_at = NOW()
    WHERE user_credits.user_id = p_user_id
    RETURNING user_credits.user_id, user_credits.credits, user_credits.updated_at
    INTO user_id, credits, updated_at;
    
    RETURN NEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION decrement_user_credits TO authenticated;

-- WEEK 3 V2 NOTE:
-- For full V2 schema with profiles and job_history, run setup_database_v2.sql
"""


# Week 3 V2 helper functions
def init_database_v2(
    url: Optional[str] = None,
    service_role_key: Optional[str] = None
) -> SupabaseClient:
    """
    Initialize database connection (Week 3 V2).
    
    Before using, make sure to apply setup_database_v2.sql to your Supabase project.
    """
    return SupabaseClient(url=url, service_role_key=service_role_key)


def create_user_profile(
    email: str,
    initial_credits: int = 100,
    client: Optional[SupabaseClient] = None
) -> UserProfile:
    """Create new user profile."""
    if client is None:
        client = SupabaseClient()
    
    return client.create_user_profile(email, initial_credits)


def get_user_by_email(
    email: str,
    client: Optional[SupabaseClient] = None
) -> UserProfile:
    """Get user profile by email."""
    if client is None:
        client = SupabaseClient()
    
    return client.get_user_profile_by_email(email)


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("DATABASE MODULE - WEEK 3 V2")
    print(f"{'='*60}\n")
    
    print("V2 Features:")
    print("-" * 60)
    print("✓ User profile management (profiles table)")
    print("✓ Job history tracking (job_history table)")
    print("✓ Enhanced credit operations")
    print("✓ RLS policies with auth integration")
    print()
    
    print("Setup Instructions:")
    print("-" * 60)
    print("1. Apply setup_database_v2.sql to your Supabase project")
    print("2. Set environment variables:")
    print("   SUPABASE_URL=https://your-project.supabase.co")
    print("   SUPABASE_SERVICE_ROLE_KEY=your_service_role_key")
    print("3. Test connection:")
    print()
    
    try:
        client = SupabaseClient()
        print("✓ Database connection successful")
    except DatabaseError as e:
        print(f"✗ Database connection failed: {e}")
    
    print(f"\n{'='*60}\n")


# ============================================================================
# WEEK 4 - DAY 23: Atomic Credit Management Methods
# ============================================================================

def add_credits(
    user_id: str,
    amount: int,
    transaction_id: str,
    package_id: Optional[str] = None,
    provider: Optional[str] = None
) -> dict:
    """
    Add credits to user account from payment (Week 4 - Day 23).
    
    Uses add_credits_secure RPC with idempotency and atomic locking.
    
    Args:
        user_id: User UUID
        amount: Credits to add
        transaction_id: Unique transaction ID for idempotency
        package_id: Payment package ID
        provider: Payment provider (ccbill, segpay, epoch)
    
    Returns:
        Dict with success status and new balance
    
    Raises:
        DatabaseError: If operation fails
    """
    logger.info(f"Adding {amount} credits for user {user_id} (txn: {transaction_id})")
    
    client = SupabaseClient()
    
    try:
        result = client.client.rpc("add_credits_secure", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_transaction_id": transaction_id,
            "p_package_id": package_id,
            "p_provider": provider
        }).execute()
        
        if not result.data:
            raise DatabaseError("RPC call returned no data")
        
        response = result.data
        
        if not response.get("success"):
            raise DatabaseError(f"Credit addition failed: {response.get('error', 'Unknown error')}")
        
        logger.info(
            f"Credits added successfully: {amount} credits to user {user_id} "
            f"(new balance: {response.get('new_balance')})"
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to add credits: {e}")
        raise DatabaseError(f"Failed to add credits: {str(e)}")


def consume_credits(user_id: str, amount: int, job_id: str) -> dict:
    """
    Consume credits for a job with atomic locking (Week 4 - Day 23).
    
    Uses consume_credits_secure RPC with FOR UPDATE lock.
    Automatically rolls back if insufficient credits.
    
    Args:
        user_id: User UUID
        amount: Credits to consume
        job_id: Job UUID
    
    Returns:
        Dict with success status and balances
    
    Raises:
        InsufficientCreditsError: If user has insufficient credits
        DatabaseError: If operation fails
    """
    logger.info(f"Consuming {amount} credits for user {user_id} (job: {job_id})")
    
    client = SupabaseClient()
    
    try:
        result = client.client.rpc("consume_credits_secure", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_job_id": job_id
        }).execute()
        
        if not result.data:
            raise DatabaseError("RPC call returned no data")
        
        response = result.data
        
        if not response.get("success"):
            error = response.get("error", "Unknown error")
            
            # Check if it's insufficient credits error
            if "Insufficient credits" in error:
                raise InsufficientCreditsError(error)
            
            raise DatabaseError(f"Credit consumption failed: {error}")
        
        logger.info(
            f"Credits consumed: {amount} from user {user_id} "
            f"(new balance: {response.get('new_balance')})"
        )
        
        return response
        
    except InsufficientCreditsError:
        raise
    except Exception as e:
        logger.error(f"Failed to consume credits: {e}")
        raise DatabaseError(f"Failed to consume credits: {str(e)}")


def refund_credits(
    user_id: str,
    amount: int,
    job_id: str,
    reason: str = "Job failed"
) -> dict:
    """
    Refund credits when a job fails (Week 4 - Day 23).
    
    Uses refund_credits_secure RPC with audit trail.
    
    Args:
        user_id: User UUID
        amount: Credits to refund
        job_id: Job UUID
        reason: Refund reason
    
    Returns:
        Dict with success status and balances
    
    Raises:
        DatabaseError: If operation fails
    """
    logger.info(f"Refunding {amount} credits for user {user_id} (job: {job_id})")
    
    client = SupabaseClient()
    
    try:
        result = client.client.rpc("refund_credits_secure", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_job_id": job_id,
            "p_reason": reason
        }).execute()
        
        if not result.data:
            raise DatabaseError("RPC call returned no data")
        
        response = result.data
        
        if not response.get("success"):
            raise DatabaseError(f"Credit refund failed: {response.get('error', 'Unknown error')}")
        
        logger.info(
            f"Credits refunded: {amount} to user {user_id} "
            f"(new balance: {response.get('new_balance')})"
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to refund credits: {e}")
        raise DatabaseError(f"Failed to refund credits: {str(e)}")

