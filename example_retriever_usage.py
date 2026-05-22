"""
Example: Dynamic Kinematic Retriever Usage
===========================================
Practical examples demonstrating the retriever in various scenarios.
"""

import asyncio
import logging
from pathlib import Path

from dynamic_retriever import (
    KinematicRetriever,
    RetrieverConfig,
    retrieve_motion_reference,
    RetrieverError,
    DownloadError
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# EXAMPLE 1: Basic Usage
# ============================================================================

async def example_1_basic_usage():
    """Example 1: Basic retrieval with default settings."""
    print("\n" + "="*70)
    print("EXAMPLE 1: Basic Usage")
    print("="*70 + "\n")
    
    # Initialize retriever with defaults
    retriever = KinematicRetriever()
    
    # Download a motion reference
    query = "ballet dancer spinning gracefully"
    
    try:
        logger.info(f"Requesting motion reference: '{query}'")
        
        video_path = await retriever.search_and_download(
            query=query,
            max_duration=8
        )
        
        logger.info(f"✓ Motion reference ready: {video_path}")
        logger.info(f"✓ File size: {Path(video_path).stat().st_size / 1024 / 1024:.2f} MB")
        
        return video_path
    
    except RetrieverError as e:
        logger.error(f"✗ Failed to retrieve motion reference: {e}")
        return None


# ============================================================================
# EXAMPLE 2: Custom Configuration
# ============================================================================

async def example_2_custom_config():
    """Example 2: Custom configuration for specific needs."""
    print("\n" + "="*70)
    print("EXAMPLE 2: Custom Configuration")
    print("="*70 + "\n")
    
    # Create custom configuration
    config = RetrieverConfig(
        cache_dir="./examples/custom_cache",
        max_duration=10,
        target_fps=30,  # Higher FPS for smooth motion
        target_resolution="1080p",  # Higher quality
        trim_duration=6,
        quality="best",  # Best available quality
        max_retries=5,
        timeout=180
    )
    
    retriever = KinematicRetriever(config=config)
    
    queries = [
        "martial arts kick slow motion",
        "parkour athlete jumping rooftop",
        "gymnast backflip tutorial"
    ]
    
    for query in queries:
        try:
            logger.info(f"Processing: '{query}'")
            video_path = await retriever.search_and_download(query, max_duration=5)
            logger.info(f"✓ Ready: {Path(video_path).name}")
        
        except RetrieverError as e:
            logger.error(f"✗ Failed: {e}")


# ============================================================================
# EXAMPLE 3: Cache Management
# ============================================================================

async def example_3_cache_management():
    """Example 3: Managing cache effectively."""
    print("\n" + "="*70)
    print("EXAMPLE 3: Cache Management")
    print("="*70 + "\n")
    
    retriever = KinematicRetriever()
    
    # Step 1: Check initial cache stats
    logger.info("Initial cache stats:")
    stats = retriever.get_cache_stats()
    logger.info(f"  Cached videos: {stats['num_cached_videos']}")
    logger.info(f"  Total size: {stats['total_size_mb']:.2f} MB")
    
    # Step 2: Download some videos
    queries = [
        "dance hip hop",
        "yoga pose warrior",
        "boxing jab combo"
    ]
    
    logger.info("\nDownloading motion references...")
    for query in queries:
        try:
            await retriever.search_and_download(query, max_duration=5)
            logger.info(f"✓ Downloaded: '{query}'")
        except RetrieverError as e:
            logger.warning(f"✗ Skipped '{query}': {e}")
    
    # Step 3: Check updated cache stats
    logger.info("\nUpdated cache stats:")
    stats = retriever.get_cache_stats()
    logger.info(f"  Cached videos: {stats['num_cached_videos']}")
    logger.info(f"  Total size: {stats['total_size_mb']:.2f} MB")
    logger.info(f"  Videos: {stats['cached_videos']}")
    
    # Step 4: Demonstrate cache hit
    logger.info("\nDemonstrating cache hit (instant access)...")
    import time
    start = time.time()
    video_path = await retriever.search_and_download(queries[0])
    elapsed = time.time() - start
    logger.info(f"✓ Cache hit in {elapsed*1000:.1f}ms: {video_path}")


# ============================================================================
# EXAMPLE 4: Integration with Video Generation Pipeline
# ============================================================================

async def example_4_integration():
    """Example 4: Integration with video generation pipeline."""
    print("\n" + "="*70)
    print("EXAMPLE 4: Integration with Video Generation")
    print("="*70 + "\n")
    
    retriever = KinematicRetriever()
    
    # Simulate video generation request
    user_request = {
        "user_id": "user-123",
        "reference_faces_dir": "./faces/user-123",
        "prompt": "A woman performing an elegant ballet spin",
        "motion_keyword": "ballet spinning pirouette",
        "duration_seconds": 10
    }
    
    logger.info("Video generation request received:")
    logger.info(f"  User: {user_request['user_id']}")
    logger.info(f"  Prompt: {user_request['prompt']}")
    logger.info(f"  Motion keyword: {user_request['motion_keyword']}")
    
    # Step 1: Check if motion reference is needed
    motion_keyword = user_request.get("motion_keyword")
    motion_path = None
    
    if motion_keyword:
        logger.info(f"\n→ Motion reference requested: '{motion_keyword}'")
        
        try:
            # Step 2: Retrieve motion reference (with caching)
            motion_path = await retriever.search_and_download(
                query=motion_keyword,
                max_duration=user_request["duration_seconds"]
            )
            
            logger.info(f"✓ Motion reference ready: {motion_path}")
        
        except RetrieverError as e:
            logger.warning(f"✗ Motion reference failed: {e}")
            logger.info("→ Continuing without motion reference")
    
    # Step 3: Proceed with video generation
    logger.info("\n→ Passing to core video generation engine...")
    logger.info(f"  Reference faces: {user_request['reference_faces_dir']}")
    logger.info(f"  Prompt: {user_request['prompt']}")
    logger.info(f"  ControlNet map: {motion_path or 'None'}")
    logger.info(f"  Duration: {user_request['duration_seconds']}s")
    
    # Simulate generation (would call core_engine here)
    logger.info("\n✓ Video generation complete!")
    
    return motion_path


# ============================================================================
# EXAMPLE 5: Batch Processing
# ============================================================================

async def example_5_batch_processing():
    """Example 5: Batch processing multiple motion references."""
    print("\n" + "="*70)
    print("EXAMPLE 5: Batch Processing")
    print("="*70 + "\n")
    
    retriever = KinematicRetriever()
    
    # List of motion references to pre-download
    motion_library = [
        "ballet spinning",
        "parkour jump",
        "martial arts kick",
        "yoga sun salutation",
        "dance contemporary",
        "boxing training",
        "running sprint",
        "swimming freestyle"
    ]
    
    logger.info(f"Pre-downloading {len(motion_library)} motion references...")
    
    # Download in parallel with error handling
    tasks = [
        retriever.search_and_download(query, max_duration=5)
        for query in motion_library
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Report results
    logger.info("\nBatch processing results:")
    success_count = 0
    fail_count = 0
    
    for query, result in zip(motion_library, results):
        if isinstance(result, Exception):
            logger.error(f"✗ {query}: {result}")
            fail_count += 1
        else:
            logger.info(f"✓ {query}: {Path(result).name}")
            success_count += 1
    
    logger.info(f"\nSummary: {success_count} succeeded, {fail_count} failed")
    
    # Show final cache stats
    stats = retriever.get_cache_stats()
    logger.info(f"Cache: {stats['num_cached_videos']} videos ({stats['total_size_mb']:.2f} MB)")


# ============================================================================
# EXAMPLE 6: Error Handling Patterns
# ============================================================================

async def example_6_error_handling():
    """Example 6: Robust error handling patterns."""
    print("\n" + "="*70)
    print("EXAMPLE 6: Error Handling Patterns")
    print("="*70 + "\n")
    
    retriever = KinematicRetriever()
    
    # Pattern 1: Simple try-except with fallback
    logger.info("Pattern 1: Simple fallback")
    try:
        video_path = await retriever.search_and_download("dance motion")
        logger.info(f"✓ Motion reference: {video_path}")
    except RetrieverError as e:
        logger.warning(f"Failed to retrieve: {e}")
        logger.info("→ Continuing without motion reference")
        video_path = None
    
    # Pattern 2: Specific error types
    logger.info("\nPattern 2: Specific error handling")
    try:
        video_path = await retriever.search_and_download("test query")
    except DownloadError as e:
        logger.error(f"Download error: {e}")
        logger.info("→ Possible causes: network timeout, video unavailable")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    
    # Pattern 3: Retry with alternative query
    logger.info("\nPattern 3: Retry with alternative")
    queries = ["specific dance move", "dance motion", "dancing"]
    video_path = None
    
    for query in queries:
        try:
            logger.info(f"Trying: '{query}'")
            video_path = await retriever.search_and_download(query, max_duration=5)
            logger.info(f"✓ Success: {video_path}")
            break
        except RetrieverError as e:
            logger.warning(f"✗ Failed: {e}")
            continue
    
    if not video_path:
        logger.warning("All attempts failed - using default behavior")


# ============================================================================
# EXAMPLE 7: Convenience Function
# ============================================================================

async def example_7_convenience():
    """Example 7: Using convenience function for quick retrieval."""
    print("\n" + "="*70)
    print("EXAMPLE 7: Convenience Function")
    print("="*70 + "\n")
    
    # Quick one-liner retrieval
    logger.info("Quick retrieval with convenience function...")
    
    try:
        video_path = await retrieve_motion_reference(
            query="martial arts kata",
            max_duration=5
        )
        
        logger.info(f"✓ Ready: {video_path}")
    
    except RetrieverError as e:
        logger.error(f"✗ Failed: {e}")


# ============================================================================
# Main Runner
# ============================================================================

async def main():
    """Run all examples."""
    print("\n" + "="*70)
    print("DYNAMIC KINEMATIC RETRIEVER - USAGE EXAMPLES")
    print("="*70)
    
    examples = [
        ("Basic Usage", example_1_basic_usage),
        ("Custom Configuration", example_2_custom_config),
        ("Cache Management", example_3_cache_management),
        ("Integration", example_4_integration),
        ("Batch Processing", example_5_batch_processing),
        ("Error Handling", example_6_error_handling),
        ("Convenience Function", example_7_convenience),
    ]
    
    print("\nAvailable examples:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    
    print("\n" + "-"*70)
    print("Running all examples (this may take several minutes)...")
    print("-"*70)
    
    for name, example_func in examples:
        try:
            await example_func()
        except Exception as e:
            logger.error(f"Example '{name}' failed: {e}")
    
    print("\n" + "="*70)
    print("✓ All examples completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    # Run all examples
    asyncio.run(main())
