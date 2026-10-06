"""VoiceConverter over the helper process ``convert_host.py`` (stage 16, TZ 1.21).

The process starts on the first request with the interpreter of its own environment and lives
until ``close()``; it also ends by itself when its stdin closes (the app died). One request at a
time: a JSON line + float32 samples each way (see ``convert_host.py``). The helper's own output
goes to a log file, so a full pipe never blocks it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import numpy as np

from subvoicer.core.errors import EngineError
from subvoicer.core.models import AudioClip

log = logging.getLogger(__name__)

HOST_SCRIPT = Path(__file__).with_name("convert_host.py")
STOP_TIMEOUT_S = 5
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # pythonw app: no console for the helper


class HostConverter:
    def __init__(
        self,
        python: Path,
        models_dir: Path,
        log_file: Path | None = None,
        script: Path = HOST_SCRIPT,
    ) -> None:
        self.python = python
        self.models_dir = models_dir
        self.log_file = log_file
        self.script = script
        self._process: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self._lock = threading.Lock()

    def ping(self) -> dict[str, Any]:
        """Starts the helper if needed; the answer names the GPU."""
        header, _ = self._call({"op": "ping"})
        return header

    def load(self, kind: str, model: str) -> None:
        self._call({"op": "load", "kind": kind, "model": model})

    def convert(
        self, clip: AudioClip, kind: str, model: str, params: Mapping[str, float | str]
    ) -> AudioClip:
        header, samples = self._call(
            {
                "op": "convert",
                "kind": kind,
                "model": model,
                "params": dict(params),
                "rate": clip.sample_rate,
            },
            clip.samples,
        )
        if samples is None:
            raise EngineError("Голоса персонажей: вспомогательный процесс не вернул звук")
        return AudioClip(samples, int(header["rate"]))

    def close(self) -> None:
        with self._lock:
            self._stop()

    # ------------------------------------------------------------------ process

    def _call(
        self, header: dict[str, Any], audio: np.ndarray | None = None
    ) -> tuple[dict[str, Any], np.ndarray | None]:
        with self._lock:
            process = self._ensure()
            assert process.stdin is not None and process.stdout is not None
            if audio is not None:
                header["frames"] = len(audio)
            try:
                process.stdin.write(json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n")
                if audio is not None:
                    process.stdin.write(np.ascontiguousarray(audio, dtype="<f4").tobytes())
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise EngineError("вспомогательный процесс завершился")
                answer: dict[str, Any] = json.loads(line)
                samples = None
                if answer.get("ok") and "frames" in answer:
                    size = int(answer["frames"]) * 4
                    data = process.stdout.read(size)
                    if len(data) != size:
                        raise EngineError("вспомогательный процесс вернул звук не целиком")
                    samples = np.frombuffer(data, dtype="<f4").astype(np.float32)
            except (OSError, ValueError, EngineError) as exc:
                self._stop()
                raise EngineError(f"Голоса персонажей: {exc}{self._log_hint()}") from exc
            if not answer.get("ok"):
                raise EngineError(f"Голоса персонажей: {answer.get('error', 'ошибка')}")
            return answer, samples

    def _ensure(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        if not self.python.is_file():
            raise EngineError(f"Голоса персонажей не установлены: нет {self.python}")
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_file.open("ab")
        log.info("Запускаю вспомогательный процесс голосов персонажей")
        try:
            self._process = subprocess.Popen(
                [str(self.python), str(self.script), str(self.models_dir)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log if self._log is not None else subprocess.DEVNULL,
                creationflags=NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as exc:
            raise EngineError(f"Голоса персонажей: процесс не запустился: {exc}") from exc
        return self._process

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()  # EOF: the helper exits by itself
                process.wait(timeout=STOP_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=STOP_TIMEOUT_S)
            log.info("Вспомогательный процесс голосов персонажей остановлен")
        if self._log is not None:
            self._log.close()
            self._log = None

    def _log_hint(self) -> str:
        return f" (подробности — {self.log_file})" if self.log_file is not None else ""
