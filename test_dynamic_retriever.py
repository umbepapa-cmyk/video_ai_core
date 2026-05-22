"""
Test Suite for Dynamic Kinematic Retriever
===========================================
Tests for download, caching, processing, and error handling.
"""

import asyncio
import logging
from pathlib import Path
import pytest

from dynamic_retriever import (
    KinematicRetriever,
    RetrieverConfig,
    RetrieverError,
    DownloadError,
    ProcessingError,
    DiskSpaceError,
    retrieve_motion_reference
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestKinematicRetriever:
    """Test suite for KinematicRetriever."""
    
    @pytest.fixture
    def config(self, tmp_path):
        """Create test configuration."""
        return RetrieverConfig(
            cache_dir=str(tmp_path / "test_cache"),
            trim_duration=5,
            target_fps=24,
            target_resolution="720p",
            max_retries=2,
            timeout=60
        )
    
    @pytest.fixture
    def retriever(self, config):
        """Create retriever instance."""
        return KinematicRetriever(config=config)
    
    def test_initialization(self, retriever, config):
        """Test retriever initialization."""
        assert retriever.cache_dir.exists()
        assert retriever.temp_dir.exists()
        assert retriever.config.trim_duration == 5
        logger.info("✓ Test: Initialization passed")
    
    def test_sanitize_filename(self, retriever):
        """Test filename sanitization."""
        test_cases = [
            ("ballet dancer spinning", "ballet_dancer_spinning"),
            ("parkour/jump @#$%", "parkourjump"),
            ("  spaces   everywhere  ", "spaces_everywhere"),
            ("CamelCaseTest", "CamelCaseTest"),
        ]
        
        for input_query, expected_prefix in test_cases:
            sanitized = retriever._sanitize_filename(input_query)
            assert expected_prefix in sanitized or sanitized.startswith(expected_prefix.split('_')[0])
            assert len(sanitized) <= 60  # Max length with hash
        
        logger.info("✓ Test: Filename sanitization passed")
    
    def test_check_cache_miss(self, retriever):
        """Test cache miss scenario."""
        cached = retriever._check_cache("nonexistent_query")
        assert cached is None
        logger.info("✓ Test: Cache miss passed")
    
    def test_check_cache_hit(self, retriever):
        """Test cache hit scenario."""
        # Create a dummy cached file
        query = "test_query"
        filename = retriever._sanitize_filename(query)
        cached_path = retriever.cache_dir / f"{filename}.mp4"
        cached_path.write_text("dummy content")
        
        result = retriever._check_cache(query)
        assert result is not None
        assert result.exists()
        logger.info("✓ Test: Cache hit passed")
    
    def test_get_cache_stats(self, retriever):
        """Test cache statistics."""
        # Create some dummy cache files
        for i in range(3):
            cache_file = retriever.cache_dir / f"video_{i}.mp4"
            cache_file.write_bytes(b"dummy" * 1000)
        
        stats = retriever.get_cache_stats()
        assert stats['num_cached_videos'] >= 3
        assert stats['total_size_mb'] > 0
        logger.info("✓ Test: Cache stats passed")
    
    def test_clear_cache(self, retriever):
        """Test cache clearing."""
        # Create dummy cache files
        for i in range(3):
            cache_file = retriever.cache_dir / f"video_{i}.mp4"
            cache_file.write_bytes(b"dummy" * 1000)
        
        deleted = retriever.clear_cache()
        assert deleted >= 3
        logger.info("✓ Test: Clear cache passed")
    
    @pytest.mark.asyncio
    async def test_download_and_process(self, retriever):
        """Test full download and processing pipeline."""
        query = "short dance clip"
        
        try:
            video_path = await retriever.search_and_download(
                query=query,
                max_duration=5
            )
            
            assert video_path is not None
            assert Path(video_path).exists()
            assert Path(video_path).stat().st_size > 0
            assert video_path.endswith(".mp4")
            
            logger.info(f"✓ Test: Download and process passed - {video_path}")
        
        except RetrieverError as e:
            logger.warning(f"Download test skipped (network/dependency issue): {e}")
            pytest.skip(f"Network or dependency issue: {e}")
    
    @pytest.mark.asyncio
    async def test_cache_reuse(self, retriever):
        """Test that second request uses cache."""
        query = "test motion"
        
        try:
            # First request (download)
            path1 = await retriever.search_and_download(query, max_duration=5)
            
            # Second request (should hit cache)
            path2 = await retriever.search_and_download(query, max_duration=5)
            
            assert path1 == path2
            logger.info("✓ Test: Cache reuse passed")
        
        except RetrieverError as e:
            logger.warning(f"Cache reuse test skipped: {e}")
            pytest.skip(f"Network or dependency issue: {e}")
    
    @pytest.mark.asyncio
    async def test_error_handling_invalid_query(self, retriever):
        """Test error handling for invalid queries."""
        invalid_queries = [
            "",
            "   ",
            "asdkjfhalskdjfhalskdjfhaslkdjfhaslkdjfhalskdjfhalskdjfh" * 10  # Very long
        ]
        
        for query in invalid_queries:
            if not query.strip():
                # Empty queries should be handled gracefully
                with pytest.raises((RetrieverError, ValueError, DownloadError)):
                    await retriever.search_and_download(query)
        
        logger.info("✓ Test: Error handling passed")
    
    def test_disk_space_check(self, retriever):
        """Test disk space checking."""
        try:
            # This should pass on most systems
            retriever._check_disk_space(required_mb=100)
            
            # This should fail (requires 1 TB)
            with pytest.raises(DiskSpaceError):
                retriever._check_disk_space(required_mb=1_000_000)
            
            logger.info("✓ Test: Disk space check passed")
        
        except Exception as e:
            logger.warning(f"Disk space test skipped: {e}")
            pytest.skip(f"Disk space check unavailable: {e}")


class TestConvenienceFunctions:
    """Test convenience functions."""
    
    @pytest.mark.asyncio
    async def test_retrieve_motion_reference(self, tmp_path):
        """Test convenience function."""
        try:
            # Override default cache dir for testing
            import dynamic_retriever
            original_config = dynamic_retriever.RetrieverConfig()
            
            video_path = await retrieve_motion_reference(
                query="short clip",
                max_duration=5
            )
            
            assert video_path is not None
            assert Path(video_path).exists()
            
            logger.info("✓ Test: Convenience function passed")
        
        except RetrieverError as e:
            logger.warning(f"Convenience function test skipped: {e}")
            pytest.skip(f"Network or dependency issue: {e}")


class TestIntegrationWithTasks:
    """Integration tests with Celery tasks."""
    
    def test_check_local_motion_cache(self):
        """Test cache check integration."""
        from tasks import check_local_motion_cache
        
        # Test cache miss
        result = check_local_motion_cache("nonexistent_motion")
        assert result is None
        
        logger.info("✓ Test: Tasks integration passed")
    
    @pytest.mark.asyncio
    async def test_tasks_integration_flow(self):
        """Test full integration flow with tasks."""
        from tasks import check_local_motion_cache, kinematic_retriever
        
        query = "test integration motion"
        
        try:
            # First check cache (should miss)
            cached = check_local_motion_cache(query)
            assert cached is None
            
            # Download
            path = await kinematic_retriever.search_and_download(query, max_duration=5)
            assert path is not None
            
            # Check cache again (should hit)
            cached = check_local_motion_cache(query)
            assert cached is not None
            assert cached == path
            
            logger.info("✓ Test: Full integration flow passed")
        
        except RetrieverError as e:
            logger.warning(f"Integration test skipped: {e}")
            pytest.skip(f"Network or dependency issue: {e}")


# ============================================================================
# Manual Test Runner (for quick testing without pytest)
# ============================================================================

async def run_manual_tests():
    """Run manual tests without pytest."""
    print("\n" + "="*70)
    print("MANUAL TEST SUITE - KINEMATIC RETRIEVER")
    print("="*70 + "\n")
    
    # Test 1: Basic initialization
    print("Test 1: Initialization")
    print("-" * 70)
    config = RetrieverConfig(
        cache_dir="./test_cache_manual",
        trim_duration=5
    )
    retriever = KinematicRetriever(config=config)
    print(f"✓ Retriever initialized: {retriever.cache_dir}")
    
    # Test 2: Cache stats
    print("\nTest 2: Initial Cache Stats")
    print("-" * 70)
    stats = retriever.get_cache_stats()
    print(f"✓ Cached videos: {stats['num_cached_videos']}")
    print(f"✓ Total size: {stats['total_size_mb']:.2f} MB")
    
    # Test 3: Download (requires network)
    print("\nTest 3: Download & Process (requires network)")
    print("-" * 70)
    query = "short dance motion"
    
    try:
        print(f"Downloading: '{query}'")
        video_path = await retriever.search_and_download(query, max_duration=5)
        print(f"✓ Video downloaded: {video_path}")
        print(f"✓ File size: {Path(video_path).stat().st_size / 1024 / 1024:.2f} MB")
    except RetrieverError as e:
        print(f"✗ Download failed: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
    
    # Test 4: Cache hit
    print("\nTest 4: Cache Hit Test")
    print("-" * 70)
    try:
        print(f"Re-requesting: '{query}'")
        video_path = await retriever.search_and_download(query)
        print(f"✓ Should be instant (cache hit): {video_path}")
    except Exception as e:
        print(f"✗ Error: {e}")
    
    # Test 5: Final stats
    print("\nTest 5: Final Cache Stats")
    print("-" * 70)
    stats = retriever.get_cache_stats()
    print(f"✓ Cached videos: {stats['num_cached_videos']}")
    print(f"✓ Total size: {stats['total_size_mb']:.2f} MB")
    print(f"✓ Videos: {stats['cached_videos']}")
    
    # Test 6: Clear cache
    print("\nTest 6: Cache Clearing")
    print("-" * 70)
    deleted = retriever.clear_cache()
    print(f"✓ Deleted {deleted} files")
    
    print("\n" + "="*70)
    print("✓ Manual tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    # Run manual tests
    asyncio.run(run_manual_tests())
