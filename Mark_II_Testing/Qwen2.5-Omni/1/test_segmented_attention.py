"""Self-check: smoke_nf4's per-segment encoder attention == upstream's dense-mask SDPA."""
import copy
import torch
from transformers.models.qwen2_5_omni import modeling_qwen2_5_omni as m

torch.manual_seed(0)
dev = "cuda"
cu = torch.tensor([0, 7, 20, 20, 33], dtype=torch.int32, device=dev)  # incl. an empty segment
x = torch.randn(33, 64, device=dev)

vis = m.Qwen2_5OmniVisionSdpaAttention(64, num_heads=4).to(dev)
rot = torch.randn(33, 8, device=dev)  # head_dim 16 -> rotary half 8
cfg = m.Qwen2_5OmniAudioEncoderConfig(d_model=64, encoder_attention_heads=4)
aud = m.Qwen2_5OmniAudioSdpaAttention(cfg).to(dev)
with torch.no_grad():
    ref_v, ref_a = vis(x, cu, rot), aud(x, cu)
    import smoke_nf4
    smoke_nf4._segmented_encoder_attention()
    new_v, new_a = vis(x, cu, rot), aud(x, cu)
for name, r, n in (("vision", ref_v, new_v), ("audio", ref_a, new_a)):
    err = (r - n).abs().max().item()
    print(f"{name}: max abs diff {err:.2e}")
    assert err < 1e-5, name
print("OK")
