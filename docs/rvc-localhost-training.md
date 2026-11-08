# Optional RVC training entry point

This note is about a separate [Retrieval-based Voice Conversion WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) checkout. It does not change the ElevenLabs microphone commands. RVC is MIT licensed; see that project's `LICENSE` (copyright 2023 liujing04, 源文雨, and Ftps). The loopback edit below is a local patch of `webui.py` only. This repository does not vendor the RVC source or any weights.

The reviewed tree is commit `81eed5e8f68b6bed1789f682fe78cdd324495afc`. The cloud agent fetched that commit and read it. It did not run `webui.py`, train a model, or open the installed `.pt` / `.bin` files.

## What upstream actually does

`webui.py` has no `if __name__` guard. Importing it builds the Gradio app and calls launch.

| Location | Behavior |
| --- | --- |
| `webui.py:91-99` | `find_available_port` defaults to `host="0.0.0.0"` and binds that host while probing ports |
| `webui.py:127-132` | `queued_app.launch(server_name="0.0.0.0", inbrowser=not config.noautoopen, ...)` with no `share` argument |
| `webui.py:2841-2844` | `--colab` uses `launch(share=True)`. Every other launch uses the `0.0.0.0` helper |
| `configs/config.py:191-219` | Arguments are `--port` (default 7865), `--pycmd`, `--colab`, `--noparallel`, `--noautoopen`, `--dml`. There is no host argument |
| `webui.py:28-42` | Import deletes everything in `./TEMP` except `jieba.cache` |
| `webui.py:164-165` | Import constructs `Config()` and `VC(config)`. `VC.__init__` does not load a checkpoint |
| `webui.py:1881-1883` | The conversion input is `gr.Audio(source="upload")`. Startup does not request a microphone |

Gradio 3.14's default for an omitted `share` is false, so the non-Colab path is not a public Gradio tunnel. It still listens on all IPv4 interfaces and opens a browser unless `--noautoopen` is passed. That is not a loopback training UI. There is no supported flag at this commit that changes `server_name`.

The safe route is the patcher in this repo. It rewrites only those launch anchors, and only when `webui.py` and the load-site files still match the pinned SHA256 values. The patched file binds `127.0.0.1`, passes `share=False` and `inbrowser=False`, and raises if `--colab` is set. IPv6 `::1` is not added. `0.0.0.0`, `::`, and `localhost` are refused as bind addresses.

`rvc prepare` writes `webui.py.vcm-upstream.bak` and then the patched file. A second run does nothing if the patched hash matches. A changed upstream file, a backup that is not the pinned original, or a patched result whose hash differs from `4cd410f526dbce044dba98848e306bf8073fb3e14dd2f2c30bb6416ee87b7cea` is refused and the original bytes are kept. If `.git` exists, `HEAD` must be the pinned commit. A zip extract with no `.git` is accepted only when the file hashes match.

This was not executed as a live Gradio server. Do not treat `state=patched` as a working trainer.

## Commands

From this repository, against the external checkout (the Windows path is an example, not a path this agent used):

```powershell
python -m voice_clone_mic.rvc_tools check --root C:\Users\souji\vcbench\rvc
python -m voice_clone_mic.rvc_tools prepare --root C:\Users\souji\vcbench\rvc
python -m voice_clone_mic.rvc_tools audit
python -m voice_clone_mic.rvc_tools audit --file C:\Users\souji\vcbench\rvc\assets\pretrained_v2\f0G40k.pth
```

`voice-clone-mic rvc ...` is the same CLI. `check` and `prepare` do not start Gradio. `launch` is implemented but was not run here:

```powershell
python -m voice_clone_mic.rvc_tools launch --root C:\Users\souji\vcbench\rvc --python C:\Users\souji\vcbench\rvc\.venv\Scripts\python.exe --port 7865
```

That process is `webui.py --noautoopen --port 7865` with the working directory set to the checkout. It is allowed only after `prepare` verifies the patch. The official training stages remain the upstream CLI in `docs/en/cli.md` of that checkout: `train/preprocess.py`, `train/dataset/extract_f0.py`, `train/dataset/extract_hubert_feature.py`, and `train/train.py`. Those commands still use upstream's own `torch.load` calls. This launcher does not wrap them and does not make them safe.

## Checkpoint loading

Torch 2.4.1 `torch.load` (`torch/serialization.py` around the `load` definition, assignment at the `weights_only is None` branch) sets `weights_only` to `False` when the caller omits it, and warns. It does not automatically retry a failed `weights_only=True` load as `False`. Passing `False` is the unsafe pickle path.

Transformers 4.49.0 `load_state_dict` (`src/transformers/modeling_utils.py`, `weights_only: bool = True`) calls `torch.load(..., weights_only=True)`. The `except` block checks for a Git LFS pointer. It does not call `torch.load` a second time with `weights_only=False`.

| Artifact | Upstream call at the pinned commit | Object the code expects | Safe-load status |
| --- | --- | --- | --- |
| `assets/hubert_base/pytorch_model.bin` | `infer/hubert.py:53` `from_pretrained(..., local_files_only=True)` | HuBERT weights for Transformers. No `weights_only` override, so 4.49 asks for `weights_only=True` | Fail-closed if the bin contains extra globals. This file was not opened |
| `assets/rmvpe/rmvpe.pt` | `infer/rmvpe.py:526` `torch.load(model_path, map_location="cpu")` then `load_state_dict` | A tensor state dict. `infer/rmvpe.py:512-518` uses `rmvpe.onnx` instead when the device string contains `privateuseone` | The `.pt` call is the torch 2.4.1 unsafe default. DirectML extraction passes `torch_directml.device()` (`train/dataset/extract_f0.py:36-42` and `:96-98`), which takes the ONNX branch. The `.pt` bytes were not opened |
| `assets/pretrained_v2/f0G40k.pth` | `train/train.py:111` `torch.load(path, map_location="cpu")["model"]` | A dict with a `"model"` tensor state dict | Unsafe default. `weights_only=True` is plausible for a tensor dict and unproven for this file. No fallback is provided |
| `assets/pretrained_v2/f0D40k.pth` | `train/train.py:273` and `:279`, same `["model"]` load | Same as the generator | Same limitation |

Related unsafe calls, not patched: `train/utils.py:23` and `:103` (full checkpoints, including optimizer state), `train/process_ckpt.py:74`, `:92`, `:224`, `:247`, `infer/vc/modules.py:174`, and `infer/cli.py:95`. Optimizer state is not a pure tensor dict. A matching SHA256 does not make any of these pickles safe.

`rvc audit --file` reads a zip member named `data.pkl`, or the raw bytes if the file is not a zip, and disassembles pickle opcodes with `pickletools`. It does not call `pickle.loads` or `torch.load`. `verdict=candidate` means the globals it saw are on the torch 2.4.1 weights-only name list, or there were none. `verdict=blocked` means stop. The tool never recommends `weights_only=False`.

## Still required before recordings or training

- Run `check` and `prepare` on the Windows checkout. Confirm `state=patched` and that the backup file exists.
- Only then decide whether to start `launch` locally. Watch that the process listens on `127.0.0.1` and does not print a public Gradio URL.
- Run `audit --file` on `pytorch_model.bin`, `rmvpe.pt`, `f0G40k.pth`, and `f0D40k.pth`. A blocked verdict means do not train with the current `train.py` loader.
- Upstream training still calls unsafe `torch.load` for the pretrained G/D files. This patch does not change that, because those files were not inspected and a failed safe load must not fall back.
- No dataset, microphone recording, driver install, or training run is authorized by these commands.
