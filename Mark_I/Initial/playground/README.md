# FRED model playground (llama.cpp)

One OpenAI-compatible endpoint, `http://localhost:8090/v1`, in front of llama.cpp's `llama-server` (CUDA build b11433 in `C:\Users\Dhiraj Vatsal\llama.cpp\b11433`, outside the repo), serving every model FRED uses. Replaces LM Studio for all model testing from 2026-10-06.

- Start: `python server.py` (background it), open `http://localhost:8090` for the playground page. Stop: `python server.py stop`.
- A request naming a model loads it on demand; asking for another chat model swaps it (one chat slot + one embedding slot, both fully on the GPU).
- `models.json` lists the models (id, path under `models_dir`, context, kind chat|embed, optional `mmproj` for vision, extra `args`). Any other .gguf under `models_dir` is picked up automatically as `local/<file>`. Add one from the page ("Register a model") or by editing the file.
- IDs match the old LM Studio ones, so `memory/run_local.py` works unchanged (`LLM_URL` overrides the address). `reasoning_effort` and `response_format` json_schema pass through.
- Measured 2026-10-06 (RTX 5060 Ti): gpt-oss-20b loads and answers a schema-forced call in 7 s, about 93 tok/s; Qwen3-8B about 80 tok/s; the Qwen3 embedder gives 2560-dim vectors.
- Not covered (not llama.cpp models): Kokoro, wake word, VAD, Smart Turn, voice ID, ASR.
