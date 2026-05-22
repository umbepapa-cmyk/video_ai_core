# Multi-Agent Spatial Conditioning Guide

## Overview

The Multi-Agent Spatial Conditioning system enables high-fidelity video generation for **multiple subjects** (e.g., duo choreographies, sports pairs, partner dancing) while preventing **Latent Identity Bleed**.

### Key Features

✅ **Multi-Subject Identity Extraction**: Separate identity embeddings per subject
✅ **Skeleton-Based Spatial Tracking**: Automatic subject tracking across frames via IoU
✅ **Regional Prompting**: Position-aware prompt conditioning to prevent identity bleed
✅ **Kinematic Validation**: Automatic mismatch detection between subjects and skeletons
✅ **Backward Compatible**: Fully compatible with single-subject pipeline

---

## Architecture

### 1. Identity Isolation

Each subject gets an **independent identity super-vector**:

```
Subject 1: faces/donna/ → Identity Vector A (512-dim)
Subject 2: faces/uomo/  → Identity Vector B (512-dim)
```

No cross-contamination during extraction phase.

### 2. Spatial Conditioning

Motion reference video → OpenPose detection → Bounding box tracking:

```
Frame 0: [Subject 1: bbox(0.2, 0.3, 0.15, 0.6), Subject 2: bbox(0.65, 0.3, 0.15, 0.6)]
Frame 1: [Subject 1: bbox(0.21, 0.29, 0.16, 0.61), Subject 2: bbox(0.64, 0.31, 0.14, 0.59)]
...
```

Subjects are tracked via **IoU (Intersection over Union)** across frames.

### 3. Regional Prompting

First frame generation uses **spatial position descriptors**:

```
Subject 1 (left):  "A woman dancing elegantly on the left side"
Subject 2 (right): "A man dancing elegantly on the right side"

Combined: "A woman dancing elegantly on the left side | A man dancing elegantly on the right side"
```

---

## Quick Start

### Example 1: Duo Dance (2 Subjects)

```python
import asyncio
from core_engine import CoreEngine, CoreEngineConfig, QualityPreset

async def generate_duo_dance():
    # Define subjects
    subjects_payload = {
        "subject_1": "inputs/donna/",   # Folder with 5 face angles
        "subject_2": "inputs/uomo/"     # Folder with 5 face angles
    }
    
    # Configure engine
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=5,
        duration_seconds=10.0,
        quality_preset=QualityPreset.HIGH,
        use_controlnet=True,
        controlnet_map_path="references/duo_dance.mp4",  # Motion reference
        output_path="outputs/duo_dance/"
    )
    
    engine = CoreEngine(config=config)
    
    # Generate
    result = await engine.generate_high_fidelity_video(
        subjects_payload=subjects_payload,
        prompt="Two people dancing in synchronization, elegant ballroom style, cinematic lighting",
        controlnet_map_path="references/duo_dance.mp4",
        duration_seconds=10,
        output_path="outputs/duo_dance/"
    )
    
    print(f"✓ Video generated: {result.final_video_url}")
    print(f"  Subjects: {result.metadata['num_subjects']}")
    print(f"  Stability scores: {result.metadata['stability_scores']}")
    print(f"  Spatial conditioning: {result.metadata['spatial_conditioning']}")

asyncio.run(generate_duo_dance())
```

---

### Example 2: Sports Pair (2 Subjects)

```python
async def generate_sports_pair():
    subjects_payload = {
        "subject_1": "inputs/athlete_1/",
        "subject_2": "inputs/athlete_2/"
    }
    
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        duration_seconds=8.0,
        quality_preset=QualityPreset.ULTRA,
        controlnet_map_path="references/tennis_doubles.mp4"
    )
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        subjects_payload=subjects_payload,
        prompt="Two tennis players in synchronized serve motion, professional sports photography",
        controlnet_map_path="references/tennis_doubles.mp4",
        duration_seconds=8
    )
    
    return result

result = asyncio.run(generate_sports_pair())
```

---

### Example 3: Backward Compatibility (Single Subject)

```python
async def generate_single_subject():
    """Legacy single-subject mode still works!"""
    
    config = CoreEngineConfig(
        reference_faces_dir="inputs/single_person/",  # Old parameter
        duration_seconds=10.0,
        quality_preset=QualityPreset.HIGH
    )
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        reference_faces_dir="inputs/single_person/",
        prompt="A person walking gracefully, natural movement",
        duration_seconds=10
    )
    
    return result
```

---

## Input Requirements

### Subject Faces Directory Structure

Each subject requires **5 reference face angles**:

```
inputs/
├── donna/
│   ├── front.jpg       # 0°
│   ├── left_45.jpg     # -45°
│   ├── right_45.jpg    # +45°
│   ├── left_90.jpg     # -90°
│   └── right_90.jpg    # +90°
└── uomo/
    ├── front.jpg
    ├── left_45.jpg
    ├── right_45.jpg
    ├── left_90.jpg
    └── right_90.jpg
```

### Motion Reference Video Requirements

- **Format**: MP4, AVI, MOV
- **Resolution**: Minimum 512x512, recommended 720p+
- **Frame Rate**: 24+ FPS
- **Duration**: Any (will be processed frame-by-frame)
- **Subjects Visible**: All subjects must be visible in **first frame** for validation
- **Subject Count**: Must match number of subjects in `subjects_payload`

---

## Error Handling

### KinematicMismatchError

Raised when skeleton count ≠ subject count:

```python
try:
    result = await engine.generate_high_fidelity_video(
        subjects_payload={"subject_1": "...", "subject_2": "..."},
        controlnet_map_path="video_with_3_people.mp4"  # Mismatch!
    )
except KinematicMismatchError as e:
    print(f"Error: {e}")
    print(f"Expected: {e.expected_count} subjects")
    print(f"Detected: {e.detected_count} skeletons")
    # Fix: Use video with exactly 2 people
```

**Solutions:**
1. Use video with correct number of subjects
2. Verify all subjects are visible in first frame
3. Check video quality (blurry/occluded subjects may not detect)

### SubjectTrackingLossError

Raised when IoU tracking fails:

```python
try:
    result = await engine.generate_high_fidelity_video(...)
except SubjectTrackingLossError as e:
    print(f"Tracking lost: {e.lost_subject_id}")
    print(f"Last seen at frame: {e.last_known_frame}")
    # Fix: Use video with slower motion or better visibility
```

**Solutions:**
1. Use slower motion reference video
2. Ensure subjects don't leave frame
3. Avoid heavy occlusion between subjects

---

## Advanced Usage

### Custom Regional Prompting

Fine-tune spatial descriptors:

```python
# Override default position descriptors
def custom_bbox_to_position(bbox):
    x, y, w, h = bbox
    center_x = x + w / 2
    
    if center_x < 0.4:
        return "far left corner"
    elif center_x > 0.6:
        return "far right corner"
    else:
        return "center stage"

# Monkey-patch before generation
engine._bbox_to_position_descriptor = custom_bbox_to_position
```

### Accessing Spatial Masks

Retrieve bounding boxes for all frames:

```python
spatial_masks = engine.controlnet_handler.detect_multiple_skeletons(
    video_path="motion_ref.mp4",
    num_expected_subjects=2
)

# spatial_masks = {
#     "subject_1": [
#         {"frame": 0, "bbox": [0.2, 0.3, 0.15, 0.6], "keypoints": [...]},
#         {"frame": 1, "bbox": [0.21, 0.29, 0.16, 0.61], "keypoints": [...]},
#         ...
#     ],
#     "subject_2": [...]
# }

# Analyze motion patterns
for subject_id, detections in spatial_masks.items():
    print(f"\n{subject_id} trajectory:")
    for det in detections[:5]:  # First 5 frames
        bbox = det["bbox"]
        print(f"  Frame {det['frame']}: x={bbox[0]:.2f}, y={bbox[1]:.2f}")
```

### Identity Stability Analysis

Check per-subject identity quality:

```python
identity_vectors, stability_scores = await engine._extract_identity(subjects_payload)

for subject_id, score in stability_scores.items():
    if score < 0.85:
        print(f"⚠ {subject_id} has low stability: {score*100:.1f}%")
        print("  Consider using higher quality reference images")
    else:
        print(f"✓ {subject_id} stability: {score*100:.1f}%")
```

---

## Performance Metrics

### Typical Generation Times (2 Subjects)

| Stage | Single-Subject | Multi-Subject | Delta |
|-------|----------------|---------------|-------|
| Identity Extraction | 3s | 5-6s | +2-3s |
| Skeleton Detection | N/A | 8-12s | +8-12s |
| First Frame | 15s | 18-22s | +3-7s |
| Video Generation | 120s | 120s | 0s |
| **Total** | **~140s** | **~155-165s** | **+15-25s** |

**Optimization Tips:**
- Use lower resolution motion reference (512p vs 1080p) for faster skeleton detection
- Reduce `num_angles` from 5 to 3 for faster identity extraction
- Use `quality_preset=QualityPreset.STANDARD` instead of ULTRA

---

## Troubleshooting

### Issue: "Identity bleed detected in output"

**Symptoms:** Subject A's face appears on Subject B's body

**Causes:**
- Spatial masks not generated (ControlNet disabled or failed)
- Subjects too close together (overlapping bounding boxes)
- Regional prompting not properly applied

**Solutions:**
1. Enable ControlNet: `use_controlnet=True`
2. Provide motion reference video with clear subject separation
3. Verify spatial masks were generated: check `result.metadata['spatial_conditioning']`

### Issue: "Skeleton detection is slow"

**Solutions:**
1. Reduce video resolution: `ffmpeg -i input.mp4 -vf scale=512:512 output_512.mp4`
2. Reduce frame rate: `ffmpeg -i input.mp4 -r 12 output_12fps.mp4`
3. Install GPU-accelerated OpenPose: `pip install controlnet-aux[gpu]`

### Issue: "First frame has only one subject visible"

**Causes:**
- Regional prompting not supported by current Fal.ai endpoint
- Need specialized IP-Adapter with spatial conditioning

**Workarounds:**
1. Use strong positional language in prompt: "person on left side and person on right side"
2. Wait for Fal.ai regional IP-Adapter endpoint support
3. Consider alternative providers with spatial conditioning support

---

## API Reference

### CoreEngineConfig

```python
@dataclass
class CoreEngineConfig:
    # Multi-subject (new)
    subjects_payload: Optional[Dict[str, str]] = None
    # Format: {"subject_1": "path/", "subject_2": "path/"}
    
    # Single-subject (legacy)
    reference_faces_dir: Optional[str] = None
    
    # Identity settings
    num_angles: int = 5
    identity_adapter_strength: float = 0.95
    
    # ControlNet settings
    use_controlnet: bool = True
    controlnet_map_path: Optional[str] = None
    controlnet_strength: float = 0.8
    
    # Video settings
    duration_seconds: float = 10.0
    fps: int = 24
    motion_preset: str = "cinematic"
    
    # Quality
    quality_preset: QualityPreset = QualityPreset.HIGH
    
    @property
    def num_subjects(self) -> int:
        """Number of subjects in configuration."""
    
    @property
    def is_multi_subject(self) -> bool:
        """Whether multi-subject mode is enabled."""
```

### ControlNetHandler.detect_multiple_skeletons()

```python
def detect_multiple_skeletons(
    self,
    video_path: str,
    num_expected_subjects: int
) -> Dict[str, List[Dict]]:
    """
    Detect and track multiple skeletons across video frames.
    
    Returns:
        Dictionary mapping subject_id to list of detections:
        {
            "subject_1": [
                {"frame": 0, "bbox": [x, y, w, h], "keypoints": [...]},
                ...
            ],
            "subject_2": [...]
        }
    
    Raises:
        KinematicMismatchError: If detected != expected
        SubjectTrackingLossError: If tracking fails
    """
```

### Exceptions

```python
class KinematicMismatchError(Exception):
    """Skeleton count mismatch."""
    expected_count: int
    detected_count: int

class SubjectTrackingLossError(Exception):
    """Subject lost during tracking."""
    lost_subject_id: str
    last_known_frame: int
    total_frames: int

class IdentityBleedError(Exception):
    """Identity features leaked between subjects."""
    subject_ids: list
    similarity_score: float
```

---

## Roadmap

### Coming Soon

- [ ] **Per-frame identity conditioning** for video generation (currently only first frame)
- [ ] **True regional IP-Adapter** support (pending Fal.ai endpoint)
- [ ] **3+ subject support** (currently optimized for 2)
- [ ] **Automatic subject assignment** (face recognition-based matching)
- [ ] **Temporal identity consistency** scoring per subject
- [ ] **GPU-accelerated skeleton detection** via TorchScript OpenPose

---

## Credits

**Architecture:** Multi-Agent Spatial Conditioning  
**Identity Extraction:** Multi-Angle Identity Lock (Week 1 V2 - Day 4)  
**Skeleton Detection:** OpenPose via controlnet_aux  
**Regional Prompting:** Spatial position descriptors  
**Video Generation:** Wan I2V + AnimateDiff (Week 1 V2 - Day 5-6)

---

## License

Part of Week 1 V2 Core Engine.  
See main project LICENSE for details.
