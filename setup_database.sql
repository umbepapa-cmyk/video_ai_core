-- Setup Database Schema for Video Synthesis Research PoC
-- Apply this to your Supabase project via SQL Editor

-- =============================================================================
-- User Credits Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    credits INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Enable Row Level Security (RLS)
-- =============================================================================

ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;

-- =============================================================================
-- RLS Policies
-- =============================================================================

-- Users can only read their own credits
DROP POLICY IF EXISTS "Users can read own credits" ON user_credits;
CREATE POLICY "Users can read own credits"
    ON user_credits
    FOR SELECT
    USING (auth.uid() = user_id);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_user_credits_user_id 
    ON user_credits(user_id);

CREATE INDEX IF NOT EXISTS idx_user_credits_updated_at 
    ON user_credits(updated_at DESC);

-- =============================================================================
-- Secure RPC Function for Credit Decrementation
-- =============================================================================

DROP FUNCTION IF EXISTS decrement_user_credits(UUID, INTEGER, INTEGER);

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
    -- Validate input
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive';
    END IF;
    
    -- Lock the row to prevent race conditions (FOR UPDATE)
    SELECT user_credits.credits INTO v_current_credits
    FROM user_credits
    WHERE user_credits.user_id = p_user_id
    FOR UPDATE;
    
    -- Check if user exists
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    -- Check if sufficient credits available
    IF v_current_credits < p_amount + p_min_credits THEN
        RAISE EXCEPTION 'Insufficient credits: has %, needs %',
            v_current_credits, p_amount + p_min_credits;
    END IF;
    
    -- Decrement credits atomically
    UPDATE user_credits
    SET credits = user_credits.credits - p_amount,
        updated_at = NOW()
    WHERE user_credits.user_id = p_user_id
    RETURNING user_credits.user_id, user_credits.credits, user_credits.updated_at
    INTO user_id, credits, updated_at;
    
    RETURN NEXT;
END;
$$;

-- =============================================================================
-- Grant Permissions
-- =============================================================================

GRANT EXECUTE ON FUNCTION decrement_user_credits TO authenticated;
GRANT USAGE ON SCHEMA public TO authenticated;

-- =============================================================================
-- Helper Function: Initialize User Credits
-- =============================================================================

DROP FUNCTION IF EXISTS initialize_user_credits(UUID, INTEGER);

CREATE OR REPLACE FUNCTION initialize_user_credits(
    p_user_id UUID,
    p_initial_credits INTEGER DEFAULT 100
)
RETURNS TABLE (
    user_id UUID,
    credits INTEGER,
    created_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO user_credits (user_id, credits)
    VALUES (p_user_id, p_initial_credits)
    ON CONFLICT (user_id) DO NOTHING
    RETURNING user_credits.user_id, user_credits.credits, user_credits.created_at
    INTO user_id, credits, created_at;
    
    IF NOT FOUND THEN
        SELECT user_credits.user_id, user_credits.credits, user_credits.created_at
        FROM user_credits
        WHERE user_credits.user_id = p_user_id
        INTO user_id, credits, created_at;
    END IF;
    
    RETURN NEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION initialize_user_credits TO authenticated;

-- =============================================================================
-- Trigger: Update timestamp on credit changes
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_user_credits_updated_at ON user_credits;

CREATE TRIGGER update_user_credits_updated_at
    BEFORE UPDATE ON user_credits
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Sample Data (for testing)
-- =============================================================================

-- Create sample users with initial credits
INSERT INTO user_credits (user_id, credits)
VALUES 
    ('00000000-0000-0000-0000-000000000001'::UUID, 100),
    ('00000000-0000-0000-0000-000000000002'::UUID, 50),
    ('00000000-0000-0000-0000-000000000003'::UUID, 200)
ON CONFLICT (user_id) DO NOTHING;

-- =============================================================================
-- Verification Queries
-- =============================================================================

-- Verify table exists
SELECT 'Table exists' as status, COUNT(*) as user_count
FROM user_credits;

-- Test RPC function (this will decrement credits!)
-- SELECT * FROM decrement_user_credits(
--     '00000000-0000-0000-0000-000000000001'::UUID,
--     10,
--     0
-- );

-- Check current credits
SELECT * FROM user_credits;

-- =============================================================================
-- Setup Complete
-- =============================================================================

-- Now update your .env file with:
-- SUPABASE_URL=https://your-project.supabase.co
-- SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
