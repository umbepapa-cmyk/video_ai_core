-- ============================================================================
-- WEEK 3 - DAY 19: Database Schema V2
-- ============================================================================
-- Extends existing user_credits table with profiles and job_history.
-- Adds B-Tree indices for performance optimization.

-- Enable UUID extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TABLE: profiles (Extended User Information)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.profiles (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    credits INTEGER DEFAULT 100 CHECK (credits >= 0),
    total_videos_generated INTEGER DEFAULT 0 CHECK (total_videos_generated >= 0),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trigger: Auto-update updated_at on profile changes
CREATE OR REPLACE FUNCTION update_profiles_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER profiles_updated_at_trigger
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_profiles_updated_at();

-- ============================================================================
-- TABLE: job_history (Job Tracking and Audit Trail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.job_history (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES public.profiles(user_id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    duration_seconds INTEGER CHECK (duration_seconds > 0),
    credits_consumed INTEGER DEFAULT 0 CHECK (credits_consumed >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    video_url TEXT,
    error_message TEXT,
    frames_extracted INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Trigger: Auto-set completed_at when status changes to completed/failed
CREATE OR REPLACE FUNCTION update_job_completed_at()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IN ('completed', 'failed') AND OLD.status NOT IN ('completed', 'failed') THEN
        NEW.completed_at = NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER job_completed_at_trigger
    BEFORE UPDATE ON public.job_history
    FOR EACH ROW
    EXECUTE FUNCTION update_job_completed_at();

-- ============================================================================
-- INDICES: B-Tree for Performance Optimization
-- ============================================================================

-- Profiles indices
CREATE INDEX IF NOT EXISTS idx_profiles_email ON public.profiles(email);
CREATE INDEX IF NOT EXISTS idx_profiles_credits ON public.profiles(credits);
CREATE INDEX IF NOT EXISTS idx_profiles_created_at ON public.profiles(created_at);

-- Job history indices
CREATE INDEX IF NOT EXISTS idx_job_history_user_id ON public.job_history(user_id);
CREATE INDEX IF NOT EXISTS idx_job_history_status ON public.job_history(status);
CREATE INDEX IF NOT EXISTS idx_job_history_created_at ON public.job_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_history_user_status ON public.job_history(user_id, status);

-- Composite index for user's recent jobs query
CREATE INDEX IF NOT EXISTS idx_job_history_user_recent 
    ON public.job_history(user_id, created_at DESC) 
    WHERE status = 'completed';

-- ============================================================================
-- RLS POLICIES: Row-Level Security (Day 20 will enable RLS)
-- ============================================================================
-- Note: RLS will be enabled on Day 20 with JWT auth

-- Prepare RLS policies (not enabled yet)
-- These will be activated on Day 20:

-- Policy: Users can view their own profile
-- CREATE POLICY "Users can view own profile" 
--     ON public.profiles FOR SELECT 
--     USING (auth.uid() = user_id);

-- Policy: Users can view their own job history
-- CREATE POLICY "Users can view own jobs" 
--     ON public.job_history FOR SELECT 
--     USING (auth.uid() = user_id);

-- Policy: Only service role can modify credits
-- CREATE POLICY "Service role can update credits"
--     ON public.profiles FOR UPDATE
--     USING (auth.jwt() ->> 'role' = 'service_role');

-- ============================================================================
-- RPC FUNCTIONS: Transactional Credit Operations
-- ============================================================================

-- Function: Decrement user credits (transactional with row lock)
CREATE OR REPLACE FUNCTION decrement_user_credits_v2(
    p_user_email TEXT,
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
    v_user_id UUID;
BEGIN
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive';
    END IF;
    
    -- Lock row to prevent race conditions
    SELECT profiles.user_id, profiles.credits INTO v_user_id, v_current_credits
    FROM profiles
    WHERE profiles.email = p_user_email
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_email;
    END IF;
    
    IF v_current_credits < p_amount + p_min_credits THEN
        RAISE EXCEPTION 'Insufficient credits: has %, needs %',
            v_current_credits, p_amount + p_min_credits;
    END IF;
    
    -- Decrement credits
    UPDATE profiles
    SET credits = profiles.credits - p_amount,
        updated_at = NOW()
    WHERE profiles.email = p_user_email
    RETURNING profiles.user_id, profiles.credits, profiles.updated_at
    INTO user_id, credits, updated_at;
    
    RETURN NEXT;
END;
$$;

-- Function: Get user credits by email
CREATE OR REPLACE FUNCTION get_user_credits_by_email(
    p_email TEXT
)
RETURNS TABLE (
    user_id UUID,
    email TEXT,
    credits INTEGER,
    total_videos_generated INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        profiles.user_id,
        profiles.email,
        profiles.credits,
        profiles.total_videos_generated
    FROM profiles
    WHERE profiles.email = p_email;
END;
$$;

-- Function: Create job record
CREATE OR REPLACE FUNCTION create_job_record(
    p_job_id UUID,
    p_user_email TEXT,
    p_prompt TEXT,
    p_duration_seconds INTEGER,
    p_credits_consumed INTEGER
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_user_id UUID;
BEGIN
    -- Get user_id from email
    SELECT profiles.user_id INTO v_user_id
    FROM profiles
    WHERE profiles.email = p_user_email;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_email;
    END IF;
    
    -- Insert job record
    INSERT INTO job_history (
        job_id,
        user_id,
        prompt,
        duration_seconds,
        credits_consumed,
        status
    ) VALUES (
        p_job_id,
        v_user_id,
        p_prompt,
        p_duration_seconds,
        p_credits_consumed,
        'pending'
    );
    
    RETURN p_job_id;
END;
$$;

-- Function: Update job status
CREATE OR REPLACE FUNCTION update_job_status(
    p_job_id UUID,
    p_status TEXT,
    p_video_url TEXT DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL,
    p_frames_extracted INTEGER DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE job_history
    SET 
        status = p_status,
        video_url = COALESCE(p_video_url, video_url),
        error_message = COALESCE(p_error_message, error_message),
        frames_extracted = COALESCE(p_frames_extracted, frames_extracted)
    WHERE job_id = p_job_id;
    
    -- Increment total_videos_generated if job completed
    IF p_status = 'completed' THEN
        UPDATE profiles
        SET total_videos_generated = total_videos_generated + 1
        WHERE user_id = (SELECT user_id FROM job_history WHERE job_id = p_job_id);
    END IF;
END;
$$;

-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================

GRANT EXECUTE ON FUNCTION decrement_user_credits_v2 TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_credits_by_email TO authenticated;
GRANT EXECUTE ON FUNCTION create_job_record TO authenticated;
GRANT EXECUTE ON FUNCTION update_job_status TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.job_history TO authenticated;

-- Service role has full access
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- ============================================================================
-- SAMPLE DATA (Optional - for testing)
-- ============================================================================

-- Insert demo user
INSERT INTO public.profiles (email, credits, total_videos_generated)
VALUES ('demo@example.com', 100, 0)
ON CONFLICT (email) DO NOTHING;

-- ============================================================================
-- END OF SCHEMA V2
-- ============================================================================

-- ============================================================================
-- WEEK 4 - DAY 23: Atomic Credit Management RPC Functions
-- ============================================================================

-- Table: payment_history (Track all payment transactions)
CREATE TABLE IF NOT EXISTS public.payment_history (
    payment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES public.profiles(user_id) ON DELETE CASCADE,
    transaction_id TEXT UNIQUE NOT NULL,
    package_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'completed' CHECK (status IN ('pending', 'completed', 'refunded', 'failed')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast transaction lookup
CREATE INDEX IF NOT EXISTS idx_payment_history_transaction_id 
    ON public.payment_history(transaction_id);
CREATE INDEX IF NOT EXISTS idx_payment_history_user_id 
    ON public.payment_history(user_id, created_at DESC);

-- Table: credit_transactions (Audit trail for all credit movements)
CREATE TABLE IF NOT EXISTS public.credit_transactions (
    transaction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES public.profiles(user_id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('purchase', 'consumption', 'refund', 'bonus')),
    job_id UUID REFERENCES public.job_history(job_id) ON DELETE SET NULL,
    payment_id UUID REFERENCES public.payment_history(payment_id) ON DELETE SET NULL,
    balance_before INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for audit queries
CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id 
    ON public.credit_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_job_id 
    ON public.credit_transactions(job_id);

-- Table: payment_webhooks (Webhook event audit log - Day 22)
CREATE TABLE IF NOT EXISTS public.payment_webhooks (
    webhook_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    transaction_id TEXT,
    user_email TEXT,
    package_id TEXT,
    amount NUMERIC(10, 2),
    success BOOLEAN DEFAULT FALSE,
    error_message TEXT,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for webhook debugging
CREATE INDEX IF NOT EXISTS idx_payment_webhooks_provider 
    ON public.payment_webhooks(provider);
CREATE INDEX IF NOT EXISTS idx_payment_webhooks_transaction_id 
    ON public.payment_webhooks(transaction_id);
CREATE INDEX IF NOT EXISTS idx_payment_webhooks_created_at 
    ON public.payment_webhooks(created_at DESC);

-- ============================================================================
-- RPC FUNCTION: add_credits_secure
-- ============================================================================
-- Adds credits to user account from payment
-- Includes transaction logging and atomic operation
CREATE OR REPLACE FUNCTION add_credits_secure(
    p_user_id UUID,
    p_amount INTEGER,
    p_transaction_id TEXT,
    p_package_id TEXT DEFAULT NULL,
    p_provider TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_old_balance INTEGER;
    v_new_balance INTEGER;
    v_payment_id UUID;
BEGIN
    -- Validate amount
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive: %', p_amount;
    END IF;
    
    -- Check if transaction already processed (idempotency)
    IF EXISTS (SELECT 1 FROM payment_history WHERE transaction_id = p_transaction_id) THEN
        -- Return existing transaction result
        SELECT credits INTO v_new_balance
        FROM profiles
        WHERE user_id = p_user_id;
        
        RETURN json_build_object(
            'success', true,
            'new_balance', v_new_balance,
            'message', 'Transaction already processed (idempotent)',
            'credits_added', p_amount
        );
    END IF;
    
    -- Lock user row to prevent race conditions
    SELECT credits INTO v_old_balance
    FROM profiles
    WHERE user_id = p_user_id
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    -- Add credits atomically
    UPDATE profiles
    SET 
        credits = credits + p_amount,
        updated_at = NOW()
    WHERE user_id = p_user_id
    RETURNING credits INTO v_new_balance;
    
    -- Log payment
    INSERT INTO payment_history (
        user_id,
        transaction_id,
        package_id,
        provider,
        amount,
        status
    ) VALUES (
        p_user_id,
        p_transaction_id,
        COALESCE(p_package_id, 'unknown'),
        COALESCE(p_provider, 'unknown'),
        p_amount,
        'completed'
    ) RETURNING payment_id INTO v_payment_id;
    
    -- Log credit transaction
    INSERT INTO credit_transactions (
        user_id,
        amount,
        type,
        payment_id,
        balance_before,
        balance_after,
        description
    ) VALUES (
        p_user_id,
        p_amount,
        'purchase',
        v_payment_id,
        v_old_balance,
        v_new_balance,
        'Credits purchased via ' || COALESCE(p_provider, 'payment gateway')
    );
    
    RETURN json_build_object(
        'success', true,
        'old_balance', v_old_balance,
        'new_balance', v_new_balance,
        'credits_added', p_amount,
        'transaction_id', p_transaction_id
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN json_build_object(
            'success', false,
            'error', SQLERRM
        );
END;
$$;

-- ============================================================================
-- RPC FUNCTION: consume_credits_secure
-- ============================================================================
-- Consumes credits for a job with atomic locking
-- Includes insufficient credits check and automatic rollback
CREATE OR REPLACE FUNCTION consume_credits_secure(
    p_user_id UUID,
    p_amount INTEGER,
    p_job_id UUID
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_current_balance INTEGER;
    v_new_balance INTEGER;
BEGIN
    -- Validate amount
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive: %', p_amount;
    END IF;
    
    -- Lock row to prevent race conditions (CRITICAL)
    SELECT credits INTO v_current_balance
    FROM profiles
    WHERE user_id = p_user_id
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    -- Check sufficient credits
    IF v_current_balance < p_amount THEN
        RAISE EXCEPTION 'Insufficient credits: % available, % required', 
            v_current_balance, p_amount;
    END IF;
    
    -- Decrement credits
    UPDATE profiles
    SET 
        credits = credits - p_amount,
        updated_at = NOW()
    WHERE user_id = p_user_id
    RETURNING credits INTO v_new_balance;
    
    -- Log consumption
    INSERT INTO credit_transactions (
        user_id,
        amount,
        type,
        job_id,
        balance_before,
        balance_after,
        description
    ) VALUES (
        p_user_id,
        -p_amount,
        'consumption',
        p_job_id,
        v_current_balance,
        v_new_balance,
        'Credits consumed for video generation'
    );
    
    RETURN json_build_object(
        'success', true,
        'old_balance', v_current_balance,
        'new_balance', v_new_balance,
        'consumed', p_amount,
        'job_id', p_job_id
    );
EXCEPTION
    WHEN OTHERS THEN
        -- Automatic rollback on any error
        RETURN json_build_object(
            'success', false,
            'error', SQLERRM,
            'current_balance', v_current_balance
        );
END;
$$;

-- ============================================================================
-- RPC FUNCTION: refund_credits_secure
-- ============================================================================
-- Refunds credits when a job fails
-- Includes audit trail logging
CREATE OR REPLACE FUNCTION refund_credits_secure(
    p_user_id UUID,
    p_amount INTEGER,
    p_job_id UUID,
    p_reason TEXT DEFAULT 'Job failed'
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_old_balance INTEGER;
    v_new_balance INTEGER;
BEGIN
    -- Validate amount
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Refund amount must be positive: %', p_amount;
    END IF;
    
    -- Get current balance
    SELECT credits INTO v_old_balance
    FROM profiles
    WHERE user_id = p_user_id;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    -- Add credits back
    UPDATE profiles
    SET 
        credits = credits + p_amount,
        updated_at = NOW()
    WHERE user_id = p_user_id
    RETURNING credits INTO v_new_balance;
    
    -- Log refund
    INSERT INTO credit_transactions (
        user_id,
        amount,
        type,
        job_id,
        balance_before,
        balance_after,
        description
    ) VALUES (
        p_user_id,
        p_amount,
        'refund',
        p_job_id,
        v_old_balance,
        v_new_balance,
        'Refund: ' || p_reason
    );
    
    RETURN json_build_object(
        'success', true,
        'old_balance', v_old_balance,
        'new_balance', v_new_balance,
        'refunded', p_amount,
        'reason', p_reason
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN json_build_object(
            'success', false,
            'error', SQLERRM
        );
END;
$$;

-- ============================================================================
-- GRANT PERMISSIONS FOR NEW FUNCTIONS
-- ============================================================================

GRANT EXECUTE ON FUNCTION add_credits_secure TO service_role;
GRANT EXECUTE ON FUNCTION consume_credits_secure TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION refund_credits_secure TO authenticated, service_role;

GRANT SELECT, INSERT, UPDATE ON public.payment_history TO service_role;
GRANT SELECT, INSERT ON public.credit_transactions TO authenticated, service_role;

-- ============================================================================
-- VERIFICATION
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Week 4 - Day 23: Atomic Credit RPC Functions installed!';
    RAISE NOTICE 'Functions: add_credits_secure, consume_credits_secure, refund_credits_secure';
    RAISE NOTICE 'Tables: payment_history, credit_transactions';
    RAISE NOTICE 'Features: FOR UPDATE locks, idempotency, audit trail';
END $$;

-- Verify setup
DO $$
BEGIN
    RAISE NOTICE 'Database Schema V2 initialized successfully!';
    RAISE NOTICE 'Tables created: profiles, job_history';
    RAISE NOTICE 'Indices: 8 B-Tree indices for performance';
    RAISE NOTICE 'RPC Functions: 4 transactional functions';
    RAISE NOTICE 'Next: Day 20 - Enable RLS and JWT auth';
END $$;
