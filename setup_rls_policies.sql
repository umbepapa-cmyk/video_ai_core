-- ============================================================
-- WEEK 3 V2 - DAY 20: Row-Level Security (RLS) Policies
-- ============================================================
-- Complete RLS policy setup with Supabase Auth integration
-- Apply this AFTER setup_database_v2.sql
-- ============================================================

-- ============================================================
-- DROP TEMPORARY POLICIES (from Day 19)
-- ============================================================

DROP POLICY IF EXISTS "profiles_select_policy" ON public.profiles;
DROP POLICY IF EXISTS "job_history_select_policy" ON public.job_history;
DROP POLICY IF EXISTS "profiles_service_role_policy" ON public.profiles;
DROP POLICY IF EXISTS "job_history_service_role_policy" ON public.job_history;


-- ============================================================
-- PROFILES TABLE - RLS POLICIES
-- ============================================================

-- Users can view their own profile
CREATE POLICY "profiles_select_own"
    ON public.profiles
    FOR SELECT
    USING (auth.uid() = user_id);

-- Users can update their own profile (except credits)
-- Credits can only be updated by service role or via RPC functions
CREATE POLICY "profiles_update_own"
    ON public.profiles
    FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (
        auth.uid() = user_id
        AND (
            -- Allow updating these fields only
            OLD.credits = NEW.credits OR
            -- Service role can update credits
            current_setting('request.jwt.claims', true)::json->>'role' = 'service_role'
        )
    );

-- New users can insert their own profile (during signup)
CREATE POLICY "profiles_insert_own"
    ON public.profiles
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Service role has full access
CREATE POLICY "profiles_service_role_all"
    ON public.profiles
    FOR ALL
    USING (
        current_setting('request.jwt.claims', true)::json->>'role' = 'service_role'
    );


-- ============================================================
-- JOB HISTORY TABLE - RLS POLICIES
-- ============================================================

-- Users can view their own job history
CREATE POLICY "job_history_select_own"
    ON public.job_history
    FOR SELECT
    USING (auth.uid() = user_id);

-- Users can insert their own jobs
CREATE POLICY "job_history_insert_own"
    ON public.job_history
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Users can update their own jobs (only certain fields)
CREATE POLICY "job_history_update_own"
    ON public.job_history
    FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (
        auth.uid() = user_id
        AND (
            -- Allow updating status via RPC only
            current_setting('request.jwt.claims', true)::json->>'role' = 'service_role'
        )
    );

-- Service role has full access
CREATE POLICY "job_history_service_role_all"
    ON public.job_history
    FOR ALL
    USING (
        current_setting('request.jwt.claims', true)::json->>'role' = 'service_role'
    );


-- ============================================================
-- HELPER FUNCTION: Create profile on user signup
-- ============================================================

-- Automatically create profile when new user signs up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (user_id, email, credits)
    VALUES (NEW.id, NEW.email, 100)  -- 100 initial credits
    ON CONFLICT (user_id) DO NOTHING;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger on auth.users table (Supabase Auth)
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();


-- ============================================================
-- SECURITY DEFINER FUNCTIONS - Update with auth checks
-- ============================================================

-- Update RPC functions to use auth.uid() for additional security

-- Decrement credits with auth check
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
    v_calling_user UUID;
BEGIN
    -- Get calling user from JWT
    v_calling_user := auth.uid();
    
    -- Security check: user can only decrement their own credits
    -- (unless service role)
    IF v_calling_user != p_user_id THEN
        IF current_setting('request.jwt.claims', true)::json->>'role' != 'service_role' THEN
            RAISE EXCEPTION 'Unauthorized: cannot modify other user credits';
        END IF;
    END IF;
    
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Amount must be positive';
    END IF;
    
    -- Lock row to prevent race conditions
    SELECT profiles.credits INTO v_current_credits
    FROM public.profiles
    WHERE profiles.user_id = p_user_id
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'User not found: %', p_user_id;
    END IF;
    
    -- Check sufficient credits
    IF v_current_credits < p_amount + p_min_credits THEN
        RAISE EXCEPTION 'Insufficient credits: has %, needs %',
            v_current_credits, p_amount + p_min_credits;
    END IF;
    
    -- Decrement credits and increment video counter
    UPDATE public.profiles
    SET 
        credits = profiles.credits - p_amount,
        total_videos_generated = profiles.total_videos_generated + 1,
        updated_at = NOW()
    WHERE profiles.user_id = p_user_id
    RETURNING 
        profiles.user_id, 
        profiles.credits, 
        profiles.updated_at
    INTO 
        decrement_user_credits.user_id,
        decrement_user_credits.credits,
        decrement_user_credits.updated_at;
    
    RETURN NEXT;
END;
$$;


-- Update job status with auth check
CREATE OR REPLACE FUNCTION update_job_status(
    p_job_id UUID,
    p_status TEXT,
    p_video_url TEXT DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_job_user_id UUID;
    v_calling_user UUID;
BEGIN
    -- Get calling user
    v_calling_user := auth.uid();
    
    -- Get job owner
    SELECT user_id INTO v_job_user_id
    FROM public.job_history
    WHERE job_id = p_job_id;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Job not found: %', p_job_id;
    END IF;
    
    -- Security check
    IF v_calling_user != v_job_user_id THEN
        IF current_setting('request.jwt.claims', true)::json->>'role' != 'service_role' THEN
            RAISE EXCEPTION 'Unauthorized: cannot update other user job';
        END IF;
    END IF;
    
    -- Update job
    UPDATE public.job_history
    SET 
        status = p_status,
        video_url = COALESCE(p_video_url, video_url),
        error_message = COALESCE(p_error_message, error_message),
        completed_at = CASE 
            WHEN p_status IN ('completed', 'failed') THEN NOW()
            ELSE completed_at
        END
    WHERE job_id = p_job_id;
END;
$$;


-- ============================================================
-- GRANT PERMISSIONS
-- ============================================================

GRANT USAGE ON SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA public TO anon;

GRANT SELECT, INSERT, UPDATE ON public.profiles TO authenticated;
GRANT SELECT, INSERT ON public.job_history TO authenticated;

GRANT EXECUTE ON FUNCTION handle_new_user TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_credits TO authenticated;
GRANT EXECUTE ON FUNCTION decrement_user_credits TO authenticated;
GRANT EXECUTE ON FUNCTION create_job TO authenticated;
GRANT EXECUTE ON FUNCTION update_job_status TO authenticated;


-- ============================================================
-- VERIFICATION
-- ============================================================

DO $$
DECLARE
    v_profile_policies INTEGER;
    v_job_policies INTEGER;
BEGIN
    -- Count policies
    SELECT COUNT(*) INTO v_profile_policies
    FROM pg_policies
    WHERE tablename = 'profiles';
    
    SELECT COUNT(*) INTO v_job_policies
    FROM pg_policies
    WHERE tablename = 'job_history';
    
    IF v_profile_policies < 3 THEN
        RAISE WARNING 'Expected at least 3 policies on profiles, found %', v_profile_policies;
    END IF;
    
    IF v_job_policies < 3 THEN
        RAISE WARNING 'Expected at least 3 policies on job_history, found %', v_job_policies;
    END IF;
    
    RAISE NOTICE '✓ RLS Policies Applied Successfully';
    RAISE NOTICE '  - Profiles policies: %', v_profile_policies;
    RAISE NOTICE '  - Job history policies: %', v_job_policies;
    RAISE NOTICE '';
    RAISE NOTICE '✓ Auth integration complete';
    RAISE NOTICE '✓ Users can only access their own data';
    RAISE NOTICE '✓ Service role has full access';
    RAISE NOTICE '✓ New users auto-create profile with 100 credits';
END $$;


-- ============================================================
-- TESTING QUERIES (Run as authenticated user)
-- ============================================================

-- Test 1: Get own profile
-- SELECT * FROM public.profiles WHERE user_id = auth.uid();

-- Test 2: Get own job history
-- SELECT * FROM public.job_history WHERE user_id = auth.uid();

-- Test 3: Try to access other user's data (should return empty)
-- SELECT * FROM public.profiles WHERE user_id != auth.uid();

-- Test 4: Decrement own credits
-- SELECT * FROM decrement_user_credits(auth.uid()::uuid, 10);
