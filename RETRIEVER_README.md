# Dynamic Kinematic Retrieval Agent - Quick Start

## What is it?

A production-ready agent that automatically downloads and caches motion reference videos from YouTube when they're not already stored locally. Perfect for AI video generation pipelines that need dynamic ControlNet references.

## Quick Start (3 steps)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `yt-dlp` (YouTube downloader)
- `ffmpeg-python` (FFmpeg wrapper)

### 2. Install FFmpeg Binary

**Windows:**
```bash
choco install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

### 3. Use it!

```python
from tasks import generate_video_task

# That's it! The agent runs automatically when motion_keyword is provided
result = generate_video_task.delay(
    user_id="user-123",
    reference_faces_dir="./faces/user-123",
    prompt="A woman dancing elegantly",
    motion_keyword="ballet spinning",  # ← Auto-downloads if not cached
    duration_seconds=10
)
```

## How It Works

```
motion_keyword="ballet spinning"
    ↓
Check cache (instant if cached)
    ↓
Cache MISS? Download from YouTube
    ↓
Trim to 8s + normalize to 720p/24fps
    ↓
Save to cache
    ↓
Pass to video generation as ControlNet reference
```

## Features

- ✅ **Smart Caching**: O(1) lookup, only downloads once per query
- ✅ **Auto-Processing**: Trims and normalizes videos automatically
- ✅ **Robust**: Retries 3 times with exponential backoff
- ✅ **Production-Ready**: Comprehensive error handling and logging
- ✅ **Zero Config**: Works out-of-the-box with sensible defaults

## Files Overview

| File | Purpose |
|------|---------|
| `dynamic_retriever.py` | Main implementation (560 lines) |
| `test_dynamic_retriever.py` | Test suite (450 lines) |
| `example_retriever_usage.py` | 7 practical examples |
| `RETRIEVER_USAGE.md` | Complete usage guide |
| `IMPLEMENTATION_SUMMARY.md` | Technical documentation |
| `tasks.py` (modified) | Celery integration |
| `requirements.txt` (modified) | Added dependencies |

## Testing

```bash
# Quick test (requires network)
python dynamic_retriever.py

# Full test suite
pytest test_dynamic_retriever.py -v

# Examples
python example_retriever_usage.py
```

## Configuration (Optional)

Default settings work great, but you can customize:

```python
from dynamic_retriever import KinematicRetriever, RetrieverConfig

config = RetrieverConfig(
    cache_dir="./my_cache",
    trim_duration=10,
    target_resolution="1080p",
    target_fps=30
)

retriever = KinematicRetriever(config=config)
```

## Cache Management

```python
from dynamic_retriever import KinematicRetriever

retriever = KinematicRetriever()

# Check cache stats
stats = retriever.get_cache_stats()
print(f"Cached: {stats['num_cached_videos']} videos")

# Clear old cache (30+ days)
deleted = retriever.clear_cache(older_than_days=30)
```

## Error Handling

The agent handles errors gracefully:

```python
# If download fails, video generation continues WITHOUT motion reference
# No need for manual error handling - it's built in!

result = generate_video_task.delay(
    user_id="user-123",
    reference_faces_dir="./faces/user-123",
    prompt="A woman dancing",
    motion_keyword="ballet spinning",  # If this fails, continues anyway
    duration_seconds=10
)
```

## Performance

- **Cache hit**: ~1ms (instant)
- **Cache miss**: ~30-60s (download + processing)
- **Disk usage**: ~10MB per cached video

## Common Use Cases

### 1. Dynamic Motion References

```python
# User specifies motion type
generate_video_task.delay(
    ...,
    motion_keyword="parkour jump"
)
```

### 2. Pre-warming Cache

```python
# Download popular motions in advance
async def prewarm():
    retriever = KinematicRetriever()
    
    for motion in ["ballet spin", "parkour jump", "martial arts kick"]:
        await retriever.search_and_download(motion)
```

### 3. Fallback Pattern

```python
# Try dynamic retrieval, fallback to manual map
generate_video_task.delay(
    ...,
    motion_keyword="custom dance",
    controlnet_map_path="./fallback.mp4"
)
```

## Troubleshooting

### "FFmpeg not found"
Install FFmpeg binary (see step 2 above)

### "Download failed"
- Check internet connection
- Try different query
- Check logs for specific error

### "Processing failed"
- Verify FFmpeg is installed
- Check disk space
- Clear temp directory

## Documentation

- **Quick Start**: This file
- **Full Guide**: `RETRIEVER_USAGE.md` (comprehensive)
- **Technical Details**: `IMPLEMENTATION_SUMMARY.md`
- **Examples**: `example_retriever_usage.py`
- **Tests**: `test_dynamic_retriever.py`

## What's Next?

1. Install dependencies (`pip install -r requirements.txt`)
2. Install FFmpeg binary
3. Test it (`python dynamic_retriever.py`)
4. Use it in your video generation pipeline!

## Support

- Check `RETRIEVER_USAGE.md` for detailed troubleshooting
- Run tests to verify setup: `pytest test_dynamic_retriever.py`
- Enable debug logging: `logging.basicConfig(level=logging.DEBUG)`

---

**That's it!** The Dynamic Kinematic Retrieval Agent is ready to use. 🚀
