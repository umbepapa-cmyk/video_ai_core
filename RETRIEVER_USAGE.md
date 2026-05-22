# Dynamic Kinematic Retrieval Agent - Usage Guide

## Overview

The **Dynamic Kinematic Retrieval Agent** automatically downloads and caches motion reference videos from external sources (YouTube) when not available in local cache.

## Features

- **Automatic YouTube Search & Download**: Searches for motion reference videos using yt-dlp
- **Smart Caching**: O(1) cache lookup with sanitized filenames
- **FFmpeg Post-Processing**: Trims to 5-10 seconds and normalizes to 720p/24fps
- **Robust Error Handling**: Exponential backoff retry with comprehensive error types
- **GDPR-Compliant**: Automatic cleanup of temporary files

## Architecture

```
Query → Cache Check → Download (yt-dlp) → FFmpeg Processing → Cache Store → Return Path
```

## Installation

### 1. Update Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `yt-dlp>=2024.3.10` - YouTube video downloader
- `ffmpeg-python>=0.2.0` - FFmpeg Python wrapper

### 2. Install FFmpeg Binary

**Windows:**
```powershell
# Using Chocolatey
choco install ffmpeg

# Or download from: https://ffmpeg.org/download.html
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

### 3. Verify Installation

```bash
ffmpeg -version
```

## Basic Usage

### Standalone Usage

```python
import asyncio
from dynamic_retriever import KinematicRetriever

async def main():
    # Initialize retriever
    retriever = KinematicRetriever()
    
    # Download motion reference
    video_path = await retriever.search_and_download(
        query="ballet dancer spinning",
        max_duration=8
    )
    
    print(f"Video ready: {video_path}")

asyncio.run(main())
```

### Convenience Function

```python
from dynamic_retriever import retrieve_motion_reference

# Quick retrieval
video_path = await retrieve_motion_reference(
    query="parkour jump",
    max_duration=5
)
```

### Integration with Celery Tasks

```python
from tasks import generate_video_task

# Submit video generation with motion reference
result = generate_video_task.delay(
    user_id="user-uuid",
    reference_faces_dir="./faces/user-uuid",
    prompt="A woman dancing gracefully",
    motion_keyword="ballet spinning",  # ← Motion reference keyword
    duration_seconds=10
)
```

## Configuration

### Custom Configuration

```python
from dynamic_retriever import KinematicRetriever, RetrieverConfig

config = RetrieverConfig(
    cache_dir="./custom_cache",
    max_duration=10,
    target_fps=24,
    target_resolution="720p",
    trim_duration=8,
    quality="best[height<=1080]",
    max_retries=3,
    timeout=120
)

retriever = KinematicRetriever(config=config)
```

### Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cache_dir` | str | `"assets/motion_references"` | Cache directory path |
| `max_duration` | int | `10` | Maximum video duration (seconds) |
| `target_fps` | int | `24` | Target frame rate |
| `target_resolution` | str | `"720p"` | Target resolution (480p/720p/1080p) |
| `trim_duration` | int | `8` | Trim duration (seconds) |
| `quality` | str | `"best[height<=1080]"` | yt-dlp quality selector |
| `max_retries` | int | `3` | Maximum retry attempts |
| `timeout` | int | `120` | Download timeout (seconds) |

## Cache Management

### Check Cache Stats

```python
stats = retriever.get_cache_stats()

print(f"Cached videos: {stats['num_cached_videos']}")
print(f"Total size: {stats['total_size_mb']:.2f} MB")
print(f"Videos: {stats['cached_videos']}")
```

### Clear Cache

```python
# Clear all cache
deleted = retriever.clear_cache()

# Clear cache older than 30 days
deleted = retriever.clear_cache(older_than_days=30)
```

## Error Handling

### Error Types

1. **RetrieverError** - Base exception for all retriever errors
2. **DownloadError** - Video download failed (network, video unavailable, etc.)
3. **ProcessingError** - FFmpeg processing failed
4. **DiskSpaceError** - Insufficient disk space

### Handling Errors

```python
from dynamic_retriever import (
    KinematicRetriever,
    DownloadError,
    ProcessingError,
    DiskSpaceError
)

retriever = KinematicRetriever()

try:
    video_path = await retriever.search_and_download("ballet spinning")
    print(f"Success: {video_path}")

except DownloadError as e:
    print(f"Download failed: {e}")
    # Fallback: continue without motion reference

except ProcessingError as e:
    print(f"Processing failed: {e}")
    # Fallback: use raw downloaded video

except DiskSpaceError as e:
    print(f"Insufficient disk space: {e}")
    # Cleanup old cache or abort

except Exception as e:
    print(f"Unexpected error: {e}")
```

## Advanced Usage

### Custom Search Query

The retriever automatically appends "motion reference" to searches. To override:

```python
# Will search: "ballet spinning motion reference"
video_path = await retriever.search_and_download("ballet spinning")

# For more specific results, include context
video_path = await retriever.search_and_download(
    "ballet dancer spinning pirouette slow motion"
)
```

### Retry Logic

The retriever includes automatic retry with exponential backoff:

```python
config = RetrieverConfig(
    max_retries=5,  # Retry up to 5 times
    timeout=180     # 3-minute timeout per attempt
)

retriever = KinematicRetriever(config=config)
```

### Logging

Enable detailed logging:

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("dynamic_retriever")
```

## Integration Examples

### Example 1: Basic Video Generation with Motion Reference

```python
from tasks import generate_video_task

result = generate_video_task.delay(
    user_id="user-123",
    reference_faces_dir="./faces/user-123",
    prompt="A person dancing elegantly",
    motion_keyword="ballet dance",
    duration_seconds=10
)

print(f"Job ID: {result.id}")
```

### Example 2: Fallback to Manual ControlNet Map

```python
from tasks import generate_video_task

# Try motion keyword first, fallback to manual map
result = generate_video_task.delay(
    user_id="user-123",
    reference_faces_dir="./faces/user-123",
    prompt="A person jumping",
    motion_keyword="parkour jump",
    controlnet_map_path="./manual_pose_map.mp4",  # Fallback
    duration_seconds=10
)
```

### Example 3: Batch Processing with Multiple Motion Keywords

```python
import asyncio
from dynamic_retriever import KinematicRetriever

async def batch_download():
    retriever = KinematicRetriever()
    
    queries = [
        "ballet spinning",
        "parkour jump",
        "martial arts kick",
        "dance hip hop"
    ]
    
    tasks = [
        retriever.search_and_download(query, max_duration=5)
        for query in queries
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for query, result in zip(queries, results):
        if isinstance(result, Exception):
            print(f"✗ {query}: {result}")
        else:
            print(f"✓ {query}: {result}")

asyncio.run(batch_download())
```

## Testing

### Run Tests with Pytest

```bash
# Install pytest
pip install pytest pytest-asyncio

# Run all tests
pytest test_dynamic_retriever.py -v

# Run specific test
pytest test_dynamic_retriever.py::TestKinematicRetriever::test_initialization -v
```

### Run Manual Tests

```bash
python test_dynamic_retriever.py
```

### Test with Real Network

```bash
# Test download (requires network)
python dynamic_retriever.py
```

## Performance Considerations

### Cache Hit Performance

- **Cache hit**: O(1) - Instant path lookup (~1ms)
- **Cache miss**: O(n) - Download + processing (~30-120s depending on network)

### Optimization Tips

1. **Pre-warm cache**: Download common motion references in advance
2. **Batch downloads**: Use `asyncio.gather()` for parallel downloads
3. **Cache expiry**: Use `clear_cache(older_than_days=30)` to manage disk space
4. **Quality settings**: Lower quality for faster downloads

### Disk Space Management

```python
# Monitor cache size
stats = retriever.get_cache_stats()
if stats['total_size_mb'] > 5000:  # Over 5 GB
    retriever.clear_cache(older_than_days=7)
```

## Troubleshooting

### Issue: FFmpeg not found

**Error:** `FFmpeg binary not found in PATH`

**Solution:**
1. Install FFmpeg (see Installation section)
2. Add FFmpeg to system PATH
3. Restart terminal/IDE

### Issue: Video download fails

**Error:** `DownloadError: Download failed after 3 attempts`

**Possible Causes:**
- Network timeout
- Video age-restricted or removed
- Geographic restrictions

**Solutions:**
1. Check internet connection
2. Try different search query
3. Increase timeout in config

### Issue: Processing fails

**Error:** `ProcessingError: FFmpeg processing failed`

**Possible Causes:**
- Corrupted download
- Unsupported codec
- Insufficient disk space

**Solutions:**
1. Clear temp directory: `rm -rf assets/motion_references/_temp/*`
2. Check disk space
3. Verify FFmpeg installation

### Issue: Cache not working

**Symptom:** Videos re-downloaded every time

**Possible Causes:**
- Different query variations (e.g., "dance" vs "dancing")
- Cache directory deleted
- Filesystem permissions

**Solutions:**
1. Use consistent query strings
2. Check cache directory exists and is writable
3. Verify cache stats: `retriever.get_cache_stats()`

## Best Practices

1. **Query Specificity**: Use descriptive queries for better results
   - ✓ Good: "ballet dancer spinning pirouette"
   - ✗ Bad: "dance"

2. **Cache Management**: Implement periodic cleanup
   ```python
   # Weekly cleanup of old cache
   retriever.clear_cache(older_than_days=7)
   ```

3. **Error Handling**: Always handle retriever errors gracefully
   ```python
   try:
       video_path = await retriever.search_and_download(query)
   except RetrieverError:
       # Continue without motion reference
       video_path = None
   ```

4. **Logging**: Enable logging for debugging
   ```python
   import logging
   logging.basicConfig(level=logging.INFO)
   ```

5. **Testing**: Test with real network before production
   ```bash
   python test_dynamic_retriever.py
   ```

## API Reference

### KinematicRetriever

#### `__init__(config: Optional[RetrieverConfig] = None)`
Initialize retriever with optional configuration.

#### `async search_and_download(query: str, max_duration: Optional[int] = None) -> str`
Main entry point. Returns absolute path to cached video.

#### `get_cache_stats() -> dict`
Get cache statistics (num files, total size, file list).

#### `clear_cache(older_than_days: Optional[int] = None) -> int`
Clear cache. Returns number of files deleted.

### RetrieverConfig

Configuration dataclass with all tunable parameters.

### Exceptions

- `RetrieverError` - Base exception
- `DownloadError` - Download failures
- `ProcessingError` - FFmpeg failures
- `DiskSpaceError` - Insufficient disk space

## License

This module is part of the AppVideoAI project. All rights reserved.

## Support

For issues or questions:
1. Check troubleshooting section
2. Enable debug logging
3. Check GitHub issues
4. Contact development team
