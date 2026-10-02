# Component research for assembled FRED (2026-09-30)

Agent report (Hugging Face first), requested by Vatsal: alternatives to
Whisper, the 2B model, the router encoder and the small vision model, whether
MiniCPM has ~4B models, and datasets. Tags: [C] confirmed with source, [P]
prior knowledge, [I] inference. Design context: `Mark_II_Testing/ASSEMBLED_FRED.md`.

## 1. Tool caller (≤4B)

- **No MiniCPM5 4B** — family is MiniCPM5-1B (2026-08) and **MiniCPM5-2B**
  (2026-09-07, 2.52B, Llama arch, Q4_K_M 1.56 GB, Apache-2.0) [C: hf.co/openbmb].
  Near-4B OpenBMB models: MiniCPM3-4B (official BFCL v4 25.6%), MiniCPM-V 4.0
  (vision), AgentCPM-Explore 4B (deep-research on Qwen3-4B), BitCPM (NPU).
- MiniCPM5-2B card (vendor numbers, † = Artificial Analysis):

| Model | BFCL v4 | τ²-Telecom | τ³-Banking | IFEval | IFBench |
|---|---:|---:|---:|---:|---:|
| **MiniCPM5-2B** | **66.6** | **97.1** | **20.8†** | 86.7 | 66.3 |
| Qwen3.5-4B (FRED today) | 56.8 (Qwen's own card: 50.3) | 92.1† | 6.8† | 90.2 | 59.0 |
| LFM2.5-2.6B | 61.1 | 90.4 | 7.2† | 93.4 | 59.0 |
| granite-4.2-3B | 52.2 | 40.9 | 5.6† | 93.7 | 73.0 |
| Qwen3.5-2B | 43.6 | 69.0† | 2.1 | 77.5 | 46.0 |

- Official BFCL v4 board (2026-04, MiniCPM5 not on it): best ≤4B xLAM-2-3b
  41.2%, Qwen3-4B-2507 35.7%. 66.6 would be far above — plausible (500K
  agent SFT) but **not independently verified**.
- Verdict: "excellent at tool calling" is **partly true** — best small model
  on paper. Caveats: IFEval below the 4B (schema discipline, no-markdown);
  XML tool-call format (FRED's parser needs an XML branch for llama.cpp);
  text-only (no vision path). Gate: FRED's own suites (≥77.8% tools,
  ≥76.2% conversation).
- Picks: 16 GB — 4B thinker + 2B talker/judge, promote 2B to caller if it
  passes. 8 GB — 2B does everything (one llama-server, 2 slots).
  Runner-up LFM2.5-2.6B (revenue-capped license) / granite-4.2-3B (Apache).

## 2. Speech-to-text

| Model | VRAM | English WER | Streaming | Hindi/Hinglish | License | Windows |
|---|---|---|---|---|---|---|
| **Nemotron 3.5 ASR streaming 0.6B** (2026-06-04) | F16 1.28 GB, Q8 ~0.7 | FLEURS-en 7.9 | native, 80–1120 ms chunks | hi-IN ready, FLEURS-hi 6.8 (no code-switch) | OpenMDW-1.1 | transcribe.cpp GGUF (Vulkan/CPU); CUDA unverified |
| Whisper large-v3-turbo (today) | ~1.6 (int8 ~0.9) | ~7.8 | VAD windows | Hindi; Hinglish mixes scripts | MIT | measured, RTF 0.095 |
| Qwen3-ASR-0.6B / 1.7B | ~1.3 / ~3.5 | 1.7B: 5.76 | yes | Hindi; community Hinglish fine-tune (OpenSLR-104) | Apache | transformers ≥5.13 |
| Parakeet TDT 0.6B v2/v3 | ~1.2 | ~6.0–6.3 | no | no Hindi | CC-BY-4.0 | NeMo / sherpa-onnx |
| Granite Speech 4.1 2B | 2B | Open ASR #1 5.33 | no | no Hindi | Apache | transformers |
| Moonshine streaming-medium | 266M | — | yes | EN | MIT | ONNX, easy |

Picks: 16 GB — Nemotron 3.5 streaming (validate on Windows first), else keep
turbo; trial Qwen3-ASR-0.6B-hinglish for Hinglish. 8 GB — Nemotron Q8.

## 3. Router embedder / reranker

| Model | Params | MMTEB / MTEB-en v2 | Runtime | License |
|---|---:|---|---|---|
| Qwen3-Embedding-4B (today) | 4B | 69.45 / 74.60 | GGUF | Apache |
| **Qwen3-Embedding-0.6B** | 0.6B | 64.33 / 70.70 | GGUF (Q8 ~0.7 GB; fp16 ~1.2) | Apache |
| jina-embeddings-v5-text-nano | 239M | 65.5 / 71.0 | — | CC-BY-NC |
| EmbeddingGemma-300M | 308M | 61.15 / 68.36 | ONNX | Gemma terms |
| granite-embedding-311m-multilingual-r2 | 311M | retrieval 65.2 | ONNX/OpenVINO/GGUF, Hindi | Apache |
| Qwen3-Reranker-0.6B | 0.6B | MTEB-R 65.8 | GGUF | Apache |

Routing short utterances is closer to classification — FRED's routing suites
decide; a small SetFit-style fine-tune on FRED's own utterance→tool pairs
likely beats any swap [I]. Picks: 16 GB Qwen3-Embedding-0.6B Q8 (+reranker
only if routing drops); 8 GB EmbeddingGemma-300M int8 or granite-97m-r2.

## 4. Screen captioning / OCR

| Model | VRAM | Notes | License |
|---|---|---|---|
| **MiniCPM-V 4.6** (~1B) | ~1.9 GB Q4 + f16 mmproj | "Qwen3.5-2B level" OCR/OpenCompass; no ScreenSpot | Apache |
| Qwen3.5-4B mmproj (today) | shares thinker | ScreenSpot-Pro 60.3, OCRBench 85.0; FRED 12/12 | Apache |
| Florence-2 base/large | 0.23/0.77B | fast OCR-with-region + captions | MIT |
| PP-OCRv5/v6 (RapidOCR ONNX) | ~0.1–0.2 GB | exact line OCR | Apache |
| Windows.Media.Ocr | 0 GPU | exact on clean UI text | OS |
| GUI grounders (Holo1.5-3B, GUI-Actor-3B, UI-TARS-2B, OmniParser v2) | 2–3B | only if FRED will click things (YAGNI now) | mixed |

Picks: 16 GB MiniCPM-V 4.6 + PP-OCRv5 on the focus crop; 8 GB same, VLM
loaded on demand, or Florence-2-base.

## 5. Datasets

| Dataset | Size | License | FRED use |
|---|---|---|---|
| openbmb/UltraData-SFT-Agent-2609 | ~500K | Apache | MiniCPM5-2B's own agent SFT — in-distribution tool LoRA |
| Salesforce/xlam-function-calling-60k (gated) | 60K | CC-BY-4.0 | single/parallel calls |
| Team-ACE/ToolACE | ~11K | Apache | schema diversity |
| Nanbeige/ToolMind | 160K+200K | Apache | multi-turn |
| NousResearch/hermes-function-calling-v1 | ~10K | Apache | JSON mode |
| **nvidia/When2Call** | ~15K | CC-BY-4.0 | when NOT to call / to ask — FRED's honesty cases |
| pipecat-ai/smart-turn-data-v3.2 | 100K–1M clips | CC-BY-4.0 | fine-tune Smart Turn on Vatsal's voice / Hinglish |
| mundo-ai/TurnBench (gated) | 7.3 h, 38 convs | other | eval for L1 turn/interrupt logic |
| candor-turntaking-annotations | 172K segs | CC-BY-4.0 | text turn-completion labels |
| anyreach-ai/dualturn | ~1.1K sessions, 80 ms labels | other | frame-level turn head (Freeze-Omni style) |
| MagicHub duplex (2509.04093) | 15 h dual-track | MagicHub | backchannel/overlap examples |
| ScreenSpot / -Pro, RICO Screen2Words, Mind2Web | — | various | screen eval |
| ProactiveBench, ProactiveVideoQA, PIRA-Bench | — | various | L4 judge eval |
| CREMA-D (clean license), RAVDESS/MSP-Podcast/IEMOCAP (restricted) | — | — | emotion2vec calibration |
| VoxCeleb1/2 | 7K+ speakers | CC-BY meta | voice-ID negatives / threshold |
| ai4bharat/IndicVoices (gated) | 11.2K h | CC-BY-4.0 | Hindi ASR |
| OpenSLR-104 (MUCS Hindi-English) | ~90 h | check | Hinglish ASR fine-tune |

## Recommended stacks [I]

**16 GB (~13.2 GB incl. 1 GB reserve):** Qwen3.5-4B Q6_K + mmproj 5.6 (→
MiniCPM5-2B if it passes, frees ~5.6) · MiniCPM5-2B Q4_K_M 2.1 · Nemotron 3.5
ASR F16 ~1.4 (or turbo 1.6) · Qwen3-Embedding-0.6B Q8 0.7 · MiniCPM-V 4.6
1.9 + PP-OCRv5 0.2 + emotion2vec+ 0.4.

**8 GB (~6.9 GB; an 8 GB card exposes ~7.2–7.4 GB under WDDM — tight):**
MiniCPM5-2B one server, 2 slots ~2.5 · reserve ~0.9 · Nemotron Q8 0.7 ·
EmbeddingGemma int8 ONNX 0.35 · MiniCPM-V 4.6 on demand 1.9 + PP-OCRv5 mobile
0.15 · emotion2vec+ 0.4 (first to drop).

First checks: MiniCPM5-2B on FRED's 81-case tool suite (llama.cpp `--jinja` +
XML parser); Nemotron 3.5 on Windows/CUDA; routing suites with the 0.6B
embedder; the Qwen3.5-4B BFCL mismatch (56.8 vs 50.3).
