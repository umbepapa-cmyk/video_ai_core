# Opzioni salto di qualità — identità + full-body nudo

Documento per decisione strategica. **Nessun retrain automatico** — solo piano e miglioramenti inference già applicati in `test_forest_v2.py`.

## Stato attuale (maggio 2026)

| Soggetto | Cartella inputs | Genere (nome cartella) | Trigger | LoRA | Dataset |
|----------|-----------------|------------------------|---------|------|---------|
| 1 | `Soggetto 1 - donna` | donna | `soggetto_uno` | v3 ✓ | `soggetto1_v3` |
| 2 | `Soggetto 2 - donna` | donna | `soggetto_due` | v3 ✓ | `soggetto2_v3` |
| 3 | *(cartella rimossa)* | — | `soggetto_tre` | base (legacy) | inputs legacy |
| 4 | `Soggetto 4 - dpnna` | donna (typo) | `soggetto_quattro` | base v2 | `soggetto4_v3` esiste, LoRA non v3 |
| 5 | `Soggetto 5 - uomo` | uomo | `soggetto_cinque` | v3 ✓ | `soggetto5_v3` |
| 6 | *(eliminato)* | — | `soggetto_sei` | assente | — |

**Note:** S3 e S6 sono stati eliminati per materiale scarso. S1 ha genere ambiguo in `gender.json` (68% male) ma cartella esplicita **donna**. S5 non ha ancora `face.jpg` curato in inputs.

Il test forest v1 mostrava generazioni OK su Replicate ma **senza gate identità**; la somiglianza facciale riportata per S1 non è percepita dall’utente — il gate InsightFace in v2 serve proprio a rendere misurabile e non silenziosa questa valutazione.

---

## A) Pipeline identity-first (gate InsightFace + retry)

**Cosa:** Dopo ogni generazione, InsightFace confronta il volto output vs `face.jpg`. Se similarity &lt; soglia (es. 0.65), **rigenera** con seed/prompt diversi fino a N tentativi; opzionale fase close-up identità prima del full-body.

**Pro**
- Miglioramento rapido senza retrain
- Metriche oggettive nel log (già parzialmente in `test_forest_v2.py`)
- Allinea aspettative: “pass” = somiglianza misurata, non impressione visiva

**Contro**
- Full-body nudo a 9:16 riduce spesso il volto → similarity artificialmente bassa
- Costo API × tentativi
- Non risolve LoRA che non ha imparato l’identità

**Sforzo:** Basso–medio (1–2 giorni: loop retry, prompt a due fasi come `test_native_loras.py`)

---

## B) Overhaul training v3 su tutti i soggetti attivi

**Cosa:** Retrain S1, S2, S4, S5 con tier v3 (cap volti frontali, più body_full), rank 32, 1200 step; evitare overfit face-only; allineare metadata a `_v3.json`.

**Pro**
- Fix strutturale se il LoRA attuale non generalizza al corpo nudo
- S4 passerebbe da v2 a v3 con dataset già esportato
- Coerenza trigger + caption ohwx

**Contro**
- Tempo GPU/costo Replicate
- S4 ha solo ~13 file in inputs — rischio identità debole anche dopo retrain
- Non garantisce full-body perfetto senza stack inference

**Sforzo:** Medio–alto (2–4 giorni export + train + benchmark)

---

## C) Stack prompt + controllo composizione

**Cosa:** Template prompt fisso (single person, head-to-toe, tramonto), negative forti (già in v2), tuning pesi Realism LoRA vs subject LoRA (es. 0.8 / 1.0), optional OpenPose/depth su Fal per pose full-body.

**Pro**
- Nessun retrain; migliora framing e riduce multi-person / cropped
- 9:16 e negative già applicati in `test_forest_v2.py`

**Contro**
- Identità facciale resta limitata dal LoRA
- ControlNet su nudo + Flux può essere instabile o censurato su alcuni provider

**Sforzo:** Basso (ore–1 giorno tuning pesi e prompt)

---

## D) Identità ibrida (LoRA corpo + conditioning faccia)

**Cosa:** LoRA per corpo/stile + **InstantID / PuLID / IP-Adapter Face** con riferimento `face.jpg` — non face-swap post-hoc, ma conditioning in generazione.

**Pro**
- Salto maggiore sulla somiglianza facciale in scene full-body
- Separazione “corpo dal LoRA, volto dal riferimento”

**Contro**
- Integrazione Fal/Replicate non uniforme; modelli face-ID spesso addestrati su SFW
- Rischio uncanny valley o incoerenza pelle corpo/volto
- Più complessità pipeline e latenza

**Sforzo:** Alto (3–7 giorni integrazione + test A/B)

---

## E) Igiene dataset (purge + re-curation tier v3)

**Cosa:** Audit cartelle (es. contaminazione S5 / mixed identity), minimo N volti tier A prima del train, `face.jpg` obbligatorio per ogni soggetto attivo, riesport v3 solo da cartelle pulite.

**Pro**
- Previene identità “fantasma” e gender drift
- Base necessaria prima di qualsiasi retrain serio

**Contro**
- Richiede lavoro manuale su inputs (S4 scarso, S5 senza face.jpg)
- Non migliora da solo l’inference senza B o D

**Sforzo:** Medio (1–2 giorni curazione + regole gate pre-train)

---

## Raccomandazione sequenza

1. **Subito:** Eseguire `python test_forest_v2.py` e leggere log `identity=` + JSON in `outputs/` — decidere soglia realistica per full-body (forse 0.50–0.55 vs 0.65 close-up).
2. **Breve termine:** **C + A** (tuning già in v2 + retry identity-first).
3. **Se identità resta insufficiente:** **E poi B** su S1, S2, S5; valutare se S4 ha abbastanza materiale.
4. **Salto grande:** **D** se dopo B la faccia in full-body nudo resta il collo di bottiglia.

---

## Campionamento video (curator / import v3)

**Policy:** tutti i frame **puliti** e **non ridondanti** per video — nessun cap arbitrario sul conto in export.

| Parametro | Valore | Note |
|-----------|--------|------|
| `VIDEO_SAMPLE_FPS` | **1.0** | Candidati campionati ~1 al secondo (`step = max(1, round(fps))`) |
| `VIDEO_SCENE_CHANGE_HIST` | **0.78** | Sotto questa correlazione istogramma → frame extra (taglio scena) |
| `V3_DIVERSITY_HIST_THRESHOLD` | **0.92** | Dedup = similarità istogramma; frame ≥ soglia vs già tenuti → scartati |
| `MAX_FRAMES_PER_VIDEO_SAFETY` | **500** | Solo anti-runaway su file corrotti — **non** è un limite utente |

**Flusso (`_sample_video_frames_diverse`):** scan ~1 fps (+ scene-change) → sharpness (Laplacian normalizzato) → se sotto `min_sharpness` scarta → confronto `_histogram_correlation` con frame già accettati dallo stesso video → se troppo simile scarta → altrimenti **yield** (tutti quelli che passano).

**Esempio video telefono 120 s (2 min):** ~120 candidati @ 1 fps → tipicamente **40–90** frame puliti+diversi (dipende da motion blur e pose statiche), vs **≤ 5** con la policy precedente o **≤ 120** ridondanti senza dedup per similarità.

**Rationale:** l'output target è **generazione video**; i clip sorgente sono miniere per **motion ed espressione** (piega braccia, sorriso, interazioni anatomiche, deformazioni, capelli al vento, ecc.). Foto statiche da sole non bastano. La dedup è **solo** per similarità visiva, non per conteggio.

---

## Comando test aggiornato

```bash
python test_forest_v2.py
python test_forest_v2.py --subjects 1 2 5 --similarity-threshold 0.55
```

Log: `test_forest_v2.log` — stati `OK`, `IDENTITY_LOW`, `IDENTITY_FAIL` (mai silenziosi).
