# End-to-End Test Instructions

## Panoramica

Lo script `run_e2e_test.py` valida l'intero flusso architetturale del **Dynamic Kinematic Retrieval Agent**, dalla biometria al rendering finale.

## Prerequisiti

### 1. Dipendenze Python

Assicurati di avere installate tutte le dipendenze:

```bash
pip install opencv-python numpy fal-client httpx aiofiles python-dotenv
```

### 2. Configurazione API

Crea un file `.env` nella directory root con la tua API key:

```bash
FAL_KEY=your_fal_api_key_here
```

Puoi ottenere una API key da [fal.ai](https://fal.ai/).

### 3. Video Selfie

Il test richiede un video selfie per l'estrazione biometrica:

**Posizione:** `inputs/mio_selfie.mp4`

**Requisiti:**
- **Formato:** MP4
- **Durata:** 5-30 secondi
- **Qualità:** 720p o superiore
- **Contenuto:** Viso chiaramente visibile, con movimento della testa per catturare diversi angoli
- **Illuminazione:** Buona (evitare controluce o ombre forti)

**Consigli per la registrazione:**
- Ruota lentamente la testa da sinistra a destra
- Mantieni espressione neutra
- Illuminazione frontale uniforme
- Evita occhiali riflettenti o cappelli

## Esecuzione

### Esecuzione Standard

```bash
python run_e2e_test.py
```

### Output Atteso

Lo script eseguirà 3 fasi:

#### Fase Setup
- Verifica presenza video selfie
- Crea directory temporanee

#### Fase 1: Ingestione Biometrica (~10-30 secondi)
- Estrae 5 frame nitidi dal video
- Calcola Laplacian variance per sharpness
- Stima Euler angles (Yaw, Pitch, Roll) con solvePnP
- Salva frame in `tmpfs/test_faces/`

**Log attesi:**
```
FASE 1 - INGESTIONE BIOMETRICA
Estrazione frame da: inputs/mio_selfie.mp4
✓ Estratti 5 frame biometrici
  Frame 1: frame_000_yaw12.3.jpg
    Yaw:   12.30°  |  Pitch:   -5.40°  |  Roll:    2.10°
    Sharpness (Laplacian): 234.56
...
```

#### Fase 2: Orchestrazione Video (~2-5 minuti)
- Estrae identity super-vector multi-angolo
- Genera first frame con Flux.1 Dev
- Sintetizza video con Wan I2V
- Applica identity consistency enforcement

**Log attesi:**
```
FASE 2 - ORCHESTRAZIONE VIDEO
Avvio generazione video...
✓ Video generato con successo!
  Video URL/Path: outputs/e2e_test/final_video_1234567890.mp4
  Duration: 5s
  Identity stability: 95.3%
  Temporal consistency: 92.1%
```

#### Fase 3: Teardown GDPR-Compliant (~1 secondo)
- Rimuove dati biometrici temporanei
- Conserva solo video finale

**Log attesi:**
```
FASE 3 - TEARDOWN GDPR-COMPLIANT
Rimozione dati biometrici: tmpfs/test_faces
✓ Dati biometrici rimossi (GDPR compliance)
```

### Risultato Finale

Se tutto funziona correttamente:

```
✓✓✓ Test PASSED ✓✓✓

Il sistema funziona correttamente end-to-end.
Video finale disponibile in: outputs/e2e_test
```

Il video generato sarà salvato in `outputs/e2e_test/final_video_<timestamp>.mp4`.

## File di Log

Tutti i log vengono salvati in `e2e_test.log` per debugging dettagliato.

```bash
# Visualizza log in tempo reale (su Unix/Mac)
tail -f e2e_test.log

# Visualizza log su Windows
Get-Content e2e_test.log -Wait
```

## Troubleshooting

### Errore: Video non trovato

```
ERRORE: Fornire un video selfie in inputs/mio_selfie.mp4
```

**Soluzione:** Registra un video selfie e salvalo in `inputs/mio_selfie.mp4`.

### Errore: No frames found (all too blurry)

```
ValueError: No suitable frames found (all frames too blurry)
```

**Soluzione:** Il video è troppo mosso o sfocato. Registra un nuovo video con:
- Movimenti più lenti
- Migliore illuminazione
- Camera più stabile

### Errore: FAL_KEY not set

```
ValueError: FAL_KEY not set in environment or constructor
```

**Soluzione:** Configura la API key nel file `.env`:
```bash
FAL_KEY=your_api_key_here
```

### Errore: API timeout o network error

```
ERRORE Fase 2: Failed to generate video
httpx.TimeoutException
```

**Soluzione:**
- Verifica connessione internet
- Riprova più tardi (servizi esterni potrebbero essere sotto carico)
- Aumenta i timeout nel codice se necessario

### Errore: Not enough keypoints for pose estimation

```
WARNING: Not enough keypoints for pose estimation
```

**Soluzione:** Questo è un warning, non un errore critico. Lo script userà angoli di default (0, 0, 0) per quel frame e continuerà.

## Struttura Directory

Dopo l'esecuzione completa:

```
project/
├── inputs/
│   └── mio_selfie.mp4              # Video sorgente (conservato)
├── outputs/
│   └── e2e_test/
│       └── final_video_*.mp4       # Video finale generato
├── tmpfs/
│   └── test_faces/                 # VUOTA dopo il test (GDPR cleanup)
├── run_e2e_test.py
├── e2e_test.log                    # Log dettagliato
└── E2E_TEST_INSTRUCTIONS.md
```

## Note GDPR

Lo script implementa **GDPR-compliant data handling**:

1. **Dati biometrici temporanei:** Salvati in `tmpfs/test_faces/` durante l'elaborazione
2. **Distruzione automatica:** Tutti i frame biometrici vengono eliminati al termine (Fase 3)
3. **Conservazione controllata:**
   - **Video sorgente:** Rimane in `inputs/` (sotto controllo utente)
   - **Video finale:** Salvato in `outputs/` (risultato della generazione)
   - **Frame biometrici:** Eliminati (privacy)

## Metriche di Qualità

Il test fornisce metriche quantitative:

### Identity Stability (Target: >90%)
Misura quanto la faccia generata corrisponde ai reference frames.

### Temporal Consistency (Target: >85%)
Misura la coerenza temporale tra frame consecutivi (assenza di flickering).

### Generation Time
Tempo totale di generazione (tipicamente 2-5 minuti con API remote).

## Prossimi Passi

Dopo un test E2E riuscito:

1. **Visualizza il video:** Apri `outputs/e2e_test/final_video_*.mp4`
2. **Verifica qualità:** Controlla identity consistency e motion smoothness
3. **Integrazione:** Se i risultati sono soddisfacenti, procedi con l'integrazione in produzione
4. **Scalabilità:** Per produzione, considera:
   - Caching dei super-vector di identità
   - Parallelizzazione per batch processing
   - Monitoraggio delle metriche di qualità
   - Rate limiting e gestione code

## Debug Avanzato

### Abilitare Debug Logging

Modifica il livello di logging in `run_e2e_test.py`:

```python
logging.basicConfig(
    level=logging.DEBUG,  # Cambia da INFO a DEBUG
    # ...
)
```

### Ispezionare Frame Estratti

Per visualizzare i frame estratti prima del cleanup, commenta temporaneamente la Fase 3:

```python
# Fase 3: Teardown
# await fase3_teardown(result)  # Commenta questa riga
```

I frame saranno conservati in `tmpfs/test_faces/` per ispezione manuale.

### Test Parziali

Puoi testare singole fasi commentando le altre nel `main()`:

```python
# Test solo Fase 1 (estrazione frame)
if not await fase1_ingestione_biometrica():
    return False
# return True  # Esci qui per testare solo Fase 1
```

## Supporto

Per problemi o domande:

1. Controlla `e2e_test.log` per errori dettagliati
2. Verifica che tutte le dipendenze siano installate correttamente
3. Assicurati che la API key sia valida
4. Controlla la connessione internet per le chiamate API remote

## Modifiche Future

Lo script è progettato per essere estensibile:

- [ ] Integrazione con Dynamic Retriever per motion keywords
- [ ] Supporto per ControlNet pose maps
- [ ] Batch processing di multipli video
- [ ] Metriche di qualità più avanzate (FID, LPIPS)
- [ ] Dashboard di monitoring real-time
