# Setup Instructions - Network Layer Implementation

**Status:** ✅ Code Implementation COMPLETE  
**Date:** 2026-05-22  
**Action Required:** Environment setup by user

---

## What Was Implemented

✅ **Real API Calls to Fal.ai GPU clusters**
- `_generate_first_frame()` → Fal.ai Flux.1 Dev
- `_generate_single_video()` → Fal.ai Wan V2.2 I2V
- `_finalize_video()` → Real download with httpx streaming
- `extract_last_frame()` → Real FFmpeg extraction

✅ **Retry Logic with Exponential Backoff**
- All network calls wrapped with retry
- Configurable retries, delays, and exceptions

✅ **Comprehensive Error Handling**
- Detailed logging at every step
- Automatic cleanup on failures
- Progress tracking for downloads

✅ **Documentation**
- Full implementation report in `NETWORK_LAYER_IMPLEMENTATION.md`
- Test suite in `test_network_layer.py`

---

## ⚠️ REQUIRED SETUP STEPS

### Step 1: Install Missing Dependencies

```bash
cd c:\Users\umbep\OneDrive\Desktop\uncensored_video_app\AppVideoAI

# Install all Python dependencies
pip install -r requirements.txt

# Verify aiofiles installed
pip show aiofiles
```

**Expected output:**
```
Name: aiofiles
Version: 23.2.1
```

---

### Step 2: Set Real FAL API Key

1. Get API key from: https://fal.ai/dashboard/keys

2. Open `.env` file and replace placeholder:

```env
# BEFORE (placeholder)
FAL_KEY=your_fal_api_key_here

# AFTER (real key)
FAL_KEY=fal_key_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

3. Verify key is set:

```bash
# PowerShell
$env:FAL_KEY = Get-Content .env | Select-String "FAL_KEY" | ForEach-Object { $_.ToString().Split('=')[1] }
echo $env:FAL_KEY
```

**Expected output:** Your real API key (not placeholder)

---

### Step 3: Install FFmpeg

FFmpeg is required for `extract_last_frame()` to work.

#### Windows Installation

**Option A: Using Chocolatey (Recommended)**
```powershell
# Install Chocolatey if not installed
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Install FFmpeg
choco install ffmpeg
```

**Option B: Manual Installation**

1. Download from: https://www.gyan.dev/ffmpeg/builds/
2. Extract to `C:\ffmpeg`
3. Add to PATH:
   - Open System Properties → Environment Variables
   - Edit PATH variable
   - Add: `C:\ffmpeg\bin`
4. Restart PowerShell

#### Verify Installation

```bash
ffmpeg -version
```

**Expected output:**
```
ffmpeg version N-XXXXX-gXXXXXXXXXX
built with gcc ...
```

---

### Step 4: Run Tests

After completing steps 1-3, run the test suite:

```bash
python test_network_layer.py
```

**Expected output:**
```
================================================================================
 TEST SUMMARY
================================================================================
✓ PASS: Imports
✓ PASS: API Key
✓ PASS: Retry Logic
✓ PASS: CoreEngine Init
✓ PASS: AnimateDiff Init
✓ PASS: Method Signatures
✓ PASS: FFmpeg
================================================================================
Results: 7/7 tests passed

✅ All tests PASSED! Network layer implementation is ready.
```

---

## Test With Real API Calls

Once all tests pass, test with real API calls:

```bash
# Test CoreEngine with real API
python core_engine.py

# Test AnimateDiff with real API
python animatediff_engine.py
```

**⚠️ WARNING:** These tests will make REAL API calls to Fal.ai and consume credits!

**Expected costs:**
- `core_engine.py` test: ~$0.23 per video
- `animatediff_engine.py` test: ~$0.10 per video

Monitor usage at: https://fal.ai/dashboard

---

## Troubleshooting

### Issue 1: "No module named 'aiofiles'"

**Solution:**
```bash
pip install aiofiles==23.2.1
```

### Issue 2: "FAL_KEY is placeholder value"

**Solution:**
1. Get real key from https://fal.ai/dashboard/keys
2. Update `.env` file
3. Restart terminal

### Issue 3: "FFmpeg not found in PATH"

**Solution:**
- Windows: Follow Step 3 above
- Verify with: `ffmpeg -version`
- Restart terminal after installation

### Issue 4: "fal_client not installed"

**Solution:**
```bash
pip install fal-client>=0.5.6
```

### Issue 5: API calls timeout

**Possible causes:**
- Slow internet connection
- Fal.ai server overload
- Firewall blocking requests

**Solution:**
- Check internet connection
- Increase timeout in code if needed (see `NETWORK_LAYER_IMPLEMENTATION.md`)
- Check firewall settings

---

## Files Modified

### Core Implementation
- `core_engine.py` - Real API calls + retry logic
- `animatediff_engine.py` - Real API calls + retry logic + real FFmpeg extraction

### Documentation
- `NETWORK_LAYER_IMPLEMENTATION.md` - Complete technical report
- `SETUP_INSTRUCTIONS.md` - This file
- `test_network_layer.py` - Test suite

### Configuration
- `requirements.txt` - Already has all dependencies
- `.env` - Needs real FAL_KEY (user action required)

---

## Quick Start Checklist

- [ ] Run `pip install -r requirements.txt`
- [ ] Verify `pip show aiofiles` shows version 23.2.1
- [ ] Install FFmpeg (Step 3)
- [ ] Verify `ffmpeg -version` works
- [ ] Set real FAL_KEY in `.env`
- [ ] Run `python test_network_layer.py`
- [ ] All 7 tests pass
- [ ] Run `python core_engine.py` for real API test (costs ~$0.23)

---

## Support

If issues persist after following all steps:

1. Check logs in terminal for detailed error messages
2. Verify all dependencies: `pip list | grep -E "fal-client|httpx|aiofiles"`
3. Check FAL_KEY is valid: https://fal.ai/dashboard/keys
4. Review full documentation: `NETWORK_LAYER_IMPLEMENTATION.md`

---

## Next Steps After Setup

Once all tests pass:

1. **Integration Testing**
   - Test full pipeline with Celery tasks
   - Verify task monitoring with Flower
   - Check Redis queue status

2. **Production Deployment**
   - Review `DEPLOYMENT_CHECKLIST_PHASE2.md`
   - Set up monitoring (Sentry)
   - Configure load balancing

3. **Cost Optimization**
   - Monitor API usage
   - Implement caching strategies
   - Set up rate limiting

---

**Implementation Status:** ✅ COMPLETE  
**User Action Required:** ⚠️ Environment Setup  
**Estimated Setup Time:** 15-30 minutes
