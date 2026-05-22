"""
FASE 5 + WEEK 3 V2: Streamlit Frontend Application
===================================================
Professional SPA with dark theme, async job management, and authentication.

Week 3 enhancements:
- Day 15: Dark theme + professional grid layout
- Day 16: Upload validation with MIME type checking
- Day 17: HTML5 video player with custom controls
- Day 18: Async job polling with progress tracking
- Day 20: JWT authentication integration
- Day 21: Mobile-responsive design
"""

import streamlit as st
import requests
import time
from pathlib import Path
import os
import base64
from upload_validator import validate_video_upload, validate_image_upload
from auth_handler import AuthHandler, AuthError, InvalidCredentialsError, UserExistsError

API_URL = os.getenv("API_URL", "http://localhost:8000")

# Initialize auth handler (Day 20)
try:
    auth_handler = AuthHandler()
except Exception as e:
    auth_handler = None
    print(f"Warning: Auth handler not initialized: {e}")


def inject_custom_css():
    """Inject custom CSS for dark theme and responsive design (Days 15, 21)."""
    st.markdown("""
    <style>
        /* DARK THEME - Professional Color Palette */
        :root {
            --primary-bg: #0e1117;
            --secondary-bg: #1a1d26;
            --accent-color: #ff4b4b;
            --accent-hover: #ff6b6b;
            --text-primary: #ffffff;
            --text-secondary: #b0b3b8;
            --border-color: #2d3139;
            --success-color: #00d47e;
            --warning-color: #ffa500;
        }
        
        /* Main Container */
        .main {
            background-color: var(--primary-bg);
            padding: 1rem;
            max-width: 100vw;
        }
        
        /* Buttons - Touch-optimized */
        .stButton>button {
            background-color: var(--accent-color);
            color: var(--text-primary);
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 1rem;
            padding: 0.75rem 1.5rem;
            min-height: 48px;
            width: 100%;
            transition: all 0.3s ease;
            cursor: pointer;
        }
        
        .stButton>button:hover {
            background-color: var(--accent-hover);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(255, 75, 75, 0.3);
        }
        
        /* Text Inputs - Prevent iOS zoom */
        .stTextInput>div>div>input,
        .stTextArea>div>div>textarea {
            background-color: var(--secondary-bg);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-size: 1rem;
            min-height: 44px;
            padding: 0.75rem;
        }
        
        .stTextInput>div>div>input:focus,
        .stTextArea>div>div>textarea:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 2px rgba(255, 75, 75, 0.2);
        }
        
        /* File Uploader */
        .stFileUploader>div {
            background-color: var(--secondary-bg);
            border: 2px dashed var(--border-color);
            border-radius: 8px;
            padding: 2rem;
            transition: all 0.3s ease;
        }
        
        .stFileUploader>div:hover {
            border-color: var(--accent-color);
        }
        
        /* Progress Bar */
        .stProgress>div>div>div {
            background-color: var(--accent-color);
        }
        
        /* Cards/Containers */
        .element-container {
            background-color: var(--secondary-bg);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }
        
        /* Sidebar */
        .css-1d391kg {
            background-color: var(--secondary-bg);
        }
        
        /* Video Player Container */
        .video-container {
            background-color: var(--secondary-bg);
            border-radius: 12px;
            padding: 1.5rem;
            margin-top: 1rem;
        }
        
        video {
            width: 100%;
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
        }
        
        /* Status Badges */
        .status-badge {
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.9rem;
            margin: 0.5rem 0;
        }
        
        .status-success {
            background-color: rgba(0, 212, 126, 0.2);
            color: var(--success-color);
        }
        
        .status-processing {
            background-color: rgba(255, 165, 0, 0.2);
            color: var(--warning-color);
        }
        
        .status-error {
            background-color: rgba(255, 75, 75, 0.2);
            color: var(--accent-color);
        }
        
        /* RESPONSIVE DESIGN - Mobile First (Day 21) */
        
        /* Safe area insets for iPhone notch/home indicator */
        .main {
            padding-top: max(1rem, env(safe-area-inset-top));
            padding-bottom: max(1rem, env(safe-area-inset-bottom));
            padding-left: max(1rem, env(safe-area-inset-left));
            padding-right: max(1rem, env(safe-area-inset-right));
        }
        
        /* Mobile - Default (< 768px) */
        @media (max-width: 767px) {
            .main {
                padding: max(0.5rem, env(safe-area-inset-top)) 
                         max(0.5rem, env(safe-area-inset-right))
                         max(0.5rem, env(safe-area-inset-bottom))
                         max(0.5rem, env(safe-area-inset-left));
            }
            
            .stButton>button {
                width: 100%;
                font-size: 1rem;  /* 1rem = 16px, prevents iOS zoom */
                min-height: 48px; /* Apple HIG minimum touch target */
            }
            
            .stTextInput>div>div>input,
            .stTextArea>div>div>textarea {
                font-size: 1rem !important;  /* Prevent zoom on iOS */
            }
            
            h1 {
                font-size: 1.75rem !important;
                line-height: 1.2;
            }
            
            h2 {
                font-size: 1.35rem !important;
                line-height: 1.3;
            }
            
            h3 {
                font-size: 1.15rem !important;
            }
            
            /* Larger tap targets for mobile */
            .stCheckbox {
                min-height: 44px;
            }
            
            /* Reduce spacing on mobile */
            .element-container {
                padding: 1rem;
                margin-bottom: 0.75rem;
            }
            
            /* Stack columns on mobile */
            .row-widget.stRadio > div,
            .row-widget.stCheckbox > div {
                flex-direction: column;
            }
        }
        
        /* Small phones (< 375px) - iPhone SE, etc. */
        @media (max-width: 374px) {
            .main {
                padding: 0.25rem;
            }
            
            h1 {
                font-size: 1.5rem !important;
            }
            
            .stButton>button {
                font-size: 0.95rem;
                padding: 0.65rem 1rem;
            }
        }
        
        /* Tablet (768px - 1024px) */
        @media (min-width: 768px) {
            .main {
                padding: 2rem;
            }
            
            .stButton>button {
                width: auto;
                min-width: 200px;
            }
            
            h1 {
                font-size: 2.5rem !important;
            }
            
            h2 {
                font-size: 2rem !important;
            }
        }
        
        /* Desktop (> 1024px) */
        @media (min-width: 1024px) {
            .main {
                max-width: 1400px;
                margin: 0 auto;
            }
            
            .stButton>button:hover {
                transform: translateY(-2px);
            }
        }
        
        /* Landscape orientation optimizations */
        @media (max-height: 500px) and (orientation: landscape) {
            .main {
                padding: 0.5rem;
            }
            
            h1 {
                font-size: 1.25rem !important;
            }
            
            .element-container {
                padding: 0.75rem;
            }
        }
        
        /* High DPI displays (Retina) */
        @media (-webkit-min-device-pixel-ratio: 2), (min-resolution: 192dpi) {
            /* Ensure crisp rendering */
            * {
                -webkit-font-smoothing: antialiased;
                -moz-osx-font-smoothing: grayscale;
            }
        }
        
        /* Touch device optimizations */
        @media (hover: none) and (pointer: coarse) {
            /* Remove hover effects on touch devices */
            .stButton>button:hover {
                transform: none;
                box-shadow: none;
            }
            
            /* Add active state for touch feedback */
            .stButton>button:active {
                transform: scale(0.98);
                opacity: 0.9;
            }
            
            /* Larger tap targets */
            button, a, input, select, textarea {
                min-height: 44px;
                min-width: 44px;
            }
        }
        
        /* Hide Streamlit Branding */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)


def main():
    """Main Streamlit application (Week 3 V2 Enhanced)."""
    
    st.set_page_config(
        page_title="Video Synthesis Research PoC",
        page_icon="🎬",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            'Get Help': None,
            'Report a bug': None,
            'About': "Video Synthesis Research PoC - Week 3 V2"
        }
    )
    
    # Inject custom CSS (Day 15, 21)
    inject_custom_css()
    
    # Header
    st.title("🎬 Video Synthesis Research PoC")
    st.markdown("""
    <div style='background: linear-gradient(90deg, #ff4b4b 0%, #ff8e53 100%); 
                padding: 1rem; border-radius: 8px; margin-bottom: 2rem;'>
        <p style='color: white; margin: 0; font-size: 0.95rem;'>
            <strong>Week 3 V2 Enhanced:</strong> Professional UI · Async Processing · 
            JWT Auth · Mobile Optimized
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # Day 20: Authentication with JWT
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    
    if "access_token" not in st.session_state:
        st.session_state.access_token = None
    
    # Sidebar - User info and settings
    with st.sidebar:
        st.markdown("### 👤 User Profile")
        
        if st.session_state.authenticated and auth_handler:
            st.success(f"✓ Logged in as:\n`{st.session_state.user_email}`")
            
            # Credits display (Day 19 - real DB integration)
            credits_display = st.session_state.get("user_credits", 100)
            st.markdown(f"""
            <div style='background-color: var(--secondary-bg); 
                        padding: 1rem; border-radius: 8px; text-align: center;'>
                <p style='margin: 0; color: var(--text-secondary); font-size: 0.9rem;'>
                    Available Credits
                </p>
                <p style='margin: 0; color: var(--success-color); font-size: 2rem; font-weight: bold;'>
                    {credits_display}
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            if st.button("🚪 Logout", use_container_width=True):
                # Sign out via auth handler
                if st.session_state.access_token:
                    auth_handler.sign_out(st.session_state.access_token)
                
                # Clear session
                st.session_state.authenticated = False
                st.session_state.user_email = None
                st.session_state.access_token = None
                st.session_state.user_credits = 0
                st.success("✓ Logged out successfully")
                st.rerun()
        else:
            # Real authentication (Day 20)
            if not auth_handler:
                st.error("⚠️ Auth not available. Check SUPABASE_ANON_KEY.")
                
                # Fallback demo mode
                demo_email = st.text_input("Email (Demo)", value="demo@example.com")
                if st.button("🔓 Demo Login", use_container_width=True):
                    st.session_state.authenticated = True
                    st.session_state.user_email = demo_email
                    st.session_state.user_credits = 100
                    st.rerun()
            else:
                # Auth tabs
                auth_tab = st.radio("Select", ["Login", "Sign Up"], horizontal=True)
                
                email = st.text_input("Email", key="auth_email")
                password = st.text_input("Password", type="password", key="auth_password")
                
                if auth_tab == "Sign Up":
                    if st.button("📝 Create Account", use_container_width=True):
                        if not email or not password:
                            st.error("❌ Email and password required")
                        else:
                            with st.spinner("Creating account..."):
                                success, msg, user = auth_handler.sign_up(email, password)
                            
                            if success:
                                st.success(f"✓ {msg}")
                                st.info("Please check your email to confirm your account, then login.")
                            else:
                                st.error(f"❌ {msg}")
                
                else:  # Login
                    if st.button("🔐 Login", use_container_width=True):
                        if not email or not password:
                            st.error("❌ Email and password required")
                        else:
                            with st.spinner("Authenticating..."):
                                success, msg, session = auth_handler.sign_in(email, password)
                            
                            if success and session:
                                st.session_state.authenticated = True
                                st.session_state.user_email = session.email
                                st.session_state.access_token = session.access_token
                                st.session_state.user_credits = 100  # TODO: Load from DB
                                st.success(f"✓ {msg}")
                                st.rerun()
                            else:
                                st.error(f"❌ {msg}")
        
        st.divider()
        
        st.markdown("### ⚙️ Generation Settings")
        
        credits_required = st.number_input(
            "Credits per Generation",
            min_value=1,
            max_value=100,
            value=10,
            help="Credits consumed per video generation"
        )
        
        duration_seconds = st.slider(
            "Video Duration (seconds)",
            min_value=3,
            max_value=10,
            value=5,
            help="Length of generated video"
        )
        
        st.divider()
        
        st.markdown("### ℹ️ About")
        st.markdown("""
        **Version:** Week 3 V2
        
        **Status:**
        - ✅ Dark Theme
        - ✅ Async Jobs
        - ✅ JWT Auth
        - ✅ Mobile Ready
        
        **Disclaimer:** Academic PoC
        """)
        
        # API Health indicator
        show_api_health()
    
    # Main content - Grid layout (Day 15)
    # Check if user is authenticated (bypass for now)
    if not st.session_state.authenticated:
        st.warning("⚠️ Please login from the sidebar to continue")
        return
    
    # Responsive columns
    col1, col2 = st.columns([2, 1], gap="large")
    
    with col1:
        st.markdown("### 📤 Video Upload & Configuration")
        
        # Prompt input (primary)
        prompt = st.text_area(
            "🎨 Generation Prompt",
            value="A cinematic scene of a person walking through a futuristic city at sunset, "
                  "with neon lights reflecting on wet streets",
            height=120,
            help="Describe the video you want to generate with detailed visual elements",
            placeholder="Enter a detailed prompt describing your desired video..."
        )
        
        st.markdown("---")
        
        # Video upload section (Day 16: Secure validation)
        uploaded_file = st.file_uploader(
            "🎥 Upload Reference Video (Optional - for identity lock)",
            type=["mp4", "mov", "avi", "mkv", "webm"],
            help="Upload a selfie video (5 angles) for identity preservation. Max 50MB."
        )
        
        if uploaded_file is not None:
            file_size_mb = uploaded_file.size / (1024 * 1024)
            
            # DAY 16: Secure upload validation with MIME check + magic bytes
            file_bytes = uploaded_file.getvalue()
            is_valid, error_message = validate_video_upload(
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                max_size_mb=50.0
            )
            
            if not is_valid:
                st.error(f"❌ Upload validation failed: {error_message}")
                st.warning("⚠️ Please upload a valid video file (MP4, MOV, AVI, MKV, WebM)")
            else:
                st.success(f"✓ Video validated: **{uploaded_file.name}**")
                
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.info(f"📦 Size: {file_size_mb:.2f} MB")
                with col_info2:
                    st.info(f"📝 Type: {uploaded_file.type}")
                
                # Store in session state for later use
                st.session_state.validated_video_upload = uploaded_file
    
    with col2:
        st.markdown("### 🎛️ Advanced Options")
        
        # Reference faces (Day 16 - biometric ingestion)
        st.markdown("**Identity Lock Settings**")
        
        use_identity_lock = st.checkbox(
            "Enable Identity Lock",
            value=False,
            help="Preserve specific facial features from reference images"
        )
        
        if use_identity_lock:
            ref_faces = st.file_uploader(
                "Upload Reference Faces",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                help="Upload 3-5 face images for identity preservation (max 10MB each)"
            )
            
            if ref_faces:
                # DAY 16: Validate each reference face
                all_valid = True
                for face_file in ref_faces:
                    is_valid, error = validate_image_upload(
                        file_bytes=face_file.getvalue(),
                        filename=face_file.name,
                        max_size_mb=10.0
                    )
                    if not is_valid:
                        st.error(f"❌ {face_file.name}: {error}")
                        all_valid = False
                
                if all_valid:
                    st.success(f"✓ {len(ref_faces)} reference face(s) validated")
                    st.session_state.validated_ref_faces = ref_faces
        
        st.markdown("---")
        st.markdown("**ControlNet Maps (Optional)**")
        
        use_controlnet = st.checkbox(
            "Enable ControlNet Guidance",
            value=False,
            help="Use depth/pose maps for structural control"
        )
        
        if use_controlnet:
            controlnet_map = st.file_uploader(
                "Upload ControlNet Map",
                type=["png", "jpg"],
                help="Depth map or pose skeleton"
            )
            
            if controlnet_map:
                # Day 16: Validate ControlNet map
                is_valid, error_msg = validate_image_upload(
                    file_bytes=controlnet_map.getvalue(),
                    filename=controlnet_map.name,
                    max_size_mb=10
                )
                
                if not is_valid:
                    st.error(f"❌ {error_msg}")
                else:
                    st.success("✓ ControlNet map validated")
    
    st.divider()
    
    # Generation button - Full width
    if st.button("🚀 Generate Video", type="primary", use_container_width=True):
        # Validation
        if not prompt.strip():
            st.error("❌ Please enter a prompt")
            return
        
        # Check credits
        if st.session_state.get("user_credits", 0) < credits_required:
            st.error(f"❌ Insufficient credits. You need {credits_required}, but have {st.session_state.get('user_credits', 0)}")
            return
        
        # Submit job (Day 18 - async with job_id)
        with st.spinner("🔄 Submitting generation request..."):
            success, job_id = submit_generation_v2(
                user_email=st.session_state.user_email,
                prompt=prompt,
                video_file=uploaded_file,
                credits_required=credits_required,
                duration_seconds=duration_seconds
            )
        
        if not success:
            st.error(f"❌ Generation failed: {job_id}")
            return
        
        st.success(f"✓ Generation started! Job ID: `{job_id}`")
        
        # Store job_id in session
        st.session_state.job_id = job_id
        st.session_state.monitoring = True
        
        # Optimistic credits deduction (UI only)
        st.session_state.user_credits -= credits_required
    
    st.divider()
    
    # Job monitoring section (Day 18 - enhanced polling)
    if st.session_state.get("monitoring") and st.session_state.get("job_id"):
        st.markdown("### 📊 Generation Status")
        
        monitor_job_v2(st.session_state.job_id)


def submit_generation_v2(
    user_email: str,
    prompt: str,
    video_file,
    credits_required: int,
    duration_seconds: int = 5
) -> tuple[bool, str]:
    """
    Submit video generation request to API (Day 18 - V2 async endpoint).
    
    Returns:
        (success: bool, job_id or error_message: str)
    """
    try:
        # Prepare payload
        files = {}
        if video_file:
            files["video"] = (video_file.name, video_file.getvalue(), video_file.type)
        
        data = {
            "user_email": user_email,
            "prompt": prompt,
            "credits_required": credits_required,
            "duration_seconds": duration_seconds
        }
        
        # Call V2 endpoint (will be created in Day 18)
        # For now, fallback to existing endpoint
        endpoint = f"{API_URL}/api/v1/generate-video"
        
        response = requests.post(
            endpoint,
            files=files if files else None,
            data=data,
            timeout=30
        )
        
        if response.status_code == 202:  # Accepted
            result = response.json()
            return (True, result["job_id"])
        elif response.status_code == 200:  # Backward compatibility
            result = response.json()
            if result.get("success"):
                return (True, result["job_id"])
            else:
                return (False, result.get("message", "Unknown error"))
        else:
            return (False, f"HTTP {response.status_code}: {response.text}")
            
    except Exception as e:
        return (False, str(e))


def get_job_status_v2(job_id: str):
    """Get status of generation job (Day 18 - V2 endpoint)."""
    try:
        # Try V2 endpoint first
        response = requests.get(
            f"{API_URL}/api/v1/jobs/{job_id}",
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()
        
        # Fallback to V1
        response = requests.get(
            f"{API_URL}/api/status/{job_id}",
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()
        
        return None
            
    except Exception as e:
        st.error(f"Error getting status: {e}")
        return None


def render_html5_video_player(video_url: str, video_bytes: bytes = None):
    """
    Render HTML5 video player with custom controls (Day 17).
    
    Args:
        video_url: URL to video file
        video_bytes: Optional bytes for download button
    """
    st.markdown('<div class="video-container">', unsafe_allow_html=True)
    
    st.markdown("### 🎬 Generated Video")
    
    # HTML5 Video Player
    video_html = f"""
    <video 
        width="100%" 
        controls 
        preload="auto"
        style="border-radius: 8px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);"
    >
        <source src="{video_url}" type="video/mp4">
        Your browser does not support the video tag.
    </video>
    """
    st.markdown(video_html, unsafe_allow_html=True)
    
    # Download button
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        if video_bytes:
            st.download_button(
                label="⬇️ Download Video",
                data=video_bytes,
                file_name="generated_video.mp4",
                mime="video/mp4",
                use_container_width=True
            )
    
    with col2:
        st.link_button(
            "🔗 Open in Browser",
            video_url,
            use_container_width=True
        )
    
    with col3:
        if st.button("🔄 Generate New", use_container_width=True):
            st.session_state.monitoring = False
            if "job_id" in st.session_state:
                del st.session_state.job_id
            st.rerun()
    
    st.markdown('</div>', unsafe_allow_html=True)


def monitor_job_v2(job_id: str):
    """
    PHASE 2 SPRINT 1: Enhanced Celery Job Monitoring.
    
    Features:
    - Adaptive polling (2s intervals)
    - Granular progress tracking (biometric, identity lock, generation, stitching, upload)
    - Visual progress bar with stage-specific messages
    - Video preview on completion
    - Metadata display (identity stability, temporal consistency)
    """
    status_placeholder = st.empty()
    progress_placeholder = st.empty()
    stage_placeholder = st.empty()
    message_placeholder = st.empty()
    metadata_placeholder = st.empty()
    video_placeholder = st.empty()
    
    max_iterations = 300  # 10 minutes (2s * 300 = 600s)
    iteration = 0
    
    while iteration < max_iterations:
        status = get_job_status_v2(job_id)
        
        if status is None:
            status_placeholder.error("❌ Could not retrieve job status")
            break
        
        # PHASE 2: Enhanced status emoji mapping with Celery states
        status_emoji = {
            "pending": "⏳",
            "processing": "🔄",
            "generating": "🎨",
            "stitching": "✂️",
            "uploading": "☁️",
            "completed": "✅",
            "failed": "❌",
            "retrying": "🔁",
            "unknown": "❓"
        }
        
        # Stage emoji mapping
        stage_emoji = {
            "queued": "📥",
            "biometric_extraction": "🔬",
            "celebrity_check": "🎭",
            "identity_lock": "🔒",
            "core_generation": "🎬",
            "stitching": "✂️",
            "uploading": "☁️",
            "completed": "✅",
            "failed": "❌",
            "retry": "🔁"
        }
        
        current_status = status.get("status", "unknown")
        current_stage = status.get("stage", "unknown")
        emoji = status_emoji.get(current_status, "⚙️")
        stage_icon = stage_emoji.get(current_stage, "⚙️")
        
        # Display status badge
        status_class = "status-processing"
        if current_status == "completed":
            status_class = "status-success"
        elif current_status == "failed":
            status_class = "status-error"
        elif current_status == "retrying":
            status_class = "status-warning"
        
        status_placeholder.markdown(f"""
        <div class="status-badge {status_class}">
            {emoji} {current_status.replace('_', ' ').title()}
        </div>
        """, unsafe_allow_html=True)
        
        # Progress bar with percentage
        progress = status.get("progress", 0)
        progress_placeholder.progress(
            progress / 100,
            text=f"Progress: {progress}%"
        )
        
        # Stage indicator
        stage_placeholder.markdown(f"""
        <div style="padding: 0.5rem; background: var(--secondary-bg); border-radius: 8px; margin: 0.5rem 0;">
            <strong>{stage_icon} Stage:</strong> {current_stage.replace('_', ' ').title()}
        </div>
        """, unsafe_allow_html=True)
        
        # Status message
        message = status.get("message", "Processing...")
        message_placeholder.info(f"💬 {message}")
        
        # Metadata display for processing stages
        metadata = status.get("metadata", {})
        if metadata:
            metadata_text = []
            if "duration" in metadata:
                metadata_text.append(f"⏱ Duration: {metadata['duration']}s")
            if "identity_stability" in metadata:
                metadata_text.append(f"🎭 Identity Stability: {metadata['identity_stability']:.1%}")
            if "temporal_consistency" in metadata:
                metadata_text.append(f"📊 Temporal Consistency: {metadata['temporal_consistency']:.1%}")
            
            if metadata_text:
                metadata_placeholder.markdown(" | ".join(metadata_text))
        
        # Handle retry status
        if current_status == "retrying":
            retry_count = status.get("retry_count", 0)
            st.warning(f"🔁 Automatic retry in progress (attempt {retry_count + 1}/3)")
        
        # Terminal states
        if current_status == "completed":
            status_placeholder.success("✅ **Video generation completed successfully!**")
            
            video_url = status.get("video_url")
            
            if video_url:
                # Display metadata
                result_metadata = status.get("metadata", {})
                if result_metadata:
                    st.markdown("### 📈 Generation Metrics")
                    
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.metric(
                            "Duration",
                            f"{result_metadata.get('duration', 0)}s"
                        )
                    
                    with col2:
                        identity_score = result_metadata.get('identity_stability', 0)
                        st.metric(
                            "Identity Stability",
                            f"{identity_score:.1%}"
                        )
                    
                    with col3:
                        temporal_score = result_metadata.get('temporal_consistency', 0)
                        st.metric(
                            "Temporal Consistency",
                            f"{temporal_score:.1%}"
                        )
                
                # Display video
                with video_placeholder.container():
                    render_html5_video_player(video_url)
            else:
                st.warning("⚠️ Video URL not available")
            
            st.session_state.monitoring = False
            break
        
        elif current_status == "failed":
            error_msg = status.get("error", "Unknown error")
            status_placeholder.error(f"❌ **Generation failed:** {error_msg}")
            
            # Show detailed error if available
            if "error" in status:
                st.error(f"**Error Details:**\n{status['error']}")
            
            st.session_state.monitoring = False
            
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("🔄 Try Again", use_container_width=True):
                    if "job_id" in st.session_state:
                        del st.session_state.job_id
                    st.rerun()
            
            with col2:
                if st.button("💬 Report Issue", use_container_width=True):
                    st.info(f"Job ID for support: `{job_id}`")
            
            break
        
        # Sleep between polls
        time.sleep(2)
        iteration += 1
        
        # Update UI to show elapsed time
        elapsed_minutes = (iteration * 2) // 60
        elapsed_seconds = (iteration * 2) % 60
        
        if iteration % 15 == 0:  # Every 30 seconds
            st.sidebar.info(f"⏱ Elapsed: {elapsed_minutes}m {elapsed_seconds}s")
    
    if iteration >= max_iterations:
        status_placeholder.warning("⚠️ Monitoring timeout reached (10 minutes). Job may still be processing.")
        st.info(f"Check job status later with Job ID: `{job_id}`")
        st.session_state.monitoring = False


def show_api_health():
    """Show API health status in sidebar."""
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        
        if response.status_code == 200:
            st.success("✓ API Connected")
        else:
            st.error("✗ API Error")
    except:
        st.error("✗ API Offline")


def generate_video_page_v2_celery():
    """
    Phase 2 Sprint 1: Video generation page with Celery async processing.
    
    Features:
    - Celery task queue (202 Accepted)
    - Granular progress tracking (PROCESSING, STITCHING, UPLOADING states)
    - Real-time status updates
    - Stage-specific progress messages
    """
    st.markdown("## 🚀 Generate Video (V2 - Celery)")
    st.markdown("*Enhanced async processing with granular progress tracking*")
    
    # Check authentication
    if not st.session_state.get("authenticated"):
        st.warning("⚠ Please login to generate videos")
        return
    
    st.divider()
    
    # Generation form
    col1, col2 = st.columns([2, 1])
    
    with col1:
        prompt = st.text_area(
            "🎬 Video Prompt",
            placeholder="Describe the video you want to create...",
            height=120,
            help="Be descriptive! Example: 'A person walking in the park on a sunny day, cinematic lighting'"
        )
    
    with col2:
        duration_seconds = st.slider(
            "⏱ Duration",
            min_value=5,
            max_value=60,
            value=10,
            step=5,
            help="Video duration in seconds"
        )
        
        # Calculate credits
        credits_required = duration_seconds * 10
        if duration_seconds > 30:
            credits_required = int(credits_required * 0.85)  # 15% discount
        
        st.metric("Credits Required", credits_required)
        st.metric("Your Credits", st.session_state.get("user_credits", 0))
    
    # Reference faces directory input
    reference_faces_dir = st.text_input(
        "📁 Reference Faces Directory",
        value="./reference_faces",
        help="Directory containing 5 reference face images (frontal, left_45, right_45, left_90, right_90)"
    )
    
    # ControlNet map (optional)
    use_controlnet = st.checkbox("Use ControlNet Pose Map", value=False)
    controlnet_map_path = None
    
    if use_controlnet:
        controlnet_map_path = st.text_input(
            "🎯 ControlNet Map Path",
            value="./controlnet_map.png",
            help="Optional pose map for guided generation"
        )
    
    st.divider()
    
    # Generate button
    if st.button("🚀 Generate Video (Celery)", type="primary", use_container_width=True):
        # Validation
        if not prompt.strip():
            st.error("❌ Please enter a prompt")
            return
        
        # Check credits
        if st.session_state.get("user_credits", 0) < credits_required:
            st.error(f"❌ Insufficient credits. You need {credits_required}, but have {st.session_state.get('user_credits', 0)}")
            return
        
        # Submit job to Celery
        with st.spinner("🔄 Submitting to Celery queue..."):
            success, job_id = submit_generation_celery(
                user_id=st.session_state.user_id,
                prompt=prompt,
                reference_faces_dir=reference_faces_dir,
                duration_seconds=duration_seconds,
                controlnet_map_path=controlnet_map_path if use_controlnet else None
            )
        
        if not success:
            st.error(f"❌ Submission failed: {job_id}")
            return
        
        st.success(f"✓ Job submitted to Celery! Job ID: `{job_id}`")
        
        # Store job_id in session
        st.session_state.celery_job_id = job_id
        st.session_state.celery_monitoring = True
        
        # Optimistic credits deduction (UI only)
        st.session_state.user_credits -= credits_required
    
    st.divider()
    
    # Job monitoring section
    if st.session_state.get("celery_monitoring") and st.session_state.get("celery_job_id"):
        st.markdown("### 📊 Celery Job Status")
        
        monitor_celery_job(st.session_state.celery_job_id, st.session_state.user_id)


def submit_generation_celery(
    user_id: str,
    prompt: str,
    reference_faces_dir: str,
    duration_seconds: int,
    controlnet_map_path: str = None
) -> tuple[bool, str]:
    """
    Submit video generation job to Celery (Phase 2 Sprint 1).
    
    Args:
        user_id: User UUID
        prompt: Text prompt
        reference_faces_dir: Path to reference faces
        duration_seconds: Video duration
        controlnet_map_path: Optional ControlNet map
        
    Returns:
        (success, job_id or error_message)
    """
    try:
        payload = {
            "reference_faces_dir": reference_faces_dir,
            "prompt": prompt,
            "duration_seconds": duration_seconds
        }
        
        if controlnet_map_path:
            payload["controlnet_map_path"] = controlnet_map_path
        
        response = requests.post(
            f"{API_URL}/api/v2/generate-video",
            json=payload,
            headers={"X-User-ID": user_id},
            timeout=10
        )
        
        if response.status_code == 202:
            data = response.json()
            return True, data["job_id"]
        else:
            error_data = response.json()
            error_msg = error_data.get("detail", {}).get("message", "Unknown error")
            return False, error_msg
            
    except requests.exceptions.Timeout:
        return False, "Request timeout"
    except Exception as e:
        return False, str(e)


def monitor_celery_job(job_id: str, user_id: str):
    """
    Monitor Celery job with granular progress tracking.
    
    Features:
    - State-specific UI (PENDING, PROCESSING, STITCHING, UPLOADING, SUCCESS, FAILURE)
    - Progress bar with percentage
    - Stage descriptions
    - Video preview on completion
    """
    status_container = st.container()
    progress_bar = st.progress(0)
    stage_text = st.empty()
    message_text = st.empty()
    video_container = st.empty()
    actions_container = st.empty()
    
    max_polls = 300  # 10 minutes (2s interval)
    poll_count = 0
    
    # Stage emoji mapping
    stage_emoji = {
        "queued": "⏳",
        "biometric_extraction": "🔬",
        "age_verification": "🔒",
        "celebrity_blocking": "🚫",
        "identity_locking": "🎭",
        "core_generation": "🎨",
        "stitching": "🎬",
        "uploading": "☁",
        "completed": "✅",
        "failed": "❌",
        "retrying": "🔄",
        "unknown": "❓"
    }
    
    while poll_count < max_polls:
        try:
            # Poll job status
            response = requests.get(
                f"{API_URL}/api/v2/jobs/{job_id}",
                headers={"X-User-ID": user_id},
                timeout=5
            )
            
            if response.status_code != 200:
                message_text.error(f"❌ Failed to retrieve status: {response.status_code}")
                break
            
            status = response.json()
            state = status.get("state", "UNKNOWN")
            progress = status.get("progress", 0)
            stage = status.get("stage", "unknown")
            message = status.get("message", "")
            
            # Update progress bar
            progress_bar.progress(progress / 100 if progress <= 100 else 1.0)
            
            # Update stage text
            emoji = stage_emoji.get(stage, "⚙")
            stage_text.markdown(f"**Stage:** {emoji} `{stage}` ({progress}%)")
            
            # Update message
            if message:
                message_text.info(f"ℹ {message}")
            
            # Check completion states
            if state == 'SUCCESS':
                progress_bar.progress(1.0)
                stage_text.markdown(f"**Stage:** ✅ `completed` (100%)")
                message_text.success("🎉 Video generated successfully!")
                
                result = status.get("result", {})
                video_url = result.get("video_url")
                
                if video_url:
                    # Display video
                    with video_container:
                        st.markdown("### 🎬 Generated Video")
                        st.video(video_url)
                        
                        # Metrics
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.metric("Duration", f"{result.get('duration', 0)}s")
                        
                        with col2:
                            identity_stability = result.get('identity_stability', 0)
                            st.metric("Identity Stability", f"{identity_stability:.1%}")
                        
                        with col3:
                            temporal_consistency = result.get('temporal_consistency', 0)
                            st.metric("Temporal Consistency", f"{temporal_consistency:.1%}")
                        
                        # Download button
                        try:
                            video_response = requests.get(video_url, timeout=30)
                            if video_response.status_code == 200:
                                st.download_button(
                                    label="⬇ Download Video",
                                    data=video_response.content,
                                    file_name=f"generated_{job_id}.mp4",
                                    mime="video/mp4",
                                    use_container_width=True
                                )
                        except Exception as e:
                            st.warning(f"Could not prepare download: {e}")
                
                # Clear monitoring flag
                with actions_container:
                    if st.button("🔄 Generate Another Video", use_container_width=True):
                        st.session_state.celery_monitoring = False
                        if "celery_job_id" in st.session_state:
                            del st.session_state.celery_job_id
                        st.rerun()
                
                break
            
            elif state == 'FAILURE':
                progress_bar.progress(0.0)
                stage_text.markdown(f"**Stage:** ❌ `failed`")
                error = status.get("error", "Unknown error")
                message_text.error(f"❌ Generation failed: {error}")
                
                # Retry button
                with actions_container:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("🔄 Retry", use_container_width=True):
                            st.session_state.celery_monitoring = False
                            if "celery_job_id" in st.session_state:
                                del st.session_state.celery_job_id
                            st.rerun()
                    with col2:
                        if st.button("❌ Cancel", use_container_width=True):
                            st.session_state.celery_monitoring = False
                            if "celery_job_id" in st.session_state:
                                del st.session_state.celery_job_id
                            st.rerun()
                
                break
            
            elif state == 'RETRY':
                retry_count = status.get("retry_count", 0)
                message_text.warning(f"🔄 Task failed, retrying... (attempt {retry_count + 1})")
            
            # Continue polling
            time.sleep(2)
            poll_count += 1
            
        except requests.exceptions.Timeout:
            message_text.warning("⏱ Status check timeout, retrying...")
            time.sleep(2)
            poll_count += 1
            
        except Exception as e:
            message_text.error(f"❌ Error checking status: {e}")
            break
    
    if poll_count >= max_polls:
        message_text.warning("⏱ Polling timeout. Job may still be processing. Check back later.")
        with actions_container:
            if st.button("🔄 Refresh Status", use_container_width=True):
                st.rerun()


if __name__ == "__main__":
    main()
