"""Serve Muse Glimmer 30B (open weights, Apache-2.0) and the corner-triage LoRA on Modal with vLLM.

    modal run modal/glimmer_serve.py::download        # CPU: BF16 weights (~60 GB) into the Volume, once
    modal deploy modal/glimmer_serve.py
    export GLIMMER_BASE_URL=https://abssi-mustapha--corner-case-glimmer-serve.modal.run/v1
    export GLIMMER_MODEL=muse-glimmer GLIMMER_TRIAGE_MODEL=corner-triage

OpenAI-compatible (/v1/chat/completions). One H100 holds the BF16 weights; the adapter
from modal/glimmer_lora_train.py is served next to the base model under its own name.
The container scales to zero after SCALEDOWN_S idle and never runs more than one copy,
so the worst case is one H100-hour per hour of use. First request after idle waits for
a cold start (weights load from the Volume).
"""
import subprocess
from pathlib import Path

import modal

HF_ID = "meta-models/Muse-Glimmer-30B"
REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
BASE_NAME, TRIAGE_NAME = "muse-glimmer", "corner-triage"
ADAPTER = "/lora/corner-triage"
GPU = "H100"
SCALEDOWN_S = 300

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.33.0", "huggingface_hub[hf_transfer]")
    # FlashInfer's sampler JIT-compiles with nvcc, which the slim image lacks; use vLLM's own sampler
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_FLASHINFER_SAMPLER": "0"})
)
weights = modal.Volume.from_name("glimmer-weights", create_if_missing=True)
lora = modal.Volume.from_name("glimmer-lora", create_if_missing=True)
vllm_cache = modal.Volume.from_name("glimmer-vllm-cache", create_if_missing=True)
app = modal.App("corner-case-glimmer", image=image)


@app.function(cpu=4, memory=8192, timeout=90 * 60, volumes={"/root/.cache/huggingface": weights})
def download() -> str:
    from huggingface_hub import snapshot_download

    path = snapshot_download(HF_ID, revision=REVISION)
    weights.commit()
    return path


@app.function(gpu=GPU, scaledown_window=SCALEDOWN_S, max_containers=1, timeout=60 * 60,
              volumes={"/root/.cache/huggingface": weights, "/lora": lora, "/root/.cache/vllm": vllm_cache},
              env={"HF_HUB_OFFLINE": "1"})
@modal.concurrent(max_inputs=32)
@modal.web_server(port=8000, startup_timeout=20 * 60)
def serve():
    cmd = ["vllm", "serve", HF_ID, "--revision", REVISION, "--served-model-name", BASE_NAME,
           "--host", "0.0.0.0", "--port", "8000", "--max-model-len", "8192",
           "--gpu-memory-utilization", "0.92", "--reasoning-parser", "muse_glimmer",
           "--limit-mm-per-prompt", '{"image": 0, "video": 0}']
    if Path(ADAPTER, "adapter_config.json").exists():
        cmd += ["--enable-lora", "--max-lora-rank", "16", "--max-loras", "1",
                "--lora-modules", f"{TRIAGE_NAME}={ADAPTER}"]
    subprocess.Popen(cmd)
