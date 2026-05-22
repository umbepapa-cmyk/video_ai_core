"""
WEEK 4 - DAY 27: Celebrity Blocker Module
==========================================
Sistema di protezione biometrica per prevenire deepfake di figure pubbliche.

Features:
- Face embedding extraction con InsightFace
- Cosine similarity comparison
- Protected identities database
- Automatic blocking (403 Forbidden)
- Audit trail logging

Compliance:
- Prevents non-consensual deepfakes of public figures
- Ethical AI usage
- Legal protection
"""

import os
import cv2
import numpy as np
import pickle
import logging
from typing import List, Tuple, Optional, Dict
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BlockReason(str, Enum):
    """Reasons for blocking an identity."""
    CELEBRITY = "celebrity"
    POLITICIAN = "politician"
    PUBLIC_FIGURE = "public_figure"
    PROTECTED = "protected"


@dataclass
class ProtectedIdentity:
    """Data structure for protected identity."""
    name: str
    embedding: np.ndarray
    category: BlockReason
    added_date: str
    metadata: Optional[Dict] = None


class CelebrityBlockingError(Exception):
    """Exception raised when a protected identity is detected."""
    pass


class CelebrityBlocker:
    """
    Sistema di protezione biometrica per prevenire deepfake
    di figure pubbliche non consenzienti.
    
    Uses InsightFace ArcFace model for high-accuracy face recognition.
    Blocks identity if cosine similarity >= threshold (default 0.85).
    """
    
    SIMILARITY_THRESHOLD = 0.85
    MODEL_NAME = 'buffalo_l'  # InsightFace model
    
    def __init__(
        self,
        embeddings_db_path: str = "celebrity_embeddings.pkl",
        similarity_threshold: float = 0.85
    ):
        """
        Initialize celebrity blocker.
        
        Args:
            embeddings_db_path: Path to protected embeddings database
            similarity_threshold: Cosine similarity threshold (0-1)
        """
        self.embeddings_db_path = Path(embeddings_db_path)
        self.similarity_threshold = similarity_threshold
        self.protected_embeddings: Dict[str, ProtectedIdentity] = {}
        
        # Initialize InsightFace
        self._init_face_analyzer()
        
        # Load protected embeddings database
        if self.embeddings_db_path.exists():
            self._load_embeddings_database()
        else:
            logger.warning(
                f"Protected embeddings database not found: {embeddings_db_path}. "
                "Creating empty database."
            )
            self._create_empty_database()
    
    def _init_face_analyzer(self):
        """Initialize InsightFace face analyzer."""
        try:
            from insightface.app import FaceAnalysis
            
            self.face_analyzer = FaceAnalysis(name=self.MODEL_NAME)
            
            # Prepare with GPU if available, else CPU
            ctx_id = 0 if self._is_gpu_available() else -1
            self.face_analyzer.prepare(ctx_id=ctx_id, det_size=(640, 640))
            
            logger.info(f"InsightFace initialized (ctx_id={ctx_id})")
            
        except ImportError:
            logger.error("InsightFace not installed. Install: pip install insightface")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize InsightFace: {e}")
            raise
    
    def _is_gpu_available(self) -> bool:
        """Check if GPU is available for inference."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    def _load_embeddings_database(self):
        """Load protected embeddings from disk."""
        try:
            with open(self.embeddings_db_path, 'rb') as f:
                self.protected_embeddings = pickle.load(f)
            
            logger.info(
                f"Loaded {len(self.protected_embeddings)} protected identities "
                f"from {self.embeddings_db_path}"
            )
            
        except Exception as e:
            logger.error(f"Failed to load embeddings database: {e}")
            self._create_empty_database()
    
    def _create_empty_database(self):
        """Create empty embeddings database."""
        self.protected_embeddings = {}
        self.save_database()
        logger.info(f"Created empty embeddings database: {self.embeddings_db_path}")
    
    def save_database(self):
        """Save protected embeddings to disk."""
        try:
            with open(self.embeddings_db_path, 'wb') as f:
                pickle.dump(self.protected_embeddings, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            logger.info(f"Saved {len(self.protected_embeddings)} protected identities")
            
        except Exception as e:
            logger.error(f"Failed to save embeddings database: {e}")
            raise
    
    def extract_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """
        Extract face embedding from image.
        
        Args:
            image_path: Path to image file
        
        Returns:
            Face embedding array (512-dim for ArcFace) or None if no face detected
        """
        try:
            # Read image
            img = cv2.imread(str(image_path))
            
            if img is None:
                logger.error(f"Failed to read image: {image_path}")
                return None
            
            # Detect faces
            faces = self.face_analyzer.get(img)
            
            if not faces:
                logger.warning(f"No faces detected in image: {image_path}")
                return None
            
            # Use largest face (by bounding box area)
            face = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            
            # Return normalized embedding
            embedding = face.normed_embedding
            
            logger.debug(f"Extracted embedding with shape: {embedding.shape}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"Failed to extract embedding: {e}")
            return None
    
    def cosine_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """
        Calculate cosine similarity between two embeddings.
        
        Args:
            emb1: First embedding
            emb2: Second embedding
        
        Returns:
            Cosine similarity (0-1, higher = more similar)
        """
        # Normalize embeddings
        emb1_norm = emb1 / np.linalg.norm(emb1)
        emb2_norm = emb2 / np.linalg.norm(emb2)
        
        # Compute dot product
        similarity = np.dot(emb1_norm, emb2_norm)
        
        return float(similarity)
    
    def check_if_protected(
        self,
        image_path: str
    ) -> Tuple[bool, Optional[str], float, Optional[BlockReason]]:
        """
        Check if image matches a protected identity.
        
        Args:
            image_path: Path to image to check
        
        Returns:
            Tuple of (is_protected, identity_name, max_similarity, block_reason)
        """
        # Extract embedding from input image
        embedding = self.extract_embedding(image_path)
        
        if embedding is None:
            # No face detected - not protected (fail open)
            return False, None, 0.0, None
        
        # Compare against all protected embeddings
        max_similarity = 0.0
        matched_identity: Optional[ProtectedIdentity] = None
        
        for identity in self.protected_embeddings.values():
            similarity = self.cosine_similarity(embedding, identity.embedding)
            
            if similarity > max_similarity:
                max_similarity = similarity
                matched_identity = identity
        
        # Check if similarity exceeds threshold
        is_protected = max_similarity >= self.similarity_threshold
        
        if is_protected and matched_identity:
            logger.warning(
                f"Protected identity detected: {matched_identity.name} "
                f"(similarity: {max_similarity:.3f}, category: {matched_identity.category})"
            )
            
            return (
                True,
                matched_identity.name,
                max_similarity,
                matched_identity.category
            )
        
        return False, None, max_similarity, None
    
    def add_protected_identity(
        self,
        name: str,
        image_path: str,
        category: BlockReason = BlockReason.PROTECTED,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Add a new protected identity to the database.
        
        Args:
            name: Identity name (unique identifier)
            image_path: Path to reference image
            category: Reason for protection
            metadata: Additional metadata
        
        Returns:
            True if added successfully, False otherwise
        """
        try:
            # Extract embedding
            embedding = self.extract_embedding(image_path)
            
            if embedding is None:
                logger.error(f"Cannot add {name}: no face detected in {image_path}")
                return False
            
            # Create protected identity
            from datetime import datetime
            
            identity = ProtectedIdentity(
                name=name,
                embedding=embedding,
                category=category,
                added_date=datetime.utcnow().isoformat(),
                metadata=metadata or {}
            )
            
            # Add to database
            self.protected_embeddings[name] = identity
            
            # Save to disk
            self.save_database()
            
            logger.info(f"Added protected identity: {name} ({category})")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to add protected identity {name}: {e}")
            return False
    
    def remove_protected_identity(self, name: str) -> bool:
        """
        Remove a protected identity from the database.
        
        Args:
            name: Identity name to remove
        
        Returns:
            True if removed, False if not found
        """
        if name in self.protected_embeddings:
            del self.protected_embeddings[name]
            self.save_database()
            logger.info(f"Removed protected identity: {name}")
            return True
        else:
            logger.warning(f"Protected identity not found: {name}")
            return False
    
    def list_protected_identities(self) -> List[Dict]:
        """
        List all protected identities.
        
        Returns:
            List of identity info dicts
        """
        return [
            {
                "name": identity.name,
                "category": identity.category.value,
                "added_date": identity.added_date,
                "metadata": identity.metadata
            }
            for identity in self.protected_embeddings.values()
        ]
    
    def get_statistics(self) -> Dict:
        """
        Get statistics about the protected identities database.
        
        Returns:
            Statistics dict
        """
        categories = {}
        for identity in self.protected_embeddings.values():
            category = identity.category.value
            categories[category] = categories.get(category, 0) + 1
        
        return {
            "total_identities": len(self.protected_embeddings),
            "categories": categories,
            "similarity_threshold": self.similarity_threshold,
            "model": self.MODEL_NAME
        }


# ============================================================================
# Utility Functions
# ============================================================================

def create_demo_database(output_path: str = "celebrity_embeddings.pkl"):
    """
    Create a demo protected identities database.
    
    Note: In production, you would populate this with actual celebrity faces.
    For demo purposes, we create an empty database.
    """
    blocker = CelebrityBlocker(embeddings_db_path=output_path)
    
    # In production, add protected identities like:
    # blocker.add_protected_identity(
    #     name="Example Celebrity",
    #     image_path="path/to/celebrity/photo.jpg",
    #     category=BlockReason.CELEBRITY,
    #     metadata={"occupation": "actor"}
    # )
    
    blocker.save_database()
    logger.info(f"Created demo database: {output_path}")


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    print("Celebrity Blocker Module - Week 4, Day 27")
    print("=" * 60)
    
    # Initialize blocker
    blocker = CelebrityBlocker()
    
    # Display statistics
    stats = blocker.get_statistics()
    print(f"\nDatabase Statistics:")
    print(f"  Total Protected Identities: {stats['total_identities']}")
    print(f"  Similarity Threshold: {stats['similarity_threshold']}")
    print(f"  Model: {stats['model']}")
    
    if stats['categories']:
        print(f"  Categories:")
        for category, count in stats['categories'].items():
            print(f"    - {category}: {count}")
    
    print("\nUsage Example:")
    print("  blocker = CelebrityBlocker()")
    print("  is_protected, name, similarity, reason = blocker.check_if_protected('image.jpg')")
    print("  if is_protected:")
    print("      raise HTTPException(403, f'Protected identity: {name}')")
    print()
