# Test E2E - Quick Start Guide

## Setup (2 minuti)

### 1. Dipendenze

```bash
pip install opencv-python numpy fal-client httpx aiofiles python-dotenv
```

### 2. API Key

Modifica `.env` e aggiungi:

```bash
FAL_KEY=your_fal_api_key_here
```

Ottieni API key: https://fal.ai/

### 3. Video Selfie

Registra un video selfie (10-15 secondi) e salva come:

```
inputs/mio_selfie.mp4
```

**Come registrare:**
- Guarda la camera frontalmente
- Ruota lentamente la testa sinistra → centro → destra
- Illuminazione frontale buona
- 720p o superiore

## Esecuzione

```bash
python run_e2e_test.py
```

**Tempo:** ~2-5 minuti

## Risultato Atteso

```
✓✓✓ Test PASSED ✓✓✓

Video finale disponibile in: outputs/e2e_test/final_video_*.mp4
```

## Metriche

- **Identity Stability:** >90% ✓
- **Temporal Consistency:** >85% ✓
- **Generation Time:** 2-5 minuti

## Troubleshooting

| Errore | Soluzione |
|--------|-----------|
| Video non trovato | Salva video in `inputs/mio_selfie.mp4` |
| FAL_KEY not set | Configura `FAL_KEY` in `.env` |
| Frames too blurry | Registra nuovo video con movimenti più lenti |
| Network timeout | Verifica connessione e riprova |

## Log

```bash
# Visualizza log (Unix/Mac)
tail -f e2e_test.log

# Visualizza log (Windows)
Get-Content e2e_test.log -Wait
```

## Documentazione Completa

- [README_E2E_TEST.md](./README_E2E_TEST.md) - Guida completa
- [E2E_TEST_INSTRUCTIONS.md](./E2E_TEST_INSTRUCTIONS.md) - Istruzioni dettagliate
- [E2E_INTEGRATION_NOTES.md](./E2E_INTEGRATION_NOTES.md) - Note per sviluppatori

## Comandi Utili

```bash
# Test completo
python run_e2e_test.py

# Pulisci tutto
rm -rf tmpfs/test_faces outputs/e2e_test e2e_test.log

# Verifica sintassi
python -m py_compile run_e2e_test.py

# Verifica dipendenze
pip list | grep -E "opencv|numpy|fal|httpx|aiofiles"
```

## Cosa Viene Testato

1. **Ingestione Biometrica**
   - Estrazione frame con Laplacian variance
   - Calcolo Euler angles con solvePnP
   - Selezione frame diversificati

2. **Orchestrazione Video**
   - Multi-angle identity extraction
   - Identity super-vector fusion
   - First frame generation (Flux.1 Dev)
   - Video synthesis (Wan I2V)

3. **GDPR Compliance**
   - Cleanup automatico dati biometrici
   - Privacy-preserving data handling

## Prossimi Passi

Dopo test riuscito:

1. ✓ Apri video in `outputs/e2e_test/`
2. ✓ Verifica identity consistency visivamente
3. ✓ Controlla metriche nel log
4. → Integra in produzione

---

**Quick Command:**
```bash
pip install opencv-python numpy fal-client httpx aiofiles python-dotenv && \
python run_e2e_test.py
```

**Stato:** ✅ Ready to Run
