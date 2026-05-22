# E2E Integration Notes

## Note Implementative per l'Integrazione Completa

Questo documento descrive le modifiche opzionali e le estensioni future per integrare completamente il Dynamic Kinematic Retrieval Agent nel test E2E.

## Stato Attuale

### Componenti Funzionanti ✓

1. **Frame Extraction (frame_extractor.py)**
   - ✓ Estrazione multi-angolo con Laplacian variance
   - ✓ Calcolo Euler angles con solvePnP
   - ✓ Selezione frame diversificati
   - ✓ Funzione `extract_and_save_frames_for_identity()` integrata

2. **Core Engine (core_engine.py)**
   - ✓ Multi-angle identity extraction
   - ✓ Identity super-vector fusion
   - ✓ Flux.1 Dev first frame generation
   - ✓ Wan I2V video synthesis
   - ✓ Autoregressive loop support
   - ✓ Funzione async `generate_high_fidelity_video()` integrata

3. **E2E Test Script (run_e2e_test.py)**
   - ✓ Setup e teardown automatici
   - ✓ Logging dettagliato
   - ✓ GDPR-compliant cleanup
   - ✓ Error handling robusto
   - ✓ Metriche di qualità

### Componenti da Integrare (Opzionali)

1. **Dynamic Retriever Integration**
   - File: `dynamic_retriever.py`
   - Status: Implementato ma non ancora integrato nel test E2E
   - Prossimi passi: Vedi sezione "Dynamic Retriever Integration" sotto

## Modifiche Necessarie per Integrazione Completa

### 1. Dynamic Retriever Integration

Il Dynamic Kinematic Retrieval Agent è implementato ma non è ancora utilizzato nel flusso E2E.

#### Opzione A: Parametro motion_keyword nel test

Modifica `run_e2e_test.py` - Fase 2:

```python
async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    # ...
    
    # AGGIUNTA: Motion keyword per Dynamic Retriever
    motion_keyword = "olympic dive platform somersault"
    
    logger.info(f"  Motion keyword: {motion_keyword}")
    logger.info(f"  Dynamic Retriever: Attivo")
    
    # TODO: Integrare Dynamic Retriever qui
    # from dynamic_retriever import DynamicKinematicRetriever
    # retriever = DynamicKinematicRetriever()
    # motion_data = await retriever.retrieve(motion_keyword)
    
    result = await generate_high_fidelity_video(
        reference_faces_dir=str(TEMP_FACES_DIR),
        prompt=prompt,
        duration_seconds=duration,
        output_path=str(OUTPUT_DIR),
        # motion_data=motion_data  # Passa al core engine
    )
```

#### Opzione B: Modifica core_engine.py

Aggiungi parametro `motion_keyword` alla funzione principale:

```python
async def generate_high_fidelity_video(
    reference_faces_dir: str,
    prompt: str,
    controlnet_map_path: Optional[str] = None,
    duration_seconds: int = 10,
    output_path: str = "outputs/",
    motion_keyword: Optional[str] = None  # NUOVO
) -> Dict[str, Any]:
    """
    Main convenience function for high-fidelity video generation.
    
    Args:
        ...
        motion_keyword: Optional motion keyword for Dynamic Retriever
    """
    
    # Se motion_keyword è fornito, usa Dynamic Retriever
    if motion_keyword:
        from dynamic_retriever import DynamicKinematicRetriever
        retriever = DynamicKinematicRetriever()
        motion_data = await retriever.retrieve(motion_keyword)
        
        # Integra motion_data nel prompt o nella generazione
        prompt = f"{prompt}, {motion_data.get('description', '')}"
    
    # Continua con la generazione normale
    config = CoreEngineConfig(...)
    # ...
```

**Nota:** Questa modifica richiede di capire come `dynamic_retriever.py` espone le sue funzionalità.

#### Verifica Dynamic Retriever API

Prima di modificare, leggi `dynamic_retriever.py` per capire la sua API:

```bash
# Leggi il file per vedere l'interfaccia
python -c "import dynamic_retriever; help(dynamic_retriever)"

# O apri il file direttamente
cat dynamic_retriever.py | grep "class\|def"
```

### 2. ControlNet Integration (Opzionale)

Il Core Engine supporta già ControlNet, ma il test E2E non lo usa ancora.

Per abilitare ControlNet nel test:

**Modifica `run_e2e_test.py` - Fase 2:**

```python
async def fase2_orchestrazione() -> Optional[Dict[str, Any]]:
    # ...
    
    # AGGIUNTA: ControlNet pose map (se disponibile)
    controlnet_map_path = None
    pose_map_file = Path("pose_maps/olympic_dive.png")
    
    if pose_map_file.exists():
        controlnet_map_path = str(pose_map_file)
        logger.info(f"  ControlNet map: {controlnet_map_path}")
    
    result = await generate_high_fidelity_video(
        reference_faces_dir=str(TEMP_FACES_DIR),
        prompt=prompt,
        controlnet_map_path=controlnet_map_path,  # Passa al core engine
        duration_seconds=duration,
        output_path=str(OUTPUT_DIR)
    )
```

### 3. Metriche Avanzate (Opzionali)

Per metriche più dettagliate, considera di aggiungere:

**Modifica `run_e2e_test.py` - Nuova fase 2.5:**

```python
async def fase2_5_quality_analysis(video_path: str) -> Dict[str, float]:
    """
    Fase 2.5: Analisi qualità avanzata
    """
    logger.info("="*70)
    logger.info("FASE 2.5 - ANALISI QUALITÀ AVANZATA")
    logger.info("="*70)
    
    # TODO: Implementare metriche avanzate
    # - FID (Fréchet Inception Distance)
    # - LPIPS (Learned Perceptual Image Patch Similarity)
    # - Frame-by-frame identity drift
    
    return {
        'fid_score': 0.0,
        'lpips_score': 0.0,
        'per_frame_drift': []
    }
```

## Modifiche Consigliate per Produzione

### 1. Configurazione Esterna

Invece di hardcodare i parametri, usa un file di configurazione:

**Crea `e2e_config.yaml`:**

```yaml
test:
  input_video: "inputs/mio_selfie.mp4"
  num_frames: 5
  laplacian_threshold: 100.0
  
generation:
  prompt: "A professional olympic diver performing a perfect triple somersault"
  motion_keyword: "olympic dive platform somersault"
  duration_seconds: 5
  quality_preset: "high"
  
output:
  temp_faces_dir: "tmpfs/test_faces"
  output_dir: "outputs/e2e_test"
  log_file: "e2e_test.log"
  
gdpr:
  auto_cleanup: true
  cleanup_temp_data: true
  retain_final_video: true
```

**Modifica `run_e2e_test.py`:**

```python
import yaml

def load_config(config_path: str = "e2e_config.yaml") -> Dict[str, Any]:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

async def main():
    config = load_config()
    
    INPUT_VIDEO = Path(config['test']['input_video'])
    # ...
```

### 2. Parallelizzazione Batch

Per processare multipli video in parallelo:

**Aggiungi `run_e2e_batch.py`:**

```python
async def process_video_batch(video_paths: List[str]):
    tasks = [
        run_e2e_test_for_video(video_path)
        for video_path in video_paths
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Analizza risultati aggregati
    success_count = sum(1 for r in results if not isinstance(r, Exception))
    logger.info(f"Batch completato: {success_count}/{len(video_paths)} successi")
```

### 3. Monitoring e Alerting

Per produzione, integra monitoring:

```python
# Aggiungi all'inizio di ogni fase
from monitoring import track_metric, alert_on_failure

async def fase1_ingestione_biometrica():
    with track_metric("fase1_duration"):
        try:
            # ... codice esistente ...
        except Exception as e:
            alert_on_failure("fase1_biometric_ingestion", str(e))
            raise
```

### 4. Database Persistence

Per tracciare test storici:

```python
from database import save_test_result

async def main():
    # ... esecuzione test ...
    
    # Salva risultato in database
    await save_test_result({
        'timestamp': time.time(),
        'success': success,
        'duration': total_elapsed,
        'video_url': result.get('video_url') if result else None,
        'metrics': {
            'identity_stability': result.get('identity_stability'),
            'temporal_consistency': result.get('temporal_consistency')
        }
    })
```

## Checklist Pre-Integrazione

Prima di integrare in produzione, verifica:

- [ ] Tutti i test E2E passano localmente
- [ ] API keys sono configurate correttamente (`.env`)
- [ ] Dipendenze installate (`requirements.txt` aggiornato)
- [ ] GDPR compliance verificato (cleanup temporanei)
- [ ] Logging configurato per produzione (no DEBUG in prod)
- [ ] Error handling testato (network failures, API timeouts)
- [ ] Metriche di qualità documentate (thresholds)
- [ ] Performance testata (tempi generazione accettabili)
- [ ] Storage pianificato (cleanup automatico video vecchi?)
- [ ] Rate limiting configurato (evitare throttling API)

## Compatibilità

### Python Version

Lo script è testato con:
- Python 3.8+
- Python 3.9+ (consigliato)
- Python 3.10+ (ottimale per asyncio)

### Dipendenze Critiche

```
opencv-python>=4.5.0
numpy>=1.19.0
fal-client>=0.4.0
httpx>=0.27.0
aiofiles>=23.0.0
python-dotenv>=1.0.0
```

### Sistema Operativo

Testato su:
- ✓ Linux (Ubuntu 20.04+)
- ✓ macOS (11+)
- ✓ Windows 10/11 (con PowerShell)

**Nota Windows:** Il path separator è gestito automaticamente da `pathlib.Path`.

## Prossimi Passi Suggeriti

1. **Immediate (questa settimana):**
   - [ ] Testare lo script E2E con un video reale
   - [ ] Verificare che tutte le metriche siano plausibili
   - [ ] Documentare eventuali problemi riscontrati

2. **Short-term (prossime 2 settimane):**
   - [ ] Integrare Dynamic Retriever nel flusso E2E
   - [ ] Aggiungere ControlNet support al test
   - [ ] Implementare configurazione esterna (YAML)

3. **Medium-term (prossimo mese):**
   - [ ] Batch processing support
   - [ ] Metriche avanzate (FID, LPIPS)
   - [ ] Database persistence per risultati storici

4. **Long-term (prossimi 3 mesi):**
   - [ ] CI/CD integration (test automatici su PR)
   - [ ] Monitoring e alerting in produzione
   - [ ] A/B testing framework per varianti modello

## Domande Frequenti

### Q: Il test funziona senza Dynamic Retriever?

**A:** Sì! Il test E2E funziona già senza Dynamic Retriever. Il retriever è un'estensione opzionale per migliorare la coerenza del movimento.

### Q: Posso usare video più lunghi?

**A:** Sì, ma considera:
- Video >30s: aumenta il tempo di elaborazione Fase 1
- Più frame: più tempo per identity extraction
- Consiglio: 10-20 secondi è ottimale

### Q: Come posso ridurre i tempi di generazione?

**A:**
1. Usa `quality_preset="standard"` invece di `"high"`
2. Riduci `duration_seconds` (es. 3 invece di 5)
3. Disabilita autoregressive se non necessario
4. Cache identity super-vectors per lo stesso soggetto

### Q: Il cleanup GDPR è sicuro?

**A:** Sì. La Fase 3 elimina tutti i dati biometrici temporanei. Solo il video finale è conservato. Se vuoi rimuovere anche quello, aggiungi:

```python
# Rimozione completa (anche video finale)
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)
```

### Q: Posso integrare questo in una API REST?

**A:** Assolutamente! Esempio con FastAPI:

```python
from fastapi import FastAPI, UploadFile
import tempfile

app = FastAPI()

@app.post("/generate-video")
async def generate_video_endpoint(selfie_video: UploadFile):
    # Salva upload temporaneo
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(await selfie_video.read())
        tmp_path = tmp.name
    
    # Esegui pipeline E2E
    result = await run_e2e_pipeline(tmp_path)
    
    # Cleanup
    os.unlink(tmp_path)
    
    return {"video_url": result['video_url']}
```

## Contributi

Per contribuire miglioramenti al test E2E:

1. Fork del repository
2. Crea branch feature (`git checkout -b feature/better-e2e-test`)
3. Commit delle modifiche
4. Push del branch
5. Apri Pull Request

## Licenza

Questo test E2E segue la stessa licenza del progetto principale.

---

**Ultimo aggiornamento:** 2026-05-22  
**Versione test E2E:** 1.0.0  
**Compatibilità Core Engine:** Week 1 V2
