"""
Multi-Agent Spatial Conditioning - Usage Examples
==================================================
Practical examples demonstrating multi-subject video generation.

Run with:
    python example_multi_subject.py
"""

import asyncio
import logging
from pathlib import Path

# Import core engine
from core_engine import (
    CoreEngine,
    CoreEngineConfig,
    QualityPreset,
    GenerationResult
)

# Import exceptions
from exceptions import KinematicMismatchError, SubjectTrackingLossError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# EXAMPLE 1: Duo Dance (2 Subjects)
# ============================================================================

async def example_duo_dance():
    """
    Generate a video of two people dancing together.
    
    Demonstrates:
    - Multi-subject identity extraction
    - Spatial skeleton tracking
    - Regional prompting
    - Kinematic validation
    """
    print("\n" + "="*80)
    print("EXAMPLE 1: DUO DANCE (2 SUBJECTS)")
    print("="*80)
    
    # Define subjects
    subjects_payload = {
        "subject_1": "inputs/donna/",   # Female dancer
        "subject_2": "inputs/uomo/"     # Male dancer
    }
    
    # Check if directories exist (for demo purposes)
    for subject_id, path in subjects_payload.items():
        if not Path(path).exists():
            logger.warning(f"{subject_id} directory not found: {path}")
            logger.info("Creating mock directory structure for demonstration...")
            Path(path).mkdir(parents=True, exist_ok=True)
    
    # Configure engine for multi-subject
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=5,
        duration_seconds=10.0,
        fps=24,
        quality_preset=QualityPreset.HIGH,
        use_controlnet=True,
        controlnet_map_path="references/duo_dance.mp4",
        enable_autoregressive=False,  # Single segment for demo
        output_path="outputs/duo_dance/"
    )
    
    logger.info(f"Configuration created:")
    logger.info(f"  Mode: {'Multi-Subject' if config.is_multi_subject else 'Single-Subject'}")
    logger.info(f"  Subjects: {config.num_subjects}")
    logger.info(f"  Quality: {config.quality_preset.value}")
    
    # Initialize engine
    engine = CoreEngine(config=config)
    
    try:
        # Generate video
        result = await engine.generate_high_fidelity_video(
            subjects_payload=subjects_payload,
            prompt="Two people dancing in synchronization, elegant ballroom style, cinematic lighting",
            controlnet_map_path="references/duo_dance.mp4",
            duration_seconds=10,
            output_path="outputs/duo_dance/"
        )
        
        # Print results
        print("\n" + "-"*80)
        print("✓ GENERATION COMPLETE")
        print("-"*80)
        print(f"Video URL: {result.final_video_url}")
        print(f"Duration: {result.duration_seconds}s")
        print(f"Subjects: {result.metadata['num_subjects']}")
        print(f"\nIdentity Stability:")
        for subject_id, score in result.metadata['stability_scores'].items():
            print(f"  {subject_id}: {score*100:.1f}%")
        print(f"\nSpatial Conditioning: {result.metadata['spatial_conditioning']}")
        print(f"Generation Time: {result.total_generation_time:.1f}s")
        
        return result
        
    except KinematicMismatchError as e:
        logger.error(f"❌ Kinematic Mismatch Error:")
        logger.error(f"  Expected: {e.expected_count} subjects")
        logger.error(f"  Detected: {e.detected_count} skeletons")
        logger.error(f"  Solution: Use motion reference video with exactly 2 subjects")
        return None
    
    except SubjectTrackingLossError as e:
        logger.error(f"❌ Subject Tracking Lost:")
        logger.error(f"  Lost: {e.lost_subject_id}")
        logger.error(f"  Last seen: frame {e.last_known_frame}/{e.total_frames}")
        logger.error(f"  Solution: Use slower motion or better visibility")
        return None


# ============================================================================
# EXAMPLE 2: Sports Pair (2 Athletes)
# ============================================================================

async def example_sports_pair():
    """
    Generate a video of two athletes performing synchronized movements.
    
    Demonstrates:
    - High-quality preset
    - Sports-specific prompting
    - Motion reference validation
    """
    print("\n" + "="*80)
    print("EXAMPLE 2: SPORTS PAIR (SYNCHRONIZED TENNIS)")
    print("="*80)
    
    subjects_payload = {
        "subject_1": "inputs/athlete_1/",
        "subject_2": "inputs/athlete_2/"
    }
    
    # Create mock directories
    for subject_id, path in subjects_payload.items():
        Path(path).mkdir(parents=True, exist_ok=True)
    
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        duration_seconds=8.0,
        quality_preset=QualityPreset.ULTRA,  # Maximum quality
        use_controlnet=True,
        controlnet_map_path="references/tennis_doubles.mp4",
        temporal_consistency=0.95,  # High temporal consistency for sports
        output_path="outputs/sports_pair/"
    )
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        subjects_payload=subjects_payload,
        prompt="Two tennis players in synchronized serve motion, professional sports photography, dynamic action",
        controlnet_map_path="references/tennis_doubles.mp4",
        duration_seconds=8,
        output_path="outputs/sports_pair/"
    )
    
    print(f"\n✓ Sports pair video generated: {result.final_video_url}")
    return result


# ============================================================================
# EXAMPLE 3: Single Subject (Backward Compatibility)
# ============================================================================

async def example_single_subject():
    """
    Generate video with single subject using legacy API.
    
    Demonstrates:
    - Backward compatibility
    - Automatic conversion to multi-subject format internally
    """
    print("\n" + "="*80)
    print("EXAMPLE 3: SINGLE SUBJECT (LEGACY MODE)")
    print("="*80)
    
    # Old-style single subject configuration
    config = CoreEngineConfig(
        reference_faces_dir="inputs/single_person/",  # Legacy parameter
        duration_seconds=10.0,
        quality_preset=QualityPreset.HIGH,
        output_path="outputs/single_subject/"
    )
    
    # Internally converts to subjects_payload = {"subject_1": "inputs/single_person/"}
    logger.info(f"Subjects payload (auto-converted): {config.subjects_payload}")
    logger.info(f"Is multi-subject: {config.is_multi_subject}")  # False (only 1 subject)
    
    Path("inputs/single_person/").mkdir(parents=True, exist_ok=True)
    
    engine = CoreEngine(config=config)
    
    result = await engine.generate_high_fidelity_video(
        reference_faces_dir="inputs/single_person/",
        prompt="A person walking gracefully, natural movement, outdoor scene",
        duration_seconds=10,
        output_path="outputs/single_subject/"
    )
    
    print(f"\n✓ Single subject video generated: {result.final_video_url}")
    return result


# ============================================================================
# EXAMPLE 4: Skeleton Detection Demo
# ============================================================================

async def example_skeleton_detection():
    """
    Demonstrate skeleton detection and tracking without full generation.
    
    Useful for:
    - Validating motion reference videos
    - Debugging spatial tracking
    - Analyzing subject trajectories
    """
    print("\n" + "="*80)
    print("EXAMPLE 4: SKELETON DETECTION DEMO")
    print("="*80)
    
    from controlnet_handler import ControlNetHandler
    
    handler = ControlNetHandler()
    
    video_path = "references/duo_dance.mp4"
    num_expected_subjects = 2
    
    logger.info(f"Analyzing video: {video_path}")
    logger.info(f"Expected subjects: {num_expected_subjects}")
    
    try:
        # Detect skeletons
        spatial_masks = handler.detect_multiple_skeletons(
            video_path=video_path,
            num_expected_subjects=num_expected_subjects
        )
        
        # Analyze results
        print("\n" + "-"*80)
        print("SKELETON DETECTION RESULTS")
        print("-"*80)
        
        for subject_id, detections in spatial_masks.items():
            print(f"\n{subject_id}:")
            print(f"  Total detections: {len(detections)}")
            
            # First 5 frames
            print(f"  First 5 frames:")
            for det in detections[:5]:
                bbox = det["bbox"]
                print(f"    Frame {det['frame']}: bbox=[{bbox[0]:.2f}, {bbox[1]:.2f}, {bbox[2]:.2f}, {bbox[3]:.2f}]")
            
            # Calculate trajectory statistics
            x_positions = [det["bbox"][0] for det in detections]
            y_positions = [det["bbox"][1] for det in detections]
            
            print(f"  Trajectory stats:")
            print(f"    X range: [{min(x_positions):.2f}, {max(x_positions):.2f}]")
            print(f"    Y range: [{min(y_positions):.2f}, {max(y_positions):.2f}]")
            print(f"    Movement: {max(x_positions) - min(x_positions):.2f} (horizontal)")
        
        return spatial_masks
        
    except KinematicMismatchError as e:
        logger.error(f"❌ Mismatch: Expected {e.expected_count}, detected {e.detected_count}")
        return None


# ============================================================================
# EXAMPLE 5: Identity Stability Analysis
# ============================================================================

async def example_identity_analysis():
    """
    Analyze identity extraction quality for multiple subjects.
    
    Useful for:
    - Validating reference face quality
    - Identifying problematic subjects
    - Quality assurance before generation
    """
    print("\n" + "="*80)
    print("EXAMPLE 5: IDENTITY STABILITY ANALYSIS")
    print("="*80)
    
    subjects_payload = {
        "subject_1": "inputs/donna/",
        "subject_2": "inputs/uomo/"
    }
    
    # Create mock directories
    for path in subjects_payload.values():
        Path(path).mkdir(parents=True, exist_ok=True)
    
    config = CoreEngineConfig(
        subjects_payload=subjects_payload,
        num_angles=5,
        output_path="outputs/temp/"
    )
    
    engine = CoreEngine(config=config)
    
    # Extract identities without full generation
    logger.info("Extracting identity super-vectors...")
    
    identity_vectors, stability_scores = await engine._extract_identity(subjects_payload)
    
    # Analyze results
    print("\n" + "-"*80)
    print("IDENTITY ANALYSIS RESULTS")
    print("-"*80)
    
    for subject_id, score in stability_scores.items():
        vector = identity_vectors[subject_id]
        
        print(f"\n{subject_id}:")
        print(f"  Stability Score: {score*100:.1f}%")
        print(f"  Vector Norm: {(vector**2).sum()**0.5:.4f}")
        print(f"  Vector Dimension: {vector.shape[0]}")
        
        # Quality assessment
        if score >= 0.90:
            quality = "✓ EXCELLENT"
        elif score >= 0.80:
            quality = "⚠ GOOD (consider better angles)"
        else:
            quality = "❌ POOR (use higher quality images)"
        
        print(f"  Quality: {quality}")
    
    # Cross-subject similarity (should be LOW for distinct identities)
    vec1 = identity_vectors["subject_1"]
    vec2 = identity_vectors["subject_2"]
    
    # Cosine similarity
    similarity = (vec1 @ vec2) / ((vec1**2).sum()**0.5 * (vec2**2).sum()**0.5)
    
    print(f"\nCross-Subject Similarity: {similarity:.4f}")
    if abs(similarity) < 0.3:
        print("  ✓ Identities are DISTINCT (good for multi-subject)")
    else:
        print("  ⚠ Identities are SIMILAR (may cause confusion)")
    
    return identity_vectors, stability_scores


# ============================================================================
# MAIN TEST SUITE
# ============================================================================

async def run_all_examples():
    """Run all examples sequentially."""
    
    print("\n" + "="*80)
    print("MULTI-AGENT SPATIAL CONDITIONING - USAGE EXAMPLES")
    print("="*80)
    print("\nThis script demonstrates:")
    print("  1. Duo dance (2 subjects)")
    print("  2. Sports pair (synchronized athletes)")
    print("  3. Single subject (backward compatibility)")
    print("  4. Skeleton detection demo")
    print("  5. Identity stability analysis")
    print("\n" + "="*80 + "\n")
    
    results = {}
    
    # Example 1: Duo Dance
    try:
        results['duo_dance'] = await example_duo_dance()
    except Exception as e:
        logger.error(f"Example 1 failed: {e}")
    
    # Example 2: Sports Pair
    try:
        results['sports_pair'] = await example_sports_pair()
    except Exception as e:
        logger.error(f"Example 2 failed: {e}")
    
    # Example 3: Single Subject
    try:
        results['single_subject'] = await example_single_subject()
    except Exception as e:
        logger.error(f"Example 3 failed: {e}")
    
    # Example 4: Skeleton Detection
    try:
        results['skeleton_detection'] = await example_skeleton_detection()
    except Exception as e:
        logger.error(f"Example 4 failed: {e}")
    
    # Example 5: Identity Analysis
    try:
        results['identity_analysis'] = await example_identity_analysis()
    except Exception as e:
        logger.error(f"Example 5 failed: {e}")
    
    # Summary
    print("\n" + "="*80)
    print("EXAMPLES SUMMARY")
    print("="*80)
    
    for name, result in results.items():
        status = "✓" if result is not None else "❌"
        print(f"{status} {name}")
    
    print("\n" + "="*80)
    print("✓ All examples completed!")
    print("="*80 + "\n")
    
    return results


if __name__ == "__main__":
    # Run all examples
    asyncio.run(run_all_examples())
    
    # Or run individual examples:
    # asyncio.run(example_duo_dance())
    # asyncio.run(example_skeleton_detection())
    # asyncio.run(example_identity_analysis())
