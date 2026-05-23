"""
PHASE 2 SPRINT 2: Dynamic Kinematic Retrieval Agent
====================================================
Automatically downloads and caches motion reference videos from external sources.

Features:
- YouTube search & download with yt-dlp
- Smart caching with O(1) access
- FFmpeg post-processing (trim, normalize to 720p/24fps)
- Robust error handling with exponential backoff
- GDPR-compliant temporary file cleanup

Architecture:
Query → Cache Check → Download (if miss) → FFmpeg Processing → Cache Store → Return Path
"""

import os
import re
import logging
import asyncio
import subprocess
import hashlib
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import shutil

try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    logging.warning("yt-dlp not installed. Install with: pip install yt-dlp")

try:
    import ffmpeg
except ImportError:
    ffmpeg = None
    logging.warning("ffmpeg-python not installed. Install with: pip install ffmpeg-python")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class RetrieverConfig:
    """Configuration for Kinematic Retriever."""
    cache_dir: str = "assets/motion_references"
    max_duration: int = 10
    target_fps: int = 24
    target_resolution: str = "720p"
    trim_duration: int = 8
    quality: str = "best[height<=1080]"
    max_retries: int = 3
    timeout: int = 120


class RetrieverError(Exception):
    """Base exception for retriever errors."""
    pass


class DownloadError(RetrieverError):
    """Raised when video download fails."""
    pass


class ProcessingError(RetrieverError):
    """Raised when FFmpeg processing fails."""
    pass


class DiskSpaceError(RetrieverError):
    """Raised when insufficient disk space is available."""
    pass


class KinematicRetriever:
    """
    Dynamic Kinematic Retrieval Agent for motion reference videos.
    
    Features:
    - Searches YouTube for motion reference videos
    - Downloads best quality video (up to 1080p)
    - Trims to first 5-10 seconds (avoids scene cuts)
    - Normalizes to 720p/24fps for consistency
    - Caches locally with sanitized filenames
    - Robust error handling with retry logic
    
    Usage:
        retriever = KinematicRetriever()
        video_path = await retriever.search_and_download("ballet dancer spinning")
    """
    
    def __init__(self, config: Optional[RetrieverConfig] = None):
        """
        Initialize Kinematic Retriever.
        
        Args:
            config: Optional RetrieverConfig instance
        """
        self.config = config or RetrieverConfig()
        
        # Setup cache directory
        self.cache_dir = Path(self.config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Temporary download directory
        self.temp_dir = self.cache_dir / "_temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"KinematicRetriever initialized")
        logger.info(f"  Cache directory: {self.cache_dir}")
        logger.info(f"  Target resolution: {self.config.target_resolution}")
        logger.info(f"  Target FPS: {self.config.target_fps}")
        
        # Verify dependencies
        self._ffmpeg_available = self._ffmpeg_binary_available()
        self._verify_dependencies()
    
    def _ffmpeg_binary_available(self) -> bool:
        """Check whether the FFmpeg CLI is on PATH."""
        return shutil.which("ffmpeg") is not None
    
    def _verify_dependencies(self) -> None:
        """Verify that required dependencies are installed."""
        if yt_dlp is None:
            raise RuntimeError("yt-dlp not installed. Install with: pip install yt-dlp")
        
        if ffmpeg is None:
            logger.warning(
                "ffmpeg-python not installed. Trim/normalize disabled. "
                "Install with: pip install ffmpeg-python"
            )
        
        if self._ffmpeg_available:
            logger.info("✓ FFmpeg binary found")
        else:
            logger.warning("[WARNING CRITICO] FFmpeg non trovato nel PATH")
            logger.warning(
                "Trim/normalize verrà saltato; yt-dlp scaricherà il file integrale con avviso."
            )
    
    def _sanitize_filename(self, query: str) -> str:
        """
        Sanitize query string for safe filename.
        
        Args:
            query: Raw search query
            
        Returns:
            Sanitized filename (without extension)
        """
        # Create hash of query for consistent naming
        query_hash = hashlib.md5(query.lower().encode()).hexdigest()[:8]
        
        # Remove special characters and limit length
        sanitized = re.sub(r'[^\w\s-]', '', query)
        sanitized = re.sub(r'[-\s]+', '_', sanitized)
        sanitized = sanitized.strip('_')[:50]
        
        # Combine sanitized name with hash
        filename = f"{sanitized}_{query_hash}"
        
        logger.debug(f"Sanitized '{query}' → '{filename}'")
        return filename
    
    def _check_cache(self, query: str) -> Optional[Path]:
        """
        Check if video is already in cache (O(1) access).
        
        Args:
            query: Search query
            
        Returns:
            Path to cached video or None if cache miss
        """
        filename = self._sanitize_filename(query)
        cached_path = self.cache_dir / f"{filename}.mp4"
        
        if cached_path.exists() and cached_path.stat().st_size > 0:
            logger.info(f"✓ Cache HIT: {cached_path.name}")
            return cached_path
        
        logger.info(f"✗ Cache MISS: {filename}")
        return None
    
    def _check_disk_space(self, required_mb: int = 500) -> None:
        """
        Check if sufficient disk space is available.
        
        Args:
            required_mb: Required disk space in MB
            
        Raises:
            DiskSpaceError: If insufficient disk space
        """
        try:
            stat = shutil.disk_usage(self.cache_dir)
            free_mb = stat.free // (1024 * 1024)
            
            if free_mb < required_mb:
                raise DiskSpaceError(
                    f"Insufficient disk space: {free_mb}MB available, {required_mb}MB required"
                )
            
            logger.debug(f"Disk space check: {free_mb}MB available")
        except Exception as e:
            logger.warning(f"Could not check disk space: {e}")
    
    async def _download_video(
        self,
        query: str,
        max_duration: Optional[int] = None,
    ) -> Tuple[Path, dict]:
        """
        Download video from YouTube using yt-dlp.
        
        Args:
            query: Search query
            
        Returns:
            Tuple of (downloaded_path, metadata)
            
        Raises:
            DownloadError: If download fails
        """
        logger.info(f"Searching YouTube: '{query}'")
        
        # Generate temporary filename
        temp_filename = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        temp_path = self.temp_dir / f"{temp_filename}.%(ext)s"
        
        # Configure yt-dlp options
        ydl_opts = {
            'format': self.config.quality,
            'outtmpl': str(temp_path),
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
            'socket_timeout': self.config.timeout,
            'retries': self.config.max_retries,
            'fragment_retries': self.config.max_retries,
            'skip_unavailable_fragments': False,
            'ignoreerrors': False,
            'nocheckcertificate': False,
            'prefer_free_formats': False,
            'age_limit': None,
            'default_search': 'ytsearch1',
            'noplaylist': True,
            'playlistend': 1,
        }

        duration_limit = max_duration or self.config.trim_duration
        use_duration_filter = False
        if not self._ffmpeg_available and duration_limit:
            try:
                ydl_opts['match_filter'] = yt_dlp.utils.match_filter_func(
                    f"duration <= {duration_limit + 15}"
                )
                use_duration_filter = True
                logger.info(
                    "FFmpeg assente: filtro yt-dlp duration <= %ss",
                    duration_limit + 15,
                )
            except Exception as exc:
                logger.warning("Impossibile applicare match_filter yt-dlp: %s", exc)
        
        try:
            # Execute download with retry logic
            for attempt in range(self.config.max_retries):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        # Search query format: "ytsearch1:query motion reference"
                        search_query = f"ytsearch1:{query} motion reference"
                        
                        logger.info(f"Download attempt {attempt + 1}/{self.config.max_retries}")
                        
                        # Download video
                        info = ydl.extract_info(search_query, download=True)
                        
                        # Get actual downloaded file path
                        if 'entries' in info and len(info['entries']) > 0:
                            video_info = info['entries'][0]
                        else:
                            video_info = info
                        
                        # Find downloaded file
                        downloaded_files = [
                            p for p in self.temp_dir.glob(f"{temp_filename}.*")
                            if p.is_file() and not p.name.endswith(".part")
                        ]
                        if not downloaded_files and use_duration_filter:
                            logger.warning(
                                "Nessun video passa il filtro durata — "
                                "retry senza match_filter (file integrale)"
                            )
                            ydl_opts.pop("match_filter", None)
                            use_duration_filter = False
                            continue

                        if not downloaded_files:
                            raise DownloadError("Downloaded file not found")
                        
                        downloaded_path = downloaded_files[0]
                        
                        logger.info(f"✓ Video downloaded successfully")
                        logger.info(f"  Title: {video_info.get('title', 'Unknown')}")
                        logger.info(f"  Duration: {video_info.get('duration', 0)}s")
                        logger.info(f"  Resolution: {video_info.get('width', 0)}x{video_info.get('height', 0)}")
                        logger.info(f"  File: {downloaded_path.name}")
                        
                        return downloaded_path, video_info
                
                except yt_dlp.utils.DownloadError as e:
                    if attempt < self.config.max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Download failed: {e}")
                        logger.info(f"Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise DownloadError(f"Download failed after {self.config.max_retries} attempts: {e}")
                
                except Exception as e:
                    raise DownloadError(f"Unexpected error during download: {e}")
        
        except Exception as e:
            logger.error(f"Video download failed: {e}")
            raise DownloadError(f"Failed to download video: {e}")
    
    async def _trim_and_normalize(
        self,
        input_path: Path,
        output_path: Path,
        duration: int
    ) -> None:
        """
        Trim video to specified duration and normalize to 720p/24fps.
        
        Args:
            input_path: Path to input video
            output_path: Path to output video
            duration: Trim duration in seconds
            
        Raises:
            ProcessingError: If FFmpeg processing fails
        """
        logger.info(f"Processing video with FFmpeg...")
        logger.info(f"  Input: {input_path.name}")
        logger.info(f"  Output: {output_path.name}")
        logger.info(f"  Duration: {duration}s")
        logger.info(f"  Target: {self.config.target_resolution}@{self.config.target_fps}fps")
        
        try:
            # Resolution mapping
            resolution_map = {
                "480p": (854, 480),
                "720p": (1280, 720),
                "1080p": (1920, 1080),
            }
            
            target_width, target_height = resolution_map.get(
                self.config.target_resolution,
                (1280, 720)
            )
            
            # Build FFmpeg pipeline
            stream = ffmpeg.input(str(input_path))
            
            # Trim to specified duration (from start to avoid scene cuts)
            stream = stream.trim(start=0, duration=duration)
            
            # Set presentation timestamp
            stream = stream.setpts('PTS-STARTPTS')
            
            # Scale to target resolution (maintain aspect ratio)
            stream = ffmpeg.filter(stream, 'scale', target_width, target_height, force_original_aspect_ratio='decrease')
            stream = ffmpeg.filter(stream, 'pad', target_width, target_height, '(ow-iw)/2', '(oh-ih)/2')
            
            # Set frame rate
            stream = ffmpeg.filter(stream, 'fps', fps=self.config.target_fps)
            
            # Output with optimal encoding
            stream = ffmpeg.output(
                stream,
                str(output_path),
                vcodec='libx264',
                preset='medium',
                crf=23,
                acodec='aac',
                audio_bitrate='128k',
                movflags='faststart',
                pix_fmt='yuv420p'
            )
            
            # Overwrite output file if exists
            stream = ffmpeg.overwrite_output(stream)
            
            # Execute FFmpeg command
            logger.info("Executing FFmpeg command...")
            ffmpeg.run(stream, capture_stdout=True, capture_stderr=True, quiet=True)
            
            # Verify output
            if not output_path.exists():
                raise ProcessingError("Output file not created")
            
            output_size = output_path.stat().st_size
            if output_size == 0:
                raise ProcessingError("Output file is empty")
            
            logger.info(f"✓ Video processed successfully")
            logger.info(f"  Output size: {output_size / 1024 / 1024:.2f} MB")
        
        except ffmpeg.Error as e:
            stderr = e.stderr.decode() if e.stderr else "No error output"
            logger.error(f"FFmpeg error: {stderr}")
            raise ProcessingError(f"FFmpeg processing failed: {stderr}")
        
        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            raise ProcessingError(f"Failed to process video: {e}")
    
    async def _use_whole_file_fallback(
        self,
        input_path: Path,
        output_path: Path,
        duration: int,
    ) -> None:
        """
        Fallback when FFmpeg is unavailable: cache the downloaded file as-is.
        
        Avoids WinError 2 from missing ffmpeg binary.
        """
        logger.warning("[WARNING CRITICO] FFmpeg non trovato nel PATH")
        logger.warning(
            "Skip trim/normalize — uso file integrale (%s). "
            "Durata target %ss non garantita.",
            input_path.name,
            duration,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if input_path.resolve() == output_path.resolve():
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise ProcessingError("Input file missing or empty")
            return

        shutil.copy2(input_path, output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ProcessingError("Fallback copy produced empty output")

        logger.info(
            "✓ File integrale salvato in cache: %.2f MB",
            output_path.stat().st_size / 1024 / 1024,
        )
    
    def _cleanup_temp_files(self, *paths: Path) -> None:
        """
        Clean up temporary files.
        
        Args:
            *paths: Paths to clean up
        """
        for path in paths:
            if path and path.exists():
                try:
                    path.unlink()
                    logger.debug(f"Cleaned up: {path.name}")
                except Exception as e:
                    logger.warning(f"Failed to clean up {path.name}: {e}")
    
    async def search_and_download(
        self,
        query: str,
        max_duration: Optional[int] = None
    ) -> str:
        """
        Search, download, and cache motion reference video.
        
        This is the main entry point for the retriever.
        
        Args:
            query: Search query (e.g., "ballet dancer spinning")
            max_duration: Maximum video duration (default: from config)
            
        Returns:
            Absolute path to cached video file
            
        Raises:
            RetrieverError: If download or processing fails
            
        Example:
            retriever = KinematicRetriever()
            path = await retriever.search_and_download("parkour jump")
            print(f"Video ready: {path}")
        """
        logger.info("\n" + "="*70)
        logger.info("KINEMATIC RETRIEVER: SEARCH & DOWNLOAD")
        logger.info("="*70)
        logger.info(f"Query: '{query}'")
        logger.info("="*70)
        
        duration = max_duration or self.config.trim_duration
        
        try:
            # STEP 1: Check cache (O(1) access)
            cached_path = self._check_cache(query)
            if cached_path:
                logger.info(f"✓ Returning cached video: {cached_path}")
                return str(cached_path.absolute())
            
            # STEP 2: Check disk space
            self._check_disk_space()
            
            # STEP 3: Download video
            downloaded_path, metadata = await self._download_video(query, max_duration=duration)
            
            # STEP 4: Prepare output path
            filename = self._sanitize_filename(query)
            output_path = self.cache_dir / f"{filename}.mp4"
            
            # STEP 5: Trim and normalize with FFmpeg (or fallback without trim)
            if self._ffmpeg_available and ffmpeg is not None:
                await self._trim_and_normalize(
                    input_path=downloaded_path,
                    output_path=output_path,
                    duration=duration
                )
            else:
                await self._use_whole_file_fallback(
                    input_path=downloaded_path,
                    output_path=output_path,
                    duration=duration,
                )
            
            # STEP 6: Cleanup temporary files
            self._cleanup_temp_files(downloaded_path)
            
            # STEP 7: Verify final output
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise ProcessingError("Final output verification failed")
            
            logger.info(f"✓ Video ready in cache: {output_path.name}")
            logger.info("="*70 + "\n")
            
            return str(output_path.absolute())
        
        except DiskSpaceError as e:
            logger.error(f"Disk space error: {e}")
            raise
        
        except DownloadError as e:
            logger.error(f"Download error: {e}")
            raise
        
        except ProcessingError as e:
            logger.error(f"Processing error: {e}")
            raise
        
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise RetrieverError(f"Failed to retrieve video: {e}")
        
        finally:
            # Always cleanup temp directory
            try:
                for temp_file in self.temp_dir.glob("temp_*"):
                    self._cleanup_temp_files(temp_file)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory: {e}")
    
    def get_cache_stats(self) -> dict:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        cache_files = list(self.cache_dir.glob("*.mp4"))
        total_size = sum(f.stat().st_size for f in cache_files)
        
        return {
            "cache_dir": str(self.cache_dir),
            "num_cached_videos": len(cache_files),
            "total_size_mb": total_size / 1024 / 1024,
            "cached_videos": [f.stem for f in cache_files]
        }
    
    def clear_cache(self, older_than_days: Optional[int] = None) -> int:
        """
        Clear cache (all files or files older than specified days).
        
        Args:
            older_than_days: Only clear files older than this many days
            
        Returns:
            Number of files deleted
        """
        logger.info("Clearing cache...")
        
        deleted_count = 0
        cache_files = list(self.cache_dir.glob("*.mp4"))
        
        for cache_file in cache_files:
            try:
                if older_than_days:
                    file_age_days = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 86400
                    if file_age_days < older_than_days:
                        continue
                
                cache_file.unlink()
                deleted_count += 1
                logger.info(f"Deleted: {cache_file.name}")
            
            except Exception as e:
                logger.warning(f"Failed to delete {cache_file.name}: {e}")
        
        logger.info(f"✓ Cleared {deleted_count} files from cache")
        return deleted_count


# ============================================================================
# Convenience Functions
# ============================================================================

async def retrieve_motion_reference(query: str, max_duration: int = 8) -> str:
    """
    Convenience function for quick motion reference retrieval.
    
    Args:
        query: Search query
        max_duration: Maximum duration in seconds
        
    Returns:
        Path to cached video file
    """
    retriever = KinematicRetriever()
    return await retriever.search_and_download(query, max_duration=max_duration)


# ============================================================================
# Testing
# ============================================================================

if __name__ == "__main__":
    async def test_retriever():
        """Test the Kinematic Retriever."""
        print("\n" + "="*70)
        print("KINEMATIC RETRIEVER TEST")
        print("="*70 + "\n")
        
        # Test 1: Initialization
        print("Test 1: Initialization")
        print("-" * 70)
        
        config = RetrieverConfig(
            cache_dir="./test_cache",
            trim_duration=5,
            target_fps=24,
            target_resolution="720p"
        )
        
        retriever = KinematicRetriever(config=config)
        print(f"✓ Retriever initialized")
        print(f"  Cache dir: {retriever.cache_dir}")
        
        # Test 2: Cache stats
        print("\nTest 2: Initial Cache Stats")
        print("-" * 70)
        
        stats = retriever.get_cache_stats()
        print(f"✓ Cache stats:")
        print(f"  Cached videos: {stats['num_cached_videos']}")
        print(f"  Total size: {stats['total_size_mb']:.2f} MB")
        
        # Test 3: Download video
        print("\nTest 3: Download & Process")
        print("-" * 70)
        
        test_queries = [
            "ballet dancer spinning",
            "parkour athlete jumping"
        ]
        
        for query in test_queries:
            try:
                print(f"\nProcessing: '{query}'")
                video_path = await retriever.search_and_download(query, max_duration=5)
                print(f"✓ Video ready: {video_path}")
            except Exception as e:
                print(f"✗ Failed: {e}")
        
        # Test 4: Cache hit
        print("\nTest 4: Cache Hit Test")
        print("-" * 70)
        
        print(f"Re-requesting: '{test_queries[0]}'")
        video_path = await retriever.search_and_download(test_queries[0])
        print(f"✓ Should be instant (cache hit): {video_path}")
        
        # Test 5: Final cache stats
        print("\nTest 5: Final Cache Stats")
        print("-" * 70)
        
        stats = retriever.get_cache_stats()
        print(f"✓ Cache stats:")
        print(f"  Cached videos: {stats['num_cached_videos']}")
        print(f"  Total size: {stats['total_size_mb']:.2f} MB")
        print(f"  Videos: {stats['cached_videos']}")
        
        print("\n" + "="*70)
        print("✓ All tests completed!")
        print("="*70 + "\n")
    
    asyncio.run(test_retriever())
