"""Verifica que PyTorch ve la GPU y puede calcular en ella."""

import torch

print("torch", torch.__version__, "| CUDA", torch.version.cuda)
assert torch.cuda.is_available(), "PyTorch no detecta la GPU"

name = torch.cuda.get_device_name(0)
cap = torch.cuda.get_device_capability(0)
arch = f"sm_{cap[0]}{cap[1]}"
print("GPU", name, "|", arch, "| soportadas:", torch.cuda.get_arch_list())
assert arch in torch.cuda.get_arch_list(), f"Este PyTorch no trae kernels para {arch}"

x = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
(x @ x).float().sum().item()
free, total = torch.cuda.mem_get_info()
print(f"matmul bf16 OK | VRAM libre {free / 2**30:.1f} / {total / 2**30:.1f} GiB")
