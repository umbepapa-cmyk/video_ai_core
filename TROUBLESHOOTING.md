# Multi-Subject Generation - Troubleshooting Guide

## Common Issues & Solutions

---

## Issue 1: KinematicMismatchError

### Symptoms

```
KinematicMismatchError: Skeleton count mismatch in first frame
  Expected: 2 subjects
  Detected: 1 skeletons
```

### Root Causes

1. **Motion reference video has wrong number of subjects**
   - Video contains 1 person, but you provided 2 subjects in `subjects_payload`
   
2. **Subject not visible in first frame**
   - Second subject enters frame after first frame
   
3. **Skeleton detection failed**
   - Subject too small, blurry, or heavily occluded
   - Poor lighting or extreme poses

4. **Wrong video file**
   - Accidentally using single-person reference for duo generation

### Solutions

#### Solution A: Verify Video Content

```bash
# Extract first frame to inspect
ffmpeg -i motion_reference.mp4 -vframes 1 first_frame.png

# Open first_frame.png and verify:
# ✓ All subjects visible
# ✓ No heavy occlusion
# ✓ Good lighting
# ✓ Subjects not too small
```

#### Solution B: Test Skeleton Detection

```python
# Run skeleton detection standalone to debug
from controlnet_handler import ControlNetHandler

handler = ControlNetHandler()

try:
    spatial_masks = handler.detect_multiple_skeletons(
        video_path="motion_reference.mp4",
        num_expected_subjects=2
    )
    print(f"✓ Detected {len(spatial_masks)} subjects successfully")
except KinematicMismatchError as e:
    print(f"Detected {e.detected_count} skeletons")
    print("Check video content and ensure all subjects are visible")
```

#### Solution C: Fix Video

```bash
# If subjects enter frame late, trim video to start when all are visible
ffmpeg -i input.mp4 -ss 00:00:02 -t 00:00:10 output_trimmed.mp4

# If video has extra subjects, crop to region of interest
ffmpeg -i input.mp4 -vf "crop=800:600:100:50" output_cropped.mp4
```

#### Solution D: Adjust subjects_payload

```python
# If video actually has 1 subject, don't use multi-subject mode
config = CoreEngineConfig(
    reference_faces_dir="inputs/single_person/",  # Single subject
    # Remove subjects_payload
)
```

---

## Issue 2: SubjectTrackingLossError

### Symptoms

```
SubjectTrackingLossError: Failed to track subjects across frames
  Lost subject: subject_2
  Last seen at frame: 45/240
```

### Root Causes

1. **Subject leaves frame**
   - Person walks out of view
   
2. **Heavy occlusion**
   - Subjects overlap significantly
   - Objects block view
   
3. **Fast motion**
   - Subject moves too quickly between frames
   - IoU threshold (0.3) too high for rapid movement

4. **Detection failure**
   - Skeleton detector misses subject in some frames

### Solutions

#### Solution A: Check Video Quality

```python
# Inspect tracking trajectory
spatial_masks = handler.detect_multiple_skeletons(video_path, num_subjects=2)

for subject_id, detections in spatial_masks.items():
    print(f"\n{subject_id}: {len(detections)} detections")
    
    # Check for gaps
    frame_numbers = [d["frame"] for d in detections]
    expected_frames = set(range(min(frame_numbers), max(frame_numbers) + 1))
    missing_frames = expected_frames - set(frame_numbers)
    
    if missing_frames:
        print(f"  ⚠ Missing frames: {sorted(missing_frames)[:10]}")
```

#### Solution B: Reduce Motion Speed

```bash
# Slow down video to improve tracking
ffmpeg -i input.mp4 -filter:v "setpts=2.0*PTS" output_slow.mp4
```

#### Solution C: Increase Frame Rate

```bash
# Interpolate frames for smoother tracking
ffmpeg -i input.mp4 -vf "minterpolate=fps=60:mi_mode=mci" output_60fps.mp4
```

#### Solution D: Lower IoU Threshold (Advanced)

```python
# Modify IoU threshold for more lenient matching
# Edit controlnet_handler.py

def _find_best_match(self, ref_bbox, candidates, exclude_ids=None):
    # ...
    iou_threshold = 0.2  # Lowered from 0.3 for fast motion
    return best_match if best_iou > iou_threshold else None
```

---

## Issue 3: Identity Bleed in Output

### Symptoms

- Subject A's face appears on Subject B's body
- Both subjects have same facial features
- Identities are confused or swapped

### Root Causes

1. **Spatial conditioning disabled**
   - `use_controlnet=False` or no motion reference video provided
   
2. **Spatial masks not generated**
   - Skeleton detection failed silently
   
3. **Regional prompting not effective**
   - Fal.ai endpoint doesn't support regional IP-Adapter
   - Prompt-based guidance insufficient

4. **Subjects too similar**
   - Reference faces are same person or very similar

### Solutions

#### Solution A: Enable Spatial Conditioning

```python
config = CoreEngineConfig(
    subjects_payload=subjects_payload,
    use_controlnet=True,  # ✓ Enable
    controlnet_map_path="motion_reference.mp4"  # ✓ Provide
)
```

#### Solution B: Verify Spatial Masks Generated

```python
result = await engine.generate_high_fidelity_video(...)

# Check metadata
if result.metadata['spatial_conditioning']:
    print("✓ Spatial masks were used")
else:
    print("⚠ Spatial masks NOT generated - identity bleed likely")
    print("Reason: Check logs for skeleton detection errors")
```

#### Solution C: Strengthen Regional Prompts

```python
# Use stronger positional language
prompt = "Woman with long hair on the FAR LEFT | Man with beard on the FAR RIGHT"

# Instead of generic:
prompt = "Two people dancing"  # ❌ Weak spatial guidance
```

#### Solution D: Verify Distinct Identities

```python
# Check identity similarity before generation
from example_multi_subject import example_identity_analysis

vectors, scores = await example_identity_analysis()

# Cross-subject similarity should be LOW (<0.3)
vec1 = vectors["subject_1"]
vec2 = vectors["subject_2"]
similarity = (vec1 @ vec2) / ((vec1**2).sum()**0.5 * (vec2**2).sum()**0.5)

if abs(similarity) > 0.5:
    print("⚠ Subjects are too SIMILAR - may cause confusion")
    print("Solution: Use reference images of more distinct people")
```

---

## Issue 4: Low Identity Stability Score

### Symptoms

```
Identity extraction complete:
  subject_1: 72% stability  ⚠ LOW
  subject_2: 88% stability  ✓ GOOD
```

### Root Causes

1. **Poor quality reference images**
   - Low resolution (<512px)
   - Blurry or noisy
   - Heavy shadows or overexposed
   
2. **Inconsistent angles**
   - Face angles don't match expected (0°, ±45°, ±90°)
   - Different lighting across images
   
3. **Occlusions**
   - Glasses, masks, hands covering face
   - Hair covering significant portions

### Solutions

#### Solution A: Check Image Quality

```python
from PIL import Image

for subject_id, faces_dir in subjects_payload.items():
    print(f"\n{subject_id}:")
    
    for img_path in Path(faces_dir).glob("*.jpg"):
        img = Image.open(img_path)
        width, height = img.size
        
        if width < 512 or height < 512:
            print(f"  ⚠ {img_path.name}: {width}x{height} (too small)")
        else:
            print(f"  ✓ {img_path.name}: {width}x{height}")
```

#### Solution B: Improve Reference Images

Requirements for good identity extraction:

- **Resolution**: Minimum 512x512, recommended 1024x1024
- **Face Size**: Face should occupy 60-80% of frame
- **Lighting**: Consistent, well-lit, minimal shadows
- **Angles**: Clear front, ±45°, ±90° views
- **Expression**: Neutral, eyes open, no extreme expressions
- **Occlusion**: None (no glasses, masks, hands)

#### Solution C: Reduce num_angles

```python
# If you only have 3 good images instead of 5
config = CoreEngineConfig(
    subjects_payload=subjects_payload,
    num_angles=3,  # Reduced from 5
)

# Rename images: front.jpg, left_45.jpg, right_45.jpg
```

---

## Issue 5: Skeleton Detection Very Slow

### Symptoms

- `detect_multiple_skeletons()` takes 5+ minutes
- High CPU usage during detection
- Frame processing stuck or very slow

### Root Causes

1. **High resolution video**
   - 1080p or 4K video with many frames
   
2. **CPU-only OpenPose**
   - `controlnet_aux` using CPU instead of GPU
   
3. **Long video duration**
   - 60+ second videos = thousands of frames

### Solutions

#### Solution A: Reduce Video Resolution

```bash
# Downscale to 512p for faster processing
ffmpeg -i input_1080p.mp4 -vf scale=512:-1 output_512p.mp4

# Speed improvement: ~4x faster
```

#### Solution B: Trim Video Duration

```bash
# Extract only first 10 seconds
ffmpeg -i input.mp4 -t 10 -c copy output_10s.mp4

# Processing time scales linearly with duration
```

#### Solution C: Reduce Frame Rate

```bash
# Downsample to 12 FPS (from 24 or 30 FPS)
ffmpeg -i input.mp4 -r 12 output_12fps.mp4

# Speed improvement: ~2x faster (50% fewer frames)
```

#### Solution D: Install GPU-Accelerated OpenPose

```bash
# Install with CUDA support
pip install controlnet-aux[gpu]

# Verify GPU available
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# Speed improvement: 5-10x faster on GPU
```

---

## Issue 6: First Frame Has Wrong Subject Positions

### Symptoms

- First frame shows subjects in wrong spatial positions
- Subject A appears on right instead of left
- Subjects overlapping or in unexpected locations

### Root Causes

1. **Spatial masks not matching expectations**
   - Skeleton detection assigned wrong IDs
   
2. **Regional prompting not followed**
   - Model ignores position descriptors
   
3. **Left-to-right sorting failed**
   - Subjects not sorted correctly in first frame

### Solutions

#### Solution A: Verify Spatial Mask Positions

```python
spatial_masks = handler.detect_multiple_skeletons(video_path, num_subjects=2)

# Check first frame positions
for subject_id, detections in spatial_masks.items():
    first_bbox = detections[0]["bbox"]
    x_center = first_bbox[0] + first_bbox[2] / 2
    
    if x_center < 0.5:
        position = "LEFT"
    else:
        position = "RIGHT"
    
    print(f"{subject_id}: {position} (x_center={x_center:.2f})")

# Expected output:
# subject_1: LEFT (x_center=0.25)
# subject_2: RIGHT (x_center=0.75)
```

#### Solution B: Swap Subject IDs Manually

```python
# If skeleton detection assigns IDs incorrectly, swap them
subjects_payload_swapped = {
    "subject_1": subjects_payload["subject_2"],  # Swap
    "subject_2": subjects_payload["subject_1"]   # Swap
}

result = await engine.generate_high_fidelity_video(
    subjects_payload=subjects_payload_swapped,
    ...
)
```

#### Solution C: Use Explicit Positional Prompts

```python
# Override automatic position detection with explicit prompts
prompt_subject_1 = "Woman with red dress on the LEFT side of the frame"
prompt_subject_2 = "Man with blue suit on the RIGHT side of the frame"

combined_prompt = f"{prompt_subject_1} | {prompt_subject_2}"
```

---

## Issue 7: Video Generation Ignores Multi-Subject Setup

### Symptoms

- First frame looks correct (both subjects visible)
- Video shows only one subject or identity bleed

### Root Cause

**Current Limitation:** Video generation stage (Wan I2V) uses only the **primary subject's identity** vector. Per-frame multi-subject conditioning is not yet implemented.

### Workarounds

#### Workaround A: Emphasize First Frame

```python
# Generate longer videos with autoregressive loop
# This preserves first frame characteristics better

config = CoreEngineConfig(
    enable_autoregressive=True,
    segment_duration=5.0,
    duration_seconds=10.0,  # 2 segments
)
```

#### Workaround B: Post-Processing

Use video editing software to:
1. Generate separate videos for each subject
2. Composite them using masks
3. Blend results

#### Future Enhancement

Per-frame identity conditioning is planned for future release.

---

## Diagnostic Checklist

Before reporting issues, complete this checklist:

### Pre-Generation Checks

- [ ] **Reference Images**
  - [ ] All subjects have 5 angles (or specified `num_angles`)
  - [ ] Images are high resolution (512x512+)
  - [ ] Consistent lighting and quality
  - [ ] No heavy occlusions

- [ ] **Motion Reference Video** (if using multi-subject)
  - [ ] Contains exactly N subjects (matching `subjects_payload`)
  - [ ] All subjects visible in first frame
  - [ ] Clear, well-lit, minimal occlusion
  - [ ] Resolution 512p or higher

- [ ] **Configuration**
  - [ ] `use_controlnet=True` for multi-subject
  - [ ] `controlnet_map_path` provided
  - [ ] `subjects_payload` correctly formatted

### Post-Generation Checks

- [ ] **Identity Stability**
  - [ ] All subjects > 80% stability
  - [ ] Cross-subject similarity < 0.3

- [ ] **Spatial Conditioning**
  - [ ] `result.metadata['spatial_conditioning'] == True`
  - [ ] Skeleton detection completed without errors

- [ ] **Output Quality**
  - [ ] First frame shows both subjects
  - [ ] Subjects in correct positions
  - [ ] No obvious identity bleed

---

## Getting Help

### Log Collection

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Run generation
result = await engine.generate_high_fidelity_video(...)

# Save logs to file
# Copy console output to file for review
```

### Minimal Reproducible Example

```python
# Provide this when reporting issues

import asyncio
from core_engine import CoreEngine, CoreEngineConfig

async def reproduce_issue():
    config = CoreEngineConfig(
        subjects_payload={
            "subject_1": "path/to/subject1/",
            "subject_2": "path/to/subject2/"
        },
        controlnet_map_path="path/to/motion.mp4",
        use_controlnet=True
    )
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        subjects_payload=config.subjects_payload,
        prompt="Two people dancing",
        controlnet_map_path=config.controlnet_map_path
    )
    
    return result

asyncio.run(reproduce_issue())
```

### Information to Include

1. **Error message** (full traceback)
2. **Configuration** (full `CoreEngineConfig` dict)
3. **Input characteristics**:
   - Reference image resolutions
   - Motion video resolution, FPS, duration
   - Number of subjects
4. **Identity stability scores**
5. **Whether spatial masks were generated**
6. **Python version and library versions**:
   ```bash
   pip list | grep -E "(fal-client|opencv|numpy|controlnet)"
   ```

---

## Performance Optimization

### Quick Wins

```python
# Reduce processing time by 30-50%

config = CoreEngineConfig(
    num_angles=3,  # Instead of 5
    quality_preset=QualityPreset.STANDARD,  # Instead of ULTRA
)

# Downscale motion video to 512p
# Reduce motion video to 12 FPS
```

### Production Settings

```python
# Optimized for speed
config_fast = CoreEngineConfig(
    num_angles=3,
    quality_preset=QualityPreset.STANDARD,
    temporal_consistency=0.8,  # Lower for faster generation
)

# Optimized for quality
config_quality = CoreEngineConfig(
    num_angles=5,
    quality_preset=QualityPreset.ULTRA,
    temporal_consistency=0.95,
)
```

---

## Version Compatibility

This implementation requires:

- Python 3.8+
- fal-client >= 0.4.0
- opencv-python >= 4.8.0
- numpy >= 1.21.0
- controlnet-aux >= 0.0.7 (optional, for real OpenPose detection)

Check versions:

```bash
python --version
pip list | grep -E "(fal|opencv|numpy|controlnet)"
```
