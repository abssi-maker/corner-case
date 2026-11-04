"""QLoRA on Muse Glimmer 30B for corner triage (Unsloth, 4-bit base, one H100).

    modal run modal/glimmer_lora_train.py::download_base      # CPU: 4-bit base into the Volume
    modal run modal/glimmer_lora_train.py --max-steps 3 --run-name smoke   # pipeline check
    modal run modal/glimmer_lora_train.py                       # full run -> /lora/corner-triage

Recipe from Unsloth's Muse Glimmer notebook (transformers 5.15.0, trl 0.22.2,
train_on_responses_only with Glimmer's ATEM markers). The adapter targets only the
language model's q/k/v/o and MLP projections: vLLM serves exactly those (it renames the
attention output gate, so that one is left out). Hard stop: BUDGET_S of wall time inside
the container, TIMEOUT_S for the whole call (~$8 of H100 at most).
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
BASE_4BIT = "unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit"
MAX_LEN = 1024
R, ALPHA = 16, 32
TARGETS = (r".*language_model.*\.layers\.\d+\."
           r"(?:self_attn\.(?:q_proj|k_proj|v_proj|o_proj)|mlp\.(?:gate_proj|up_proj|down_proj))")
N_LAYERS = 52
BUDGET_S = 100 * 60
TIMEOUT_S = 115 * 60

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
    .apt_install("git", "build-essential")
    .pip_install("torch", "torchvision", "torchaudio", "xformers", index_url="https://download.pytorch.org/whl/cu128")
    # pinned together: an unpinned `pip install unsloth` resolved to 2025.7.2, which predates Glimmer
    .pip_install("unsloth==2026.11.1", "unsloth_zoo==2026.11.1", "transformers==5.15.0", "datasets", "hf_transfer")
    .pip_install("torchao>=0.16.0", extra_options="--no-deps --upgrade")
    .pip_install("trl==0.22.2", extra_options="--no-deps")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
weights = modal.Volume.from_name("glimmer-weights", create_if_missing=True)
lora = modal.Volume.from_name("glimmer-lora", create_if_missing=True)
app = modal.App("corner-case-glimmer-train", image=image)
VOLS = {"/root/.cache/huggingface": weights, "/lora": lora}


@app.function(cpu=4, memory=8192, timeout=60 * 60, volumes=VOLS)
def download_base() -> str:
    from huggingface_hub import model_info, snapshot_download

    snapshot_download(BASE_4BIT)
    weights.commit()
    return model_info(BASE_4BIT).sha


@app.function(gpu="H100", timeout=TIMEOUT_S, volumes=VOLS)
def train(train_msgs: list, val_msgs: list, max_steps: int = -1, epochs: float = 1.0,
          run_name: str = "corner-triage") -> dict:
    t0 = time.time()
    import re

    import torch
    from unsloth import FastModel  # must precede transformers / trl
    import peft
    import transformers
    import trl
    import unsloth
    from datasets import Dataset
    from huggingface_hub import model_info
    from transformers import TrainerCallback
    from trl import SFTConfig, SFTTrainer
    from unsloth.chat_templates import train_on_responses_only

    model, tok = FastModel.from_pretrained(model_name=BASE_4BIT, max_seq_length=MAX_LEN, load_in_4bit=True)
    t_load = time.time() - t0
    hits = [n for n, _ in model.named_modules() if re.fullmatch(TARGETS, n)]
    if len(hits) != N_LAYERS * 7:
        sample = [n for n, _ in model.named_modules() if ".layers.0." in n and "vision" not in n]
        raise RuntimeError(f"{len(hits)} target modules (want {N_LAYERS * 7}); layer-0 names: {sample[:30]}")
    model = FastModel.get_peft_model(model, target_modules=TARGETS, r=R, lora_alpha=ALPHA, lora_dropout=0,
                                     bias="none", use_gradient_checkpointing=True, random_state=3407)
    lora_mods = sorted({n.split(".lora_A")[0] for n, _ in model.named_modules() if n.endswith("lora_A")})
    assert len(lora_mods) == N_LAYERS * 7 and not any("vision" in n or "self_attn.gate_proj" in n for n in lora_mods)

    bos = tok.bos_token or ""

    def fmt(m):
        return tok.apply_chat_template(m, tokenize=False, add_generation_prompt=False).removeprefix(bos)

    ds_train = Dataset.from_dict({"text": [fmt(m) for m in train_msgs]})
    ds_val = Dataset.from_dict({"text": [fmt(m) for m in val_msgs]})
    print("EXAMPLE >>>", ds_train[0]["text"][-700:])
    text_tok = getattr(tok, "tokenizer", tok)
    n_tok = [len(text_tok(t, add_special_tokens=False).input_ids) for t in ds_train["text"][:200]]
    print(f"tokens/example (first 200): mean {sum(n_tok) / len(n_tok):.0f}, max {max(n_tok)}")

    class Guard(TrainerCallback):
        def on_step_end(self, args, state, control, **kw):
            if time.time() - t0 > BUDGET_S:
                print(f"time budget reached at step {state.global_step}; stopping")
                control.should_training_stop = True
            return control

        def on_save(self, args, state, control, **kw):
            lora.commit()
            return control

    out = f"/lora/{run_name}"
    trainer = SFTTrainer(
        model=model, tokenizer=tok, train_dataset=ds_train, eval_dataset=ds_val,
        args=SFTConfig(dataset_text_field="text", max_length=MAX_LEN, loss_type="nll",
                       per_device_train_batch_size=8, gradient_accumulation_steps=2, warmup_steps=10,
                       num_train_epochs=epochs, max_steps=max_steps, learning_rate=2e-4, logging_steps=10,
                       optim="adamw_8bit", weight_decay=0.001, lr_scheduler_type="linear", seed=3407,
                       output_dir=f"{out}-ckpt", save_strategy="steps", save_steps=60, save_total_limit=1,
                       per_device_eval_batch_size=8, eval_strategy="no", report_to="none"),
    )
    # the loss starts right after "<|start|>assistant", so the model also learns to answer the
    # user directly (" to=user") instead of opening a reasoning channel (" to=self")
    trainer = train_on_responses_only(trainer, instruction_part="<|start|>user<|message|>",
                                      response_part="<|start|>assistant")
    trainer.add_callback(Guard())
    t1 = time.time()
    stats = trainer.train()
    t_train = time.time() - t1
    ev = trainer.evaluate()
    model.save_pretrained(out)
    tok.save_pretrained(out)

    FastModel.for_inference(model)
    samples = []
    for m in val_msgs[:5]:
        ids = tok.apply_chat_template(m[:-1], add_generation_prompt=True, return_tensors="pt", tokenize=True,
                                      return_dict=True).to("cuda")
        g = model.generate(**ids, max_new_tokens=96, do_sample=False)
        samples.append({"want": m[-1]["content"],
                        "got": tok.decode(g[0][ids["input_ids"].shape[1]:], skip_special_tokens=False)})
    result = {
        "run_name": run_name, "adapter_dir": out, "base_4bit": BASE_4BIT, "base_4bit_sha": model_info(BASE_4BIT).sha,
        "versions": {"unsloth": unsloth.__version__, "transformers": transformers.__version__, "trl": trl.__version__,
                     "peft": peft.__version__, "torch": torch.__version__},
        "gpu": torch.cuda.get_device_name(0), "peak_mem_gib": round(torch.cuda.max_memory_reserved() / 2**30, 1),
        "config": {"method": "QLoRA 4-bit (Unsloth dynamic bnb)", "r": R, "alpha": ALPHA, "dropout": 0,
                   "targets": "language model q/k/v/o + MLP gate/up/down, all 52 layers", "max_len": MAX_LEN,
                   "epochs": epochs, "max_steps": max_steps, "batch": 8, "grad_accum": 2, "lr": 2e-4,
                   "optim": "adamw_8bit", "schedule": "linear, 10 warmup steps", "loss": "assistant tokens only",
                   "n_train": len(train_msgs), "n_val": len(val_msgs)},
        "steps": stats.global_step, "train_loss": round(stats.training_loss, 4), "val_loss": round(ev["eval_loss"], 4),
        "loss_log": [{"step": h["step"], "loss": h["loss"]} for h in trainer.state.log_history if "loss" in h],
        "wall_s": {"load": round(t_load), "train": round(t_train), "total": round(time.time() - t0)},
        "stopped_by_budget": time.time() - t0 > BUDGET_S, "val_samples": samples,
    }
    result = json.loads(json.dumps(result, default=float))  # torch scalars -> floats (Mac has no torch)
    Path(out, "train_run.json").write_text(json.dumps(result, indent=1))
    lora.commit()
    return result


@app.local_entrypoint()
def main(max_steps: int = -1, epochs: float = 1.0, run_name: str = "corner-triage"):
    def load(split):
        return [json.loads(l)["messages"] for l in (ROOT / "data" / "finetune" / f"{split}.jsonl").open()]

    t0 = time.time()
    res = train.remote(load("train"), load("val"), max_steps=max_steps, epochs=epochs, run_name=run_name)
    res["wall_s"]["call"] = round(time.time() - t0)
    (ROOT / "data" / "finetune" / f"train_run_{run_name}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("steps", "train_loss", "val_loss", "wall_s", "peak_mem_gib",
                                          "stopped_by_budget")}, indent=1))
    for s in res["val_samples"]:
        print("want", s["want"], "\ngot ", s["got"])
