"""Character-voice helper process (stage 16, TZ 1.21, 1.25): RVC v2 and so-vits-svc 4 on the
NVIDIA GPU, or on the CPU in an environment with torch for the CPU.

A separate program, run by ``convert_client.py`` with the interpreter of its own environment
(``voice_convert/env``, ``resources/voice_convert/requirements.txt``)::

    python convert_host.py <models dir>
    python convert_host.py --strip <G.pth>   (installer: keep only the weights of a so-vits model)

It imports nothing from the app. Protocol over stdin/stdout, one request at a time: a JSON line,
then ``frames`` little-endian float32 samples; the answer has the same shape.

    {"op": "ping"}                          -> {"ok": true, "device": "...", "memory_mb": M}
                                               (device "cpu" without CUDA, memory 0)
    {"op": "load", "model": "vi", "kind": "rvc"}
                                            -> {"ok": true}  (load ahead, with a warm-up)
    {"op": "convert", "model": "vi", "kind": "rvc", "params": {...}, "rate": 48000,
     "frames": N} + N*4 bytes               -> {"ok": true, "rate": R, "frames": M} + M*4 bytes
    any failure                             -> {"ok": false, "error": "..."}

EOF on stdin ends the process (also when the app dies). Models load on first use, weights only.
The random noise of both converters is seeded per request: the same line sounds the same.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import types
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

RVC_DEFAULTS = {"key": 0, "index": 0.75, "protect": 0.33, "rms": 0.25}
SVC_DEFAULTS = {"key": 0, "noise": 0.4, "f0": "crepe"}
CONTENTVEC_HUB_ID = "lengyue233/content-vec-best"  # the name so-vits-svc-fork asks transformers for
SEED = 0


def strip(path: Path) -> None:
    """A so-vits-svc checkpoint without its training state: the optimizer is two thirds of the
    file and is never used for conversion (``load_checkpoint`` reads model, iteration and
    learning_rate only)."""
    import torch

    data = torch.load(path, map_location="cpu", weights_only=True)
    slim = {
        "model": data["model"],
        "iteration": data.get("iteration", 0),
        "learning_rate": data.get("learning_rate", 0.0),
        "optimizer": None,
    }
    temporary = path.with_suffix(".slim")
    torch.save(slim, temporary)
    os.replace(temporary, path)


def main() -> None:
    if sys.argv[1] == "--strip":
        strip(Path(sys.argv[2]))
        return
    models_dir = Path(sys.argv[1])
    # The protocol owns the real stdout; anything the libraries print goes to stderr.
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    host = Host(models_dir)
    requests = sys.stdin.buffer
    while True:
        line = requests.readline()
        if not line:
            return
        try:
            request = json.loads(line)
            audio = _read_audio(requests, int(request.get("frames", 0)))
            header, out = host.handle(request, audio)
        except Exception as exc:  # every failure goes back to the app
            traceback.print_exc()
            header, out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, None
        _write(protocol, header, out)


def _read_audio(stream: BinaryIO, frames: int) -> np.ndarray:
    data = stream.read(frames * 4)
    if len(data) != frames * 4:
        raise EOFError("Звук пришёл не целиком")
    return np.frombuffer(data, dtype="<f4").astype(np.float32)


def _write(stream: BinaryIO, header: dict[str, Any], audio: np.ndarray | None) -> None:
    if audio is not None:
        header["frames"] = len(audio)
    stream.write(json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n")
    if audio is not None:
        stream.write(np.ascontiguousarray(audio, dtype="<f4").tobytes())
    stream.flush()


class Host:
    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self.assets = models_dir / "assets"
        self._torch: Any = None
        self._rvc: dict[str, Any] = {}
        self._svc: dict[str, Any] = {}
        self._hubert: Any = None
        self._rmvpe: Any = None
        self.device = "cuda"

    def handle(self, request: dict[str, Any], audio: np.ndarray) -> tuple[dict[str, Any], Any]:
        torch = self._setup()
        if request["op"] == "ping":
            self._import_converters()  # a broken environment shows up here, not on a line
            if self.device == "cpu":
                return {"ok": True, "device": "cpu", "memory_mb": 0}, None
            memory = round(torch.cuda.max_memory_allocated() / 2**20)
            return {"ok": True, "device": torch.cuda.get_device_name(0), "memory_mb": memory}, None
        kind, model = request.get("kind"), request.get("model")
        if request["op"] == "load":
            if kind == "rvc":
                self._rvc_model(model)
            elif kind == "svc":
                self._svc_model(model)
                self._convert_svc(model, np.zeros(16_000, np.float32), 16_000, dict(SVC_DEFAULTS))
            else:
                raise ValueError(f"Неизвестный тип модели {kind!r}")
            return {"ok": True}, None
        if request["op"] != "convert":
            raise ValueError(f"Неизвестная команда {request['op']!r}")
        params = request.get("params", {})
        rate = int(request["rate"])
        if kind == "rvc":
            out, out_rate = self._convert_rvc(model, audio, rate, {**RVC_DEFAULTS, **params})
        elif kind == "svc":
            out, out_rate = self._convert_svc(model, audio, rate, {**SVC_DEFAULTS, **params})
        else:
            raise ValueError(f"Неизвестный тип модели {kind!r}")
        return {"ok": True, "rate": out_rate}, out

    # ------------------------------------------------------------------ setup

    def _setup(self) -> Any:
        if self._torch is not None:
            return self._torch
        import torch

        # the environment decides: torch for CUDA, or the CPU build (rvc picks the device itself)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        load = torch.load

        def weights_only(*args: Any, **kwargs: Any) -> Any:
            kwargs["weights_only"] = True  # never unpickle code from a model file
            return load(*args, **kwargs)

        torch.load = weights_only
        torch.set_grad_enabled(False)
        self._torch = torch
        return torch

    def _import_converters(self) -> None:
        _install_fairseq_shim()
        os.environ["rmvpe_root"] = str(self.assets)  # noqa: SIM112 - the names rvc reads
        import rvc.modules.vc.modules  # noqa: F401
        import so_vits_svc_fork.inference.core  # noqa: F401

    # ------------------------------------------------------------------ RVC

    def _convert_rvc(
        self, model: str, audio: np.ndarray, rate: int, p: dict[str, Any]
    ) -> tuple[np.ndarray, int]:
        import librosa

        vc, index = self._rvc_model(model)
        self._torch.manual_seed(SEED)  # after a possible warm-up: same line, same sound
        audio16 = librosa.resample(audio, orig_sr=rate, target_sr=16_000)
        peak = np.abs(audio16).max() / 0.95
        if peak > 1:
            audio16 = audio16 / peak
        out = vc.pipeline.pipeline(
            vc.hubert_model,
            vc.net_g,
            0,  # speaker id
            audio16,
            "",  # input path: only for f0 files
            {"npy": 0, "f0": 0, "infer": 0},
            int(p["key"]),
            "rmvpe",
            index if float(p["index"]) > 0 else "",
            float(p["index"]),
            vc.if_f0,
            3,  # filter radius
            vc.tgt_sr,
            0,  # no resampling
            float(p["rms"]),
            vc.version,
            float(p["protect"]),
            None,  # f0 file
        )
        return np.asarray(out, dtype=np.float32) / 32768, int(vc.tgt_sr)

    def _rvc_model(self, model: str) -> tuple[Any, str]:
        if model in self._rvc:
            return self._rvc[model]
        _install_fairseq_shim()
        os.environ["rmvpe_root"] = str(self.assets)  # noqa: SIM112 - the names rvc reads
        from rvc.modules.vc.modules import VC

        folder = self.models_dir / model
        weights = _one(folder, "*.pth")
        index = _one(folder, "*.index", required=False)
        os.environ["index_root"] = os.environ["weight_root"] = str(folder)  # noqa: SIM112
        vc = VC()
        vc.get_vc(str(weights))
        if self._hubert is None:
            from rvc.modules.vc.utils import load_hubert

            self._hubert = load_hubert(vc.config, str(self.assets / "contentvec"))
        vc.hubert_model = self._hubert
        if self._rmvpe is not None:
            vc.pipeline.model_rmvpe = self._rmvpe  # one pitch model for all RVC voices
        self._rvc[model] = (vc, str(index) if index else "")
        # warm-up: the first pass is several times slower (CUDA kernels, the pitch model)
        self._convert_rvc(model, np.zeros(16_000, np.float32), 16_000, dict(RVC_DEFAULTS))
        self._rmvpe = getattr(vc.pipeline, "model_rmvpe", None)
        return self._rvc[model]

    # ------------------------------------------------------------------ so-vits-svc

    def _convert_svc(
        self, model: str, audio: np.ndarray, rate: int, p: dict[str, Any]
    ) -> tuple[np.ndarray, int]:
        import librosa

        svc = self._svc_model(model)
        self._torch.manual_seed(SEED)
        target = int(svc.target_sample)
        out = svc.infer_silence(
            librosa.resample(audio, orig_sr=rate, target_sr=target),
            speaker=next(iter(svc.spk2id.__dict__)),
            transpose=int(p["key"]),
            noise_scale=float(p["noise"]),
            f0_method=str(p["f0"]),
        )
        return np.asarray(out, dtype=np.float32), target

    def _svc_model(self, model: str) -> Any:
        if model in self._svc:
            return self._svc[model]
        self._local_contentvec()
        from so_vits_svc_fork.inference.core import Svc

        folder = self.models_dir / model
        svc = Svc(
            net_g_path=folder / "G.pth", config_path=folder / "config.json", device=self.device
        )
        self._svc[model] = svc
        return svc

    def _local_contentvec(self) -> None:
        """so-vits-svc-fork asks transformers for ContentVec by its hub id; give it the copy the
        installer downloaded, so nothing is fetched at run time."""
        import transformers

        model_class = transformers.HubertModel
        if getattr(model_class, "_subvoicer_local", False):
            return
        original = model_class.from_pretrained.__func__
        local = str(self.assets / "contentvec")

        def from_pretrained(cls: Any, name: Any, *args: Any, **kwargs: Any) -> Any:
            if name == CONTENTVEC_HUB_ID:
                name = local
            return original(cls, name, *args, **kwargs)

        model_class.from_pretrained = classmethod(from_pretrained)
        model_class._subvoicer_local = True


def _install_fairseq_shim() -> None:
    """rvc 0.3.5 loads HuBERT through fairseq, which has no Windows wheels; the same model in the
    transformers format (ContentVec) answers what the RVC pipeline asks of it."""
    if "fairseq" in sys.modules:
        return
    from torch import nn
    from transformers import HubertConfig, HubertModel

    class WithProjection(HubertModel):
        def __init__(self, config: HubertConfig) -> None:
            super().__init__(config)
            self.final_proj = nn.Linear(config.hidden_size, config.classifier_proj_size)

    class ContentVec(nn.Module):
        def __init__(self, path: str) -> None:
            super().__init__()
            self.model = WithProjection.from_pretrained(path)
            self.final_proj = self.model.final_proj

        def extract_features(
            self, source: Any, padding_mask: Any = None, output_layer: int = 12
        ) -> tuple[Any]:
            hidden = self.model(source, output_hidden_states=True).hidden_states
            return (hidden[output_layer],)

    def load_model_ensemble_and_task(paths: list[str], suffix: str = "") -> tuple[Any, Any, Any]:
        return [ContentVec(paths[0])], None, None

    fairseq = types.ModuleType("fairseq")
    checkpoint_utils = types.SimpleNamespace(
        load_model_ensemble_and_task=load_model_ensemble_and_task
    )
    fairseq.checkpoint_utils = checkpoint_utils  # type: ignore[attr-defined]
    sys.modules["fairseq"] = fairseq
    sys.modules["fairseq.checkpoint_utils"] = checkpoint_utils  # type: ignore[assignment]


def _one(folder: Path, pattern: str, required: bool = True) -> Path | None:
    found = sorted(folder.glob(pattern))
    if not found and required:
        raise FileNotFoundError(f"Нет файла {pattern} в {folder}")
    return found[0] if found else None


if __name__ == "__main__":
    main()
