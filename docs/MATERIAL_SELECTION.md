# Material selection — idee oltre tier A–F (pipeline v3/VIP)

Documento complementare a `QUALITY_LEAP_OPTIONS.md`: azioni concrete per migliorare **scelta** e **uso** del materiale prima/dopo training LoRA.

## Idee (nuove)

1. **Dedup embedding (InsightFace cosine)** — Calcolare embedding 512-d su ogni frame candidato; scartare coppie con cosine > 0.92 rispetto a un frame già accettato. Riduce ridondanza senza perdere pose diverse.

2. **Tutti i frame puliti e diversi + scene-cut** - Da ogni video estrarre ogni frame nitido e non ridondante (~1 fps + taglio scena via correlazione istogramma in \_sample_video_frames_diverse\ / \uto_curator.py\); nessun cap per video salvo \MAX_FRAMES_PER_VIDEO_SAFETY\ anti-file corrotti.

3. **Cluster purge identità** — Dopo raccolta tier A, clustering k-means su embedding; eliminare cluster con centroide troppo lontano dalla mediana (outlier / altra persona / riflesso).

4. **Soglie minime pre-train** — Non avviare training v3 se `face_front` < 25 o body tiers < 50 combinati; per VIP se biometric < 15 usare fallback `face_front` (già in `auto_vip_curator_s2.py`).

5. **Dual-LoRA inference** — In generazione: peso VIP 0.85–1.0 su close-up, v3 combined 0.6–0.8 su full-body; regolare per soggetto in base a gate InsightFace su closeup test.

6. **Reference face auto-pick** — Scegliere `face.jpg` dal tier A con massimo `(lora_score + sharpness*0.05)` invece del primo crop VIP o file manuale.

7. **Diversità EXIF/data** — Raggruppare per giorno/ora EXIF; limitare export a max N frame per sessione per evitare overfit su stessa luce/outfit.

8. **Body consistency gate** — Per tier body, confrontare embedding corpo (torso crop) o colore pelle medio HSV tra frame; scartare outlier prima dello zip Replicate.

## Operativo immediato

- VIP: `--sharpness-threshold 55–60`, **no** `--move-rejects` (opt-in in `run_vip_pipeline.py`).
- S4/S5 con poco materiale: `--merge-inputs --lora-export` + fallback tier A.
- Monitor Replicate 403 model limit: eliminare modelli legacy non usati prima di nuovi train VIP/v3.
