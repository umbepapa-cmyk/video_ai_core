#!/usr/bin/env python3
"""
End-to-End Kinematic Integration Test
=====================================

Valida l'intero flusso architetturale:
1. Ingestione biometrica (frame extraction)
2. Orchestrazione video (core_engine + dynamic retriever)
3. Teardown GDPR-compliant

Usage:
    python run_e2e_test.py
"""

import asyncio
import logging
import time
import shutil
from pathlib import Path
import sys
from typing import Dict, Any, List, Optional

# Import dei moduli del progetto
from frame_extractor import extract_and_save_frames_for_identity
from core_engine import generate_high_fidelity_video

# Setup logging dettagliato
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('e2e_test.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Percorsi
INPUT_DIR = Path("inputs")
INPUT_VIDEO = INPUT_DIR / "mio_selfie.mp4"
TEMP_FACES_DIR = Path("tmpfs/test_faces")
OUTPUT_DIR = Path("outputs/e2e_test")


async def setup_phase() -> bool:
    """
    Fase Setup: Prepara directories e verifica input
    """
    logger.info("="*70)
    logger.info("FASE SETUP - Preparazione ambiente test")
    logger.info("="*70)
    
    start_time = time.time()
    
    # Crea directory inputs se non esiste
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Verifica presenza video selfie
    if not INPUT_VIDEO.exists():
        logger.warning(f"Video selfie non trovato: {INPUT_VIDEO}")
        logger.info("")
        logger.info("="*70)
        logger.info("ISTRUZIONI PER FORNIRE IL VIDEO SELFIE")
        logger.info("="*70)
        logger.info("")
        logger.info("Per eseguire questo test E2E, è necessario un video selfie.")
        logger.info("")
        logger.info("Opzioni:")
        logger.info("  1. Registra un breve video selfie (5-10 secondi)")
        logger.info("  2. Salva il video come: inputs/mio_selfie.mp4")
        logger.info("  3. Esegui nuovamente questo script")
        logger.info("")
        logger.info("Requisiti video:")
        logger.info("  - Formato: MP4")
        logger.info("  - Durata: 5-30 secondi")
        logger.info("  - Qualità: 720p o superiore")
        logger.info("  - Contenuto: Viso chiaramente visibile")
        logger.info("  - Illuminazione: Buona (evitare controluce)")
        logger.info("")
        logger.info("="*70)
        logger.error("ERRORE: Fornire un video selfie in inputs/mio_selfie.mp4")
        return False
    
    logger.info(f"✓ Video selfie trovato: {INPUT_VIDEO}")
    logger.info(f"  Dimensione: {INPUT_VIDEO.stat().st_size / 1024 / 1024:.2f} MB")
    
    # Crea directory temporanea per faces
    TEMP_FACES_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"✓ Directory temporanea creata: {TEMP_FACES_DIR}")
    
    # Crea directory output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"✓ Directory output creata: {OUTPUT_DIR}")
    
    elapsed = time.time() - start_time
    logger.info(f"Setup completato in {elapsed:.2f}s")
    
    return True


async def fase1_ingestione_biometrica() -> bool:
    """
    Fase 1: Estrazione fotogrammi biometrici dal video selfie
    """
    logger.info("")
    logger.info("="*70)
    logger.info("FASE 1 - INGESTIONE BIOMETRICA")
    logger.info("="*70)
    
    start_time = time.time()
    
    try:
        # Estrai 5 frame nitidi dal video selfie usando la funzione corretta
        logger.info(f"Estrazione frame da: {INPUT_VIDEO}")
        logger.info(f"Target: 5 frame con diversi angoli di ripresa")
        logger.info(f"Algoritmo: Laplacian variance + solvePnP per Euler angles")
        
        frame_data = extract_and_save_frames_for_identity(
            video_path=str(INPUT_VIDEO),
            output_dir=str(TEMP_FACES_DIR),
            num_frames=5,
            laplacian_threshold=100.0
        )
        
        logger.info(f"✓ Estratti {len(frame_data)} frame biometrici")
        logger.info("")
        logger.info("Dettagli frame estratti:")
        for i, data in enumerate(frame_data, 1):
            angles = data['angles']
            lap_var = data['laplacian_variance']
            logger.info(f"  Frame {i}: {Path(data['path']).name}")
            logger.info(f"    Yaw: {angles[0]:7.2f}°  |  Pitch: {angles[1]:7.2f}°  |  Roll: {angles[2]:7.2f}°")
            logger.info(f"    Sharpness (Laplacian): {lap_var:.2f}")
        
        elapsed = time.time() - start_time
        logger.info("")
        logger.info(f"Fase 1 completata in {elapsed:.2f}s")
        
        return True
        
    except FileNotFoundError as e:
        logger.error(f"ERRORE Fase 1: Video non trovato - {e}")
        return False
    except ValueError as e:
        logger.error(f"ERRORE Fase 1: Video non valido o corrotto - {e}")
        return False
    except Exception as e:
        logger.error(f"ERRORE Fase 1: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    """
    Fase 2: Innesco orchestratore con Dynamic Retrieval
    """
    logger.info("")
    logger.info("="*70)
    logger.info("FASE 2 - ORCHESTRAZIONE VIDEO")
    logger.info("="*70)
    
    start_time = time.time()
    
    try:
        # Parametri generazione
        prompt = "A professional olympic diver performing a perfect triple somersault, cinematic lighting, 4k resolution, photorealistic"
        duration = 5
        
        logger.info("Parametri generazione:")
        logger.info(f"  Prompt: {prompt}")
        logger.info(f"  Duration: {duration}s")
        logger.info(f"  Reference faces: {TEMP_FACES_DIR}")
        logger.info("")
        logger.info("Pipeline attiva:")
        logger.info("  1. Multi-angle identity extraction (5 angoli)")
        logger.info("  2. Identity super-vector fusion")
        logger.info("  3. High-fidelity first frame (Flux.1 Dev)")
        logger.info("  4. Image-to-Video (Wan I2V)")
        logger.info("  5. Identity consistency enforcement")
        logger.info("")
        
        # Invoca core engine
        logger.info("Avvio generazione video...")
        logger.info("(Questa operazione può richiedere 2-5 minuti)")
        logger.info("")
        
        result = await generate_high_fidelity_video(
            reference_faces_dir=str(TEMP_FACES_DIR),
            prompt=prompt,
            duration_seconds=duration,
            output_path=str(OUTPUT_DIR)
        )
        
        logger.info("")
        logger.info("✓ Video generato con successo!")
        logger.info("")
        logger.info("Risultati generazione:")
        logger.info(f"  Video URL/Path: {result.get('video_url', 'N/A')}")
        logger.info(f"  Duration: {result.get('duration', 0)}s")
        logger.info(f"  Identity stability: {result.get('identity_stability', 0)*100:.1f}%")
        logger.info(f"  Temporal consistency: {result.get('temporal_consistency', 0)*100:.1f}%")
        logger.info(f"  Generation time: {result.get('generation_time', 0):.2f}s")
        
        elapsed = time.time() - start_time
        logger.info("")
        logger.info(f"Fase 2 completata in {elapsed:.2f}s")
        
        return result
        
    except RuntimeError as e:
        logger.error(f"ERRORE Fase 2: {e}")
        logger.error("")
        logger.error("Possibili cause:")
        logger.error("  - API key non configurata (FAL_KEY in .env)")
        logger.error("  - Servizi esterni non disponibili")
        logger.error("  - Timeout di rete")
        import traceback
        logger.error(traceback.format_exc())
        return None
    except Exception as e:
        logger.error(f"ERRORE Fase 2: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def fase3_teardown(result: Optional[Dict[str, Any]]) -> bool:
    """
    Fase 3: Cleanup GDPR-compliant
    """
    logger.info("")
    logger.info("="*70)
    logger.info("FASE 3 - TEARDOWN GDPR-COMPLIANT")
    logger.info("="*70)
    
    start_time = time.time()
    
    try:
        # Stampa risultato finale
        if result:
            logger.info("Risultato finale:")
            logger.info(f"  Video URL/Path: {result.get('video_url', 'N/A')}")
            logger.info(f"  Duration: {result.get('duration', 0)}s")
            logger.info(f"  Quality metrics:")
            logger.info(f"    - Identity stability: {result.get('identity_stability', 0)*100:.1f}%")
            logger.info(f"    - Temporal consistency: {result.get('temporal_consistency', 0)*100:.1f}%")
            logger.info("")
        
        # Distruzione dati biometrici temporanei
        logger.info(f"Rimozione dati biometrici: {TEMP_FACES_DIR}")
        
        if TEMP_FACES_DIR.exists():
            # Conta file prima della rimozione
            files_to_remove = list(TEMP_FACES_DIR.glob("*"))
            num_files = len(files_to_remove)
            
            logger.info(f"  File da rimuovere: {num_files}")
            
            # Rimozione sicura
            shutil.rmtree(TEMP_FACES_DIR)
            logger.info("✓ Dati biometrici rimossi (GDPR compliance)")
            logger.info("")
            logger.info("Note GDPR:")
            logger.info("  - Dati biometrici temporanei eliminati")
            logger.info("  - Solo il video finale è conservato in outputs/")
            logger.info("  - Il video sorgente rimane in inputs/ (sotto controllo utente)")
        
        elapsed = time.time() - start_time
        logger.info("")
        logger.info(f"Fase 3 completata in {elapsed:.2f}s")
        
        return True
        
    except Exception as e:
        logger.error(f"ERRORE Fase 3: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def main() -> bool:
    """
    Main E2E test runner
    """
    logger.info("")
    logger.info("#"*70)
    logger.info("# END-TO-END KINEMATIC INTEGRATION TEST")
    logger.info("#"*70)
    logger.info("")
    logger.info("Questo test valida l'intero stack architetturale:")
    logger.info("  - Ingestione biometrica (OpenCV)")
    logger.info("  - Orchestrazione video (Core Engine)")
    logger.info("  - Dynamic Kinematic Retrieval")
    logger.info("  - GDPR compliance (data destruction)")
    logger.info("")
    
    total_start = time.time()
    
    # Setup
    if not await setup_phase():
        logger.error("")
        logger.error("Setup fallito - terminazione test")
        return False
    
    # Fase 1: Biometria
    if not await fase1_ingestione_biometrica():
        logger.error("")
        logger.error("Fase 1 fallita - terminazione test")
        await fase3_teardown(None)  # Cleanup parziale
        return False
    
    # Fase 2: Orchestrazione
    result = await fase2_orchestrazione()
    if not result:
        logger.error("")
        logger.error("Fase 2 fallita - continuo con teardown")
        # Continua comunque con teardown
    
    # Fase 3: Teardown
    await fase3_teardown(result)
    
    # Summary
    total_elapsed = time.time() - total_start
    logger.info("")
    logger.info("="*70)
    logger.info("E2E TEST COMPLETATO")
    logger.info("="*70)
    logger.info(f"Tempo totale: {total_elapsed:.2f}s")
    logger.info(f"Log salvato in: e2e_test.log")
    logger.info("")
    
    if result:
        logger.info("✓✓✓ Test PASSED ✓✓✓")
        logger.info("")
        logger.info("Il sistema funziona correttamente end-to-end.")
        logger.info(f"Video finale disponibile in: {OUTPUT_DIR}")
        logger.info("")
        return True
    else:
        logger.error("✗✗✗ Test FAILED ✗✗✗")
        logger.error("")
        logger.error("Controllare i log sopra per dettagli sull'errore.")
        logger.error("Verificare:")
        logger.error("  1. FAL_KEY configurata in .env")
        logger.error("  2. Connessione internet attiva")
        logger.error("  3. Video selfie valido in inputs/")
        logger.error("")
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("")
        logger.warning("Test interrotto dall'utente (Ctrl+C)")
        logger.info("Cleanup in corso...")
        
        # Cleanup veloce su interruzione
        if TEMP_FACES_DIR.exists():
            shutil.rmtree(TEMP_FACES_DIR)
            logger.info("✓ Dati temporanei rimossi")
        
        sys.exit(130)
    except Exception as e:
        logger.error("")
        logger.error(f"ERRORE CRITICO: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
