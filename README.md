# Video Synthesis Research PoC

**Academic Proof of Concept** for video synthesis integrating spatial analysis, GDPR compliance, and AI-powered generation.

> ⚠️ **DISCLAIMER:** This is an academic research prototype NOT intended for production use.

## 🎯 Project Overview

This PoC implements a complete 5-phase pipeline for AI-driven video synthesis:

### **FASE 1**: Frame Extractor (`frame_extractor.py`)
- OpenCV-based spatial video analysis
- Laplacian variance for motion blur detection
- `cv2.solvePnP` for camera pose estimation (Yaw, Pitch, Roll)
- Intelligent selection of 5 diverse frames with optimal spatial distribution

### **FASE 2**: Security Module (`security_module.py`)
- GDPR-compliant ephemeral storage (tmpfs on Linux, temp on Windows)
- Face analysis and age verification using DeepFace
- Blocking exception for underage detection (< 25 years)
- Secure asynchronous cleanup with irreversible deletion (`shutil.rmtree`)

### **FASE 3**: API Orchestrator (`api_orchestrator.py`)
- Extends `test_fal.py` into production-ready orchestrator
- Flux.1 Dev image generation
- Alibaba Wan I2V-01 video generation
- FFmpeg-based video merging with xfade crossfade
- Autoregressive multi-shot generation support

### **FASE 4**: Database (`database.py`)
- Supabase/PostgreSQL integration
- Secure RPC functions for credit management
- Row-level locking (FOR UPDATE) to prevent race conditions
- Transactional credit decrementation

### **FASE 5**: Main Integration (`main.py` + `app.py`)
- FastAPI REST API backend with async job processing
- Streamlit interactive frontend
- Complete pipeline orchestration of all 5 phases

## 📦 Installation

### Prerequisites

- Python 3.9+
- FFmpeg (for video processing)
- Supabase project (optional, for credit management)
- Fal.ai API key (already configured in `.env`)

### Setup

1. **Activate virtual environment** (already created):

```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

2. **Install new dependencies**:

```bash
pip install -r requirements.txt
```

3. **Configure environment** (update `.env`):

```bash
# Already configured:
FAL_KEY=your_fal_key_here

# Add these for full functionality:
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here
MIN_AGE_THRESHOLD=25
```

4. **Setup Supabase database** (optional, for credit management):

Apply the SQL schema from `database.py`:

```sql
-- See database.py SCHEMA_SQL constant for full schema
python -c "from database import SCHEMA_SQL; print(SCHEMA_SQL)" > setup_database.sql
```

Then apply to your Supabase project via SQL Editor.

## 🚀 Usage

### Start Backend Server

```bash
python main.py
```

Server runs on `http://localhost:8000`
API docs: `http://localhost:8000/docs`

### Start Frontend

In a separate terminal:

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`

### Using the Application

1. Open Streamlit frontend
2. Enter User ID (for credit tracking)
3. Upload a video file
4. Enter text prompt for video generation
5. Click "Generate Video"
6. Monitor real-time progress
7. Download result when complete

## 🔬 Testing Individual Modules

Each phase can be tested independently:

```bash
# Test frame extraction
python frame_extractor.py path/to/video.mp4

# Test security module
python security_module.py path/to/image.jpg

# Test API orchestrator
python api_orchestrator.py

# Test database connection
python database.py
```

## 📁 Project Structure

```
AppVideoAI/
├── test_fal.py              # Day 1 - Original Fal.ai smoke test
├── frame_extractor.py        # Fase 1 - Spatial video analysis
├── security_module.py        # Fase 2 - GDPR security
├── api_orchestrator.py       # Fase 3 - API orchestration
├── database.py               # Fase 4 - Supabase integration
├── main.py                   # Fase 5 - FastAPI backend
├── app.py                    # Fase 5 - Streamlit frontend
├── requirements.txt          # All dependencies
├── .env                      # Configuration (DO NOT COMMIT)
├── .gitignore               # Git ignore rules
└── venv/                     # Virtual environment
```

## 🔒 Security Features

### Age Verification
- DeepFace-based facial age estimation
- Minimum threshold: 25 years (configurable)
- Blocks processing if age < threshold

### Ephemeral Storage
- RAM-based storage (tmpfs on Linux)
- Secure multi-pass file deletion
- Automatic cleanup after processing

### Credit Management
- Row-level locking to prevent race conditions
- Atomic credit decrementation
- Transaction-safe operations

## ⚙️ Configuration

Key environment variables in `.env`:

```bash
# API
FAL_KEY=your_fal_api_key

# Database (optional)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key

# Security
MIN_AGE_THRESHOLD=25

# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false

# Processing
MAX_VIDEO_SIZE_MB=500
NUM_FRAMES_TO_EXTRACT=5
LAPLACIAN_VARIANCE_THRESHOLD=100.0
```

## 🐛 Troubleshooting

### FFmpeg not found
```bash
# Windows: choco install ffmpeg
# Linux: apt install ffmpeg
# Mac: brew install ffmpeg
```

### DeepFace model downloads
First run downloads models (~300MB). Requires internet.

### Supabase connection errors
- Verify URL format: `https://xxxxx.supabase.co`
- Use Service Role Key (not anon key)
- Ensure RPC function is deployed

## 📚 Technical Details

### Algorithms Used
- **Laplacian operator**: Motion blur detection
- **PnP (Perspective-n-Point)**: Camera pose estimation
- **Euler angles**: Rotation representation (Yaw, Pitch, Roll)
- **Greedy diversity selection**: Optimal frame sampling

### APIs Integrated
- **Fal.ai Flux.1 Dev**: High-quality image generation
- **Fal.ai Alibaba Wan I2V-01**: Image-to-video generation
- **Supabase**: PostgreSQL with real-time capabilities
- **FFmpeg**: Video processing and merging

### Performance
- Frame extraction: ~2-5s per video
- Age verification: ~1-2s per frame
- Video generation: 60-120s (API-dependent)
- FFmpeg crossfade: ~5-10s per merge

## 📝 Research Context

This PoC demonstrates:

1. **Computer Vision**: Spatial analysis, pose estimation, blur detection
2. **Security**: GDPR compliance, age verification, secure deletion
3. **Distributed Systems**: Async APIs, queue management, job orchestration
4. **Database**: Transactional operations, concurrency control, RLS

**Limitations (by design for PoC):**
- Simplified pose estimation (requires calibration in production)
- Single-frame age verification (consider multi-frame consensus)
- In-memory job queue (use Redis/Celery in production)
- No authentication/authorization (implement OAuth2/JWT)
- Last frame extraction not implemented for autoregressive generation

## 🔄 Migration from test_fal.py

The original `test_fal.py` has been extended into `api_orchestrator.py` with:
- ✅ Production-ready async client
- ✅ Video generation support (Alibaba Wan)
- ✅ Queue management
- ✅ FFmpeg integration
- ✅ Autoregressive generation framework

Original `test_fal.py` remains unchanged for reference.

## 📈 Future Enhancements

- [ ] Multi-frame age verification with consensus
- [ ] Last frame extraction from generated videos
- [ ] Real-time WebSocket status updates
- [ ] Distributed job queue (Celery/RabbitMQ)
- [ ] Advanced video synthesis models
- [ ] Temporal consistency analysis

## 📄 License

Academic research prototype - Not for commercial use.

---

**Version:** 0.1.0  
**Status:** Academic Research PoC  
**Last Updated:** 2026-05-22
