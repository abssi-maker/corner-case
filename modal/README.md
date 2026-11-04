# Muse Glimmer on Modal: base model + corner-triage LoRA

Muse Glimmer 30B (Meta, open weights, Apache-2.0) runs on Modal with vLLM. Next to the base
model, the same endpoint serves **corner-triage**, a QLoRA adapter that learned one skill from
our simulator: read one intersection under a stress future and return its risk band, near-miss
rate, main conflict and the single change that cuts risk most. Full numbers: `evidence/finetune.json`.

## Use it

```bash
export GLIMMER_BASE_URL=https://abssi-mustapha--corner-case-glimmer-serve.us-east.modal.run/v1
export GLIMMER_MODEL=muse-glimmer
export GLIMMER_TRIAGE_MODEL=corner-triage
```

OpenAI-compatible `/v1/chat/completions`. One H100, scale to zero after 5 min idle, at most one
container. **The first request after idle waits ~150 s** (cold start); later calls take ~2 s for
`corner-triage`. The prompt the adapter expects is `cornercase.finetune.triage.messages()`.

```bash
curl $GLIMMER_BASE_URL/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"muse-glimmer","messages":[{"role":"user","content":"What is a leading pedestrian interval?"}]}'
```

## Result (300 scenarios at 6 corners never seen in training)

| | corner-triage (LoRA) | base Glimmer, 3-shot | always "daylighting" |
|---|---|---|---|
| Valid JSON | **100%** | 87.7% | 100% |
| Best change correct | **51.3%** | 21.7% | 48.3% |
| Risk cut captured (of best possible, simulated) | **74.1%** | 39.4% | 69.9% |
| Risk band correct | **78.3%** | 52.7% | 26.7% |
| Top conflict correct | **47.7%** | 5.7% | 47.3% |
| Near-miss/h error (MAE) | **24.7** | 41.6 | 64.2 |
| Latency p50 / p95 | 1.9 s / **2.0 s** | 1.6 s / 25.0 s | - |
| Est. cost per call | **$0.00013** | $0.00039 | - |

- LoRA vs base: best change +29.7 points, 95% CI +18.0 to +41.7 (bootstrap resampling whole corners).
- LoRA vs "always daylighting": best change +3.0 points, 95% CI -0.7 to +7.3, **not significant**.
  The LoRA recommends daylighting in 222 of 300 cases and only ever picks 3 of the 13 changes.
  Its clear gain over the trivial rule is risk estimation (band 78% vs 27%, MAE 25 vs 64).
- Simulated near-misses, not injuries. The LoRA learns our simulator, not real crashes.

## What was verified (2026-11-04)

| Check | Result | Where |
|---|---|---|
| Weights | `meta-models/Muse-Glimmer-30B` rev `cec94fcf`, not gated, 59.6 GB BF16 | HF API |
| 4-bit training base | `unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit` rev `ecedda39` | HF API |
| Training stack | unsloth 2026.11.1, transformers 5.15.0, trl 0.22.2, peft 0.21.2, torch 2.11 cu128 | Unsloth Glimmer notebook; installed versions recorded |
| vLLM | 0.33.0 registers `MuseGlimmerForConditionalGeneration` (SupportsLoRA) and a `muse_glimmer` reasoning parser | vLLM source at v0.33.0 |
| Pricing | H100 $0.001097/s, CPU $0.0000131/core/s, memory $0.00000222/GiB/s | modal.com/pricing |

## Reproduce

```bash
uv tool install modal && modal setup          # Modal CLI is not a project dependency
uv run --with modal modal run -m cornercase.finetune.make_dataset   # 3,600 scenarios on Modal CPUs, ~2.5 min
modal run modal/glimmer_lora_train.py::download_base                # 4-bit base into Volume glimmer-weights
modal run modal/glimmer_lora_train.py                               # QLoRA, 1 epoch, ~18 min on one H100
modal run modal/glimmer_serve.py::download                          # BF16 base into the Volume
modal deploy modal/glimmer_serve.py
GLIMMER_BASE_URL=... uv run python modal/glimmer_lora_eval.py       # -> data/finetune/eval.json
```

- **Data**: 30 real corners perturbed (walkers x0.4-2.5, vehicles x0.6-1.4, LPI 0/4/7/10 s, widths +-20%,
  6 stress futures). Each scenario is labelled by simulating today plus every valid change from
  `cornercase/agent_value.py` MENU. The best change is the one that cuts corridor risk most within
  +8 s/vehicle (risk pushed to the next block counts against a turn ban). Split by corner: W 32, 37,
  42, 47, 52, 57 St are test only. Seed and source hash: `data/finetune/manifest.json`.
- **Training**: QLoRA 4-bit, r=16, alpha=32, q/k/v/o + MLP in all 52 language-model layers (vision
  frozen), 1 epoch (188 steps, batch 16), lr 2e-4, loss on assistant tokens only. Train loss
  0.53 → 0.29, val loss 0.28, 15 min training, 28 GiB peak.
- **Eval**: same system prompt for both models; base also gets 3 solved training examples;
  temperature 0, max 1,024 tokens.

## Cost (actual, `modal billing report`)

**$3.55 total** on 2026-11-04 (read 08:57 ET): serving tests + eval $1.78, training incl. two
smoke runs $1.63, dataset $0.13. Volumes: ~85 GB, inside the free 1 TiB.

## Gotchas we hit

- `pip install unsloth` without a pin resolved to 2025.7.2 (pre-Glimmer); pin unsloth and unsloth_zoo.
- FlashInfer's sampler JIT-compiles with nvcc, absent from the slim image: `VLLM_USE_FLASHINFER_SAMPLER=0`.
- The LoRA leaves out the attention output gate (vLLM renames it), so adapter names map 1:1.
- Masking from `<|start|>assistant` (not `... to=user<|message|>`) teaches the model to answer
  directly instead of opening a reasoning channel: 47 output tokens per call instead of ~190.
- The endpoint has no auth; `max_containers=1` caps spend at one H100-hour per hour of use.
