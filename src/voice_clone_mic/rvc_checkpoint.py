"""Static checkpoint audit. This module never calls torch.load or pickle.loads."""

from __future__ import annotations

import pickletools
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Names accepted by torch 2.4.1 torch._weights_only_unpickler._get_allowed_globals
# for the classes that source lists explicitly, plus dtype and *Storage names that
# the same function registers dynamically. This is a scanner allowlist, not a loader.
_EXPLICIT_GLOBALS = {
    "collections.OrderedDict",
    "collections.Counter",
    "torch.nn.parameter.Parameter",
    "torch.serialization._get_layout",
    "torch.Size",
    "torch.Tensor",
    "torch.device",
    "torch._tensor._rebuild_from_type_v2",
    "torch._utils._rebuild_parameter",
    "torch._utils._rebuild_parameter_with_state",
    "torch._utils._rebuild_qtensor",
    "torch._utils._rebuild_tensor",
    "torch._utils._rebuild_tensor_v2",
    "torch._utils._rebuild_tensor_v3",
    "torch._utils._rebuild_sparse_tensor",
    "torch._utils._rebuild_meta_tensor_no_storage",
    "torch._utils._rebuild_nested_tensor",
    "torch._utils._rebuild_wrapper_subclass",
    "torch.float32",
    "torch.float16",
    "torch.float64",
    "torch.bfloat16",
    "torch.int64",
    "torch.int32",
    "torch.int16",
    "torch.int8",
    "torch.uint8",
    "torch.bool",
    "torch.complex64",
    "torch.complex128",
    "torch.per_tensor_affine",
    "torch.per_tensor_symmetric",
    "torch.per_channel_affine",
    "torch.per_channel_symmetric",
    "torch.per_channel_affine_float_qparams",
}

_STRING_OPCODES = {
    "STRING",
    "BINSTRING",
    "SHORT_BINSTRING",
    "UNICODE",
    "BINUNICODE",
    "SHORT_BINUNICODE",
    "BINUNICODE8",
}


@dataclass(frozen=True)
class ScanReport:
    kind: str
    globals_found: tuple[str, ...]
    verdict: str
    detail: str

    @property
    def allows_unsafe_fallback(self) -> bool:
        return False


@dataclass(frozen=True)
class LoadSite:
    artifact: str
    path: str
    line: int
    call: str
    expected_object: str
    torch_241_behavior: str
    weights_only_true: str


LOAD_SITES = (
    LoadSite(
        artifact="assets/hubert_base/pytorch_model.bin",
        path="infer/hubert.py",
        line=53,
        call="HubertModelWithFinalProj.from_pretrained(..., local_files_only=True)",
        expected_object="Transformers HuBERT state dict inside a PyTorch zip or pickle",
        torch_241_behavior=(
            "transformers 4.49 load_state_dict defaults weights_only=True and does not "
            "call torch.load again when that fails."
        ),
        weights_only_true=(
            "Already requested by transformers 4.49. A file with extra globals fails closed. "
            "This audit did not open the installed bin."
        ),
    ),
    LoadSite(
        artifact="assets/rmvpe/rmvpe.pt",
        path="infer/rmvpe.py",
        line=526,
        call='torch.load(model_path, map_location="cpu") then load_state_dict',
        expected_object="Tensor state dict, not a wrapped training checkpoint",
        torch_241_behavior=(
            "The call omits weights_only. torch 2.4.1 treats that as weights_only=False "
            "and emits FutureWarning. DirectML skips this call and loads the sibling .onnx."
        ),
        weights_only_true=(
            "Would be the right call only after a header scan shows allowlisted globals. "
            "Do not catch a failure and retry with weights_only=False."
        ),
    ),
    LoadSite(
        artifact="assets/pretrained_v2/f0G40k.pth",
        path="train/train.py",
        line=111,
        call='torch.load(path, map_location="cpu")["model"]',
        expected_object='Dict whose "model" value is a tensor state dict',
        torch_241_behavior="Omitted weights_only uses the torch 2.4.1 unsafe default.",
        weights_only_true=(
            "A dict of tensors and strings can load. Optimizer objects or numpy scalars "
            "would fail. No fallback is acceptable. The installed file was not opened here."
        ),
    ),
    LoadSite(
        artifact="assets/pretrained_v2/f0D40k.pth",
        path="train/train.py",
        line=273,
        call='torch.load(hps.pretrainD, map_location="cpu")["model"]',
        expected_object='Dict whose "model" value is a tensor state dict',
        torch_241_behavior="Same omitted weights_only default as the generator.",
        weights_only_true="Same limitation as f0G40k.pth. The installed file was not opened here.",
    ),
)


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def scan_pickle_globals(data: bytes) -> tuple[str, ...]:
    """Record GLOBAL names. pickletools disassembly does not build the objects."""

    found: list[str] = []
    stack: list[object] = []
    memo: dict[int, object] = {}
    for opcode, arg, _position in pickletools.genops(data):
        name = opcode.name
        if name in _STRING_OPCODES:
            stack.append(_as_text(arg))
        elif name == "GLOBAL":
            module, qual = _as_text(arg).split(" ", 1)
            found.append(f"{module}.{qual}")
            stack.append(f"{module}.{qual}")
        elif name == "INST":
            module, qual = _as_text(arg).split(" ", 1)
            found.append(f"{module}.{qual}")
            stack.append(f"{module}.{qual}")
        elif name == "STACK_GLOBAL":
            if len(stack) < 2:
                found.append("?.STACK_GLOBAL")
                stack.append("?.STACK_GLOBAL")
                continue
            qual = stack.pop()
            module = stack.pop()
            full = f"{module}.{qual}"
            found.append(full)
            stack.append(full)
        elif name == "MEMOIZE" and stack:
            memo[len(memo)] = stack[-1]
        elif name in {"PUT", "BINPUT", "LONG_BINPUT"}:
            if stack:
                memo[int(arg)] = stack[-1]
        elif name in {"GET", "BINGET", "LONG_BINGET"}:
            stack.append(memo.get(int(arg)))
        elif name == "POP" and stack:
            stack.pop()
        elif name == "POP_MARK":
            while stack:
                item = stack.pop()
                if item == "MARK":
                    break
        elif name == "MARK":
            stack.append("MARK")
        elif name == "STOP":
            break
        else:
            stack.append(None)
    return tuple(found)


def _allowed(global_name: str) -> bool:
    if global_name in _EXPLICIT_GLOBALS:
        return True
    module, _dot, qual = global_name.rpartition(".")
    return module == "torch" and qual.endswith("Storage")


def scan_checkpoint_file(path: Path) -> ScanReport:
    blob = path.read_bytes()
    if blob.startswith(b"PK"):
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            return ScanReport("zip", (), "unreadable", "Zip header is not a readable archive.")
        pickles = [name for name in archive.namelist() if name.endswith("data.pkl")]
        if not pickles:
            return ScanReport("zip", (), "unreadable", "No data.pkl member. Nothing was unpickled.")
        found: list[str] = []
        for name in pickles:
            found.extend(scan_pickle_globals(archive.read(name)))
        kind = "pytorch-zip"
    else:
        try:
            found = list(scan_pickle_globals(blob))
        except Exception as exc:
            return ScanReport("pickle", (), "unreadable", f"Disassembly failed: {type(exc).__name__}.")
        kind = "pickle"
    unique = tuple(dict.fromkeys(found))
    blocked = [item for item in unique if not _allowed(item)]
    if blocked:
        return ScanReport(
            kind,
            unique,
            "blocked",
            "Disassembly found globals outside the torch 2.4.1 weights_only allowlist. "
            "Do not load this file and do not retry with weights_only=False. "
            + ", ".join(blocked),
        )
    return ScanReport(
        kind,
        unique,
        "candidate",
        "Disassembly saw only allowlisted globals or no globals. This did not execute the pickle "
        "and is not proof the file is safe to load.",
    )


def render_load_sites() -> str:
    lines = []
    for site in LOAD_SITES:
        lines.append(
            f"{site.artifact}\n"
            f"  {site.path}:{site.line} {site.call}\n"
            f"  object: {site.expected_object}\n"
            f"  torch 2.4.1: {site.torch_241_behavior}\n"
            f"  weights_only=True: {site.weights_only_true}\n"
            "  unsafe fallback: disabled"
        )
    return "\n".join(lines)
