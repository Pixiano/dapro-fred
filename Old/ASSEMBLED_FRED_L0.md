# Assembled FRED: L0 Senses (finalised 2026-10-02)

Layer 0 is the always-on perception layer: microphone, voice activity, speaker
separation, screen change, screen text, screen description, camera presence.
Parent plan: `Mark_II_Testing/ASSEMBLED_FRED.md` (section 6). English is the
working language throughout.

## Final picks

| # | Component | Pick | Runs on | VRAM | Latency | Status |
|---|---|---|---|---:|---|---|
| 1 | Mic | 16 kHz, 80 ms chunks, the stream the wake word already owns | CPU | 0 | 80 ms | existing |
| 2 | Wake word | openWakeWord `hey_fred` | CPU | 0 | 80 ms | existing, **frozen** |
| 3 | VAD | webrtcvad now, then Silero v5 ONNX | CPU | 0 | <1 ms/frame | webrtcvad verified; Silero to wire |
| 4 | Speaker separation (optional layer) | Nemotron-3-Diarization 99M, beside voice ID | **CPU (allowed)** | 0 | 0.32 s buffer (ultra-low-latency config) | to test (below) |
| 5 | Screen change detector | tile-max difference, threshold 0.990, 1 fps, no model | CPU | 0 | ms | designed from Venus/4.5 measurements |
| 6 | Focus crop | native-resolution crop of the changed region | CPU | 0 | ms | designed (took small-text reads 0/9 to 8/9) |
| 7 | OCR | Windows.Media.Ocr on the crop | CPU | 0 | ~50-150 ms (estimate) | to measure; CPU approval pending |
| 7b | Complex OCR (on demand: dense documents, tables, formulas, code, PDFs) | GLM-OCR 0.9B (MIT; GGUF at `ggml-org/GLM-OCR-GGUF`) | GPU, loaded on demand and unloaded after | ~1.5 GB while loaded (estimate) | ~1.9 pages/s on the vendor's hardware | added 2026-10-02; llama.cpp run unverified |
| 8 | Screen description | MiniCPM-V 4.6 Q4_K_M + f16 image encoder | GPU | ~1.9 GB | ~0.5 s per caption (estimate) | llama.cpp support unverified |
| 9 | Camera presence | existing | CPU | 0 | - | existing |

**L0 GPU total: about 1.9 GB** (only the screen describer). Everything else is CPU.

## Why

- **Mic, wake word:** already working; the wake word is frozen by standing order.
- **VAD on CPU:** tiny and fast; a turn-detection model, so allowed under the GPU-only rule.
- **Change detector + crop before any model:** most seconds nothing changes (Venus skipped ~37% of idle seconds). The crop, not a bigger model, is what made small text readable.
- **OCR separate from the describer:** a vision model can invent text that is not on screen. Rule from the plan: any text quoted from the describer must also appear in the OCR output, so nothing unseen is ever spoken. Windows OCR is the free, exact check.
- **MiniCPM-V 4.6 as describer:** about 1.9 GB, does captions and reads text, Apache-2.0.
- **Nemotron-3-Diarization as an optional layer, not a replacement for voice ID:** it says who spoke when (up to 8 anonymous speakers, streaming, ~99M parameters, OpenMDW-1.1 licence). It never says "this is Vatsal", so voice ID (L1) stays the identity gate. Its value is overlap, TV or a second person, and speaker memory across a long session, i.e. fewer false barge-ins and a second signal when the voice changes (tiredness, cold, distance).

## Considered and not picked

| Candidate | Why not |
|---|---|
| PaliGemma-3B-pt-224 | 224 px cannot read small text; "pt" base needs per-task fine-tuning; gated; 2.9B (~5.8 GB in 16-bit), more than OCR + describer together; unverified in our llama.cpp |
| GOT-OCR-2.0 (580M, ~1.2 GB) | Superseded by GLM-OCR for the complex-OCR tier (GLM-OCR reports 94.62 on OmniDocBench V1.5, #1 on its card; GOT-OCR is older) |
| Gemma 4 E4B as a unified OCR + describer | Dropped by Vatsal 2026-10-02: ~6 GB, no VRAM saving versus the split, weak tool use |
| Qwen3.5-4B image path (100% on 12 screenshots) | Fallback describer; shares the brain's model slot (contention) |
| Florence-2 | Fallback describer |
| TEN VAD | Alternative to Silero if Silero disappoints |

## Open checks (gates before this is final)

1. **MiniCPM-V 4.6 in our llama.cpp build:** loads and describes? (unverified)
2. **Windows OCR on real crops:** accuracy vs MiniCPM-V reading the same crops, vs GOT-OCR as the upper bound. Use the 12-screenshot set FRED already has.
3. **Unification test:** does MiniCPM-V alone (OCR + caption) pass the grounding rule well enough to drop the separate OCR? Default stays split until it does.
4. **Nemotron diarization on this PC:** runs on CPU (NeMo or the NeMo-Speech.cpp runtime) in real time? Then test offline on recordings with a TV, two speakers and overlap: adopt only if it cuts false barge-ins versus VAD + voice ID alone.
5. **CPU approvals:** Nemotron diarization approved (Vatsal, 2026-10-02). Windows OCR on CPU: pending Vatsal's OK ("other small models: discuss later").
6. **Silero v5** swap-in: measure against webrtcvad on the same recordings.
7. **GLM-OCR:** runs in our llama.cpp build (or Ollama/vLLM)? Its benchmark is documents, not screenshots, so test on the 12-screenshot set plus a few dense pages and tables. Decide the trigger (user asks "read this document/table", or Windows OCR confidence is low) and that it unloads after use so it never sits in VRAM.

## Pi 5 (noted, discuss later)

Vatsal has a Raspberry Pi 5. Small CPU-only parts of L0/L1 could move there: wake word, VAD, Smart Turn, voice ID, diarization. Open questions for that discussion: ARM builds (NeMo-Speech.cpp, ONNX Runtime), network latency to the PC, Windows OCR is Windows-only (needs a replacement on the Pi), and what stays co-located with the mic. Nothing in this file depends on the Pi.

## VRAM note

L0 adds ~1.9 GB GPU. The whole-plan sum is in `Mark_II_Testing/ASSEMBLED_FRED.md` section 7 and is redone once all layers are final.
