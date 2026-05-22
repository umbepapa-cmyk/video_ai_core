"""
Configurazione Centralizzata dei Path dell'Applicazione
========================================================
Definisce tutte le cartelle utilizzate dall'applicazione con nomi descrittivi in italiano.

Questo modulo centralizza la gestione dei path per facilitare manutenzione e configurazione.
"""

from pathlib import Path
from typing import Optional
import os


# =============================================================================
# CARTELLE PRINCIPALI
# =============================================================================

# Cartella radice del progetto
CARTELLA_RADICE: Path = Path(__file__).parent.resolve()

# Cartella per i risultati/output generati dall'applicazione
CARTELLA_RISULTATI: str = "./outputs/"
CARTELLA_RISULTATI_PATH: Path = CARTELLA_RADICE / "outputs"

# Cartella per lo storage temporaneo/effimero
CARTELLA_STORAGE_EFFIMERO: str = "./ephemeral_storage"
CARTELLA_STORAGE_EFFIMERO_PATH: Path = CARTELLA_RADICE / "ephemeral_storage"


# =============================================================================
# CARTELLE PER TEST
# =============================================================================

# Cartella per volti di riferimento usati nei test
CARTELLA_VOLTI_RIFERIMENTO_TEST: str = "./test_reference_faces"
CARTELLA_VOLTI_RIFERIMENTO_TEST_PATH: Path = CARTELLA_RADICE / "test_reference_faces"

# Cartella per output dei test
CARTELLA_RISULTATI_TEST: str = "./test_outputs/"
CARTELLA_RISULTATI_TEST_PATH: Path = CARTELLA_RADICE / "test_outputs"

# Cartella per immagini di volti generici per test
CARTELLA_VOLTI_TEST: str = "./test_faces"
CARTELLA_VOLTI_TEST_PATH: Path = CARTELLA_RADICE / "test_faces"


# =============================================================================
# CARTELLE PER MODELLI E RISORSE
# =============================================================================

# Cartella per mappe di pose generate da ControlNet
CARTELLA_MAPPE_POSE: str = "./pose_maps"
CARTELLA_MAPPE_POSE_PATH: Path = CARTELLA_RADICE / "pose_maps"

# Cartella per checkpoint di modelli personalizzati
CARTELLA_CHECKPOINT_PERSONALIZZATI: str = "./custom_checkpoints"
CARTELLA_CHECKPOINT_PERSONALIZZATI_PATH: Path = CARTELLA_RADICE / "custom_checkpoints"

# Cartella per volti di riferimento predefiniti
CARTELLA_VOLTI_PREDEFINITI: str = "./default_faces"
CARTELLA_VOLTI_PREDEFINITI_PATH: Path = CARTELLA_RADICE / "default_faces"


# =============================================================================
# FUNZIONI UTILITY
# =============================================================================

def crea_cartelle_necessarie(verbose: bool = False) -> None:
    """
    Crea tutte le cartelle necessarie se non esistono già.
    
    Args:
        verbose: Se True, stampa le cartelle create
    """
    cartelle = [
        CARTELLA_RISULTATI_PATH,
        CARTELLA_STORAGE_EFFIMERO_PATH,
        CARTELLA_VOLTI_RIFERIMENTO_TEST_PATH,
        CARTELLA_RISULTATI_TEST_PATH,
        CARTELLA_VOLTI_TEST_PATH,
        CARTELLA_MAPPE_POSE_PATH,
        CARTELLA_CHECKPOINT_PERSONALIZZATI_PATH,
        CARTELLA_VOLTI_PREDEFINITI_PATH,
    ]
    
    for cartella in cartelle:
        if not cartella.exists():
            cartella.mkdir(parents=True, exist_ok=True)
            if verbose:
                print(f"[OK] Creata cartella: {cartella}")


def ottieni_path_output(nome_file: str, sottocartella: Optional[str] = None) -> Path:
    """
    Ottiene il path completo per un file di output.
    
    Args:
        nome_file: Nome del file
        sottocartella: Sottocartella opzionale dentro outputs/
        
    Returns:
        Path completo per il file
    """
    if sottocartella:
        base_path = CARTELLA_RISULTATI_PATH / sottocartella
        base_path.mkdir(parents=True, exist_ok=True)
    else:
        base_path = CARTELLA_RISULTATI_PATH
        
    base_path.mkdir(parents=True, exist_ok=True)
    return base_path / nome_file


def ottieni_path_test(nome_file: str) -> Path:
    """
    Ottiene il path completo per un file di test output.
    
    Args:
        nome_file: Nome del file
        
    Returns:
        Path completo per il file di test
    """
    CARTELLA_RISULTATI_TEST_PATH.mkdir(parents=True, exist_ok=True)
    return CARTELLA_RISULTATI_TEST_PATH / nome_file


# =============================================================================
# RETROCOMPATIBILITÀ (Path originali come alias)
# =============================================================================

# Mantieni i nomi originali come alias per retrocompatibilità
OUTPUTS_DIR = CARTELLA_RISULTATI
TEST_REFERENCE_FACES_DIR = CARTELLA_VOLTI_RIFERIMENTO_TEST
TEST_OUTPUTS_DIR = CARTELLA_RISULTATI_TEST
TEST_FACES_DIR = CARTELLA_VOLTI_TEST
POSE_MAPS_DIR = CARTELLA_MAPPE_POSE
CUSTOM_CHECKPOINTS_DIR = CARTELLA_CHECKPOINT_PERSONALIZZATI
DEFAULT_FACES_DIR = CARTELLA_VOLTI_PREDEFINITI
EPHEMERAL_STORAGE_DIR = CARTELLA_STORAGE_EFFIMERO


if __name__ == "__main__":
    # Test: crea tutte le cartelle
    print("Creazione cartelle necessarie...")
    crea_cartelle_necessarie(verbose=True)
    print("\n[OK] Tutte le cartelle sono state create con successo!")
