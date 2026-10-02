import time, torch, bitsandbytes as bnb
print("torch", torch.__version__, "bnb", bnb.__version__, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
torch.manual_seed(0)
dev = "cuda:0"
# Qwen3-8B MLP-shaped layer: 4096 -> 12288
ref = torch.nn.Linear(4096, 12288, bias=False, dtype=torch.bfloat16)
q = bnb.nn.Linear4bit(4096, 12288, bias=False, compute_dtype=torch.bfloat16, quant_type="nf4", compress_statistics=True)
q.weight = bnb.nn.Params4bit(ref.weight.data.clone(), requires_grad=False, quant_type="nf4", compress_statistics=True)
ref, q = ref.to(dev), q.to(dev)
print("weight dtype on GPU:", q.weight.dtype, "quant_state:", q.weight.quant_state is not None)
for batch in (1, 64):
    x = torch.randn(1, batch, 4096, dtype=torch.bfloat16, device=dev)
    with torch.no_grad():
        y_ref, y_q = ref(x), q(x)
        torch.cuda.synchronize(); t = time.perf_counter()
        for _ in range(50): q(x)
        torch.cuda.synchronize(); ms = (time.perf_counter() - t) / 50 * 1000
    cos = torch.nn.functional.cosine_similarity(y_ref.float().flatten(), y_q.float().flatten(), dim=0).item()
    print(f"tokens={batch:3d}  cosine vs bf16={cos:.4f}  {ms:.3f} ms/call  finite={torch.isfinite(y_q).all().item()}")
print(f"VRAM allocated {torch.cuda.memory_allocated()/2**20:.0f} MiB")
