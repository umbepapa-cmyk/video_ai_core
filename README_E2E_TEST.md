# End-to-End Kinematic Integration Test

## Quick Start

### 1. Installa Dipendenze

```bash
pip install opencv-python numpy fal-client httpx aiofiles python-dotenv
```

### 2. Configura API Key

```bash
# Copia il template
cp .env.example .env

# Modifica .env e aggiungi la tua FAL_KEY
FAL_KEY=your_actual_fal_api_key_here
```

Ottieni una API key da: https://fal.ai/

### 3. Prepara Video Selfie

Registra un video selfie (5-30 secondi) e salvalo come:

```
inputs/mio_selfie.mp4
```

**Requisiti:**
- Viso chiaramente visibile
- Ruota lentamente la testa da sinistra a destra
- Buona illuminazione frontale
- 720p o superiore

### 4. Esegui Test

```bash
python run_e2e_test.py
```

## Output Atteso

```
✓✓✓ Test PASSED ✓✓✓

Il sistema funziona correttamente end-to-end.
Video finale disponibile in: outputs/e2e_test
```

Tempo totale: ~2-5 minuti

## Struttura File

```
run_e2e_test.py               # Script principale
E2E_TEST_INSTRUCTIONS.md      # Istruzioni dettagliate
E2E_INTEGRATION_NOTES.md      # Note per sviluppatori
README_E2E_TEST.md           # Questo file (quick start)
```

## Flusso Test

1. **Fase Setup** (~1s)
   - Verifica presenza video selfie
   - Crea directory temporanee

2. **Fase 1: Ingestione Biometrica** (~10-30s)
   - Estrae 5 frame nitidi
   - Calcola Euler angles (Yaw, Pitch, Roll)
   - Seleziona frame con massima diversità angolare

3. **Fase 2: Orchestrazione Video** (~2-5 minuti)
   - Estrae identity super-vector multi-angolo
   - Genera first frame (Flux.1 Dev)
   - Sintetizza video (Wan I2V)
   - Applica identity consistency

4. **Fase 3: Teardown GDPR** (~1s)
   - Rimuove dati biometrici temporanei
   - Conserva solo video finale

## Troubleshooting Rapido

### Video non trovato
```
ERRORE: Fornire un video selfie in inputs/mio_selfie.mp4
```
→ Registra e salva un video in `inputs/mio_selfie.mp4`

### API key non configurata
```
ValueError: FAL_KEY not set in environment
```
→ Configura `FAL_KEY` nel file `.env`

### Frames troppo sfocati
```
ValueError: No suitable frames found (all frames too blurry)
```
→ Registra nuovo video con movimenti più lenti e migliore illuminazione

### Timeout di rete
```
httpx.TimeoutException
```
→ Verifica connessione internet e riprova

## Log Dettagliati

Tutti i log sono salvati in `e2e_test.log`:

```bash
# Visualizza log in tempo reale (Unix/Mac)
tail -f e2e_test.log

# Visualizza log (Windows)
Get-Content e2e_test.log -Wait
```

## Metriche di Qualità

Il test fornisce metriche quantitative:

- **Identity Stability:** >90% (ottimo), >85% (buono), <85% (da migliorare)
- **Temporal Consistency:** >85% (ottimo), >80% (buono), <80% (da migliorare)
- **Generation Time:** 2-5 minuti tipici con API remote

## Video Generato

Il video finale viene salvato in:

```
outputs/e2e_test/final_video_<timestamp>.mp4
```

Puoi aprirlo con qualsiasi player video per verificare la qualità.

## GDPR Compliance

Il test implementa **GDPR-compliant data handling**:

✓ Dati biometrici temporanei eliminati automaticamente  
✓ Solo video finale conservato  
✓ Video sorgente sotto controllo utente  

## Documentazione Completa

- **Istruzioni dettagliate:** [E2E_TEST_INSTRUCTIONS.md](./E2E_TEST_INSTRUCTIONS.md)
- **Note di integrazione:** [E2E_INTEGRATION_NOTES.md](./E2E_INTEGRATION_NOTES.md)

## Prossimi Passi

Dopo un test riuscito:

1. ✓ Visualizza il video generato
2. ✓ Verifica identity consistency visivamente
3. ✓ Controlla metriche di qualità nel log
4. → Procedi con integrazione in produzione

## Supporto

Per problemi:

1. Controlla `e2e_test.log` per errori dettagliati
2. Verifica che tutte le dipendenze siano installate
3. Assicurati che la API key sia valida
4. Controlla la connessione internet

## Versione

- **Test E2E:** v1.0.0
- **Core Engine:** Week 1 V2
- **Python:** 3.8+ (3.10+ consigliato)

---

**Quick Command Reference:**

```bash
# Setup
pip install opencv-python numpy fal-client httpx aiofiles python-dotenv
cp .env.example .env
# [Edit .env with your FAL_KEY]

# Run
python run_e2e_test.py

# View logs
tail -f e2e_test.log  # Unix/Mac
Get-Content e2e_test.log -Wait  # Windows

# Clean
rm -rf tmpfs/test_faces outputs/e2e_test e2e_test.log
```

---

**Stato:** ✅ Production Ready  
**Ultimo aggiornamento:** 2026-05-22
