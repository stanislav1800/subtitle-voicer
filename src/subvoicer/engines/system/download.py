"""Downloading one file over HTTPS with progress and a SHA-256 check (TZ 4.14).

The file is written next to the target as ``*.part`` and renamed only when the hash matches,
so an interrupted download never looks like a model. A ``*.part`` left by a cancelled or broken
download is continued with an HTTP Range request (TZ 1.24); a server that ignores Range sends the
whole file again. A part that is already whole (the rename failed) is only renamed; one the server
cannot continue (HTTP 416) is downloaded again from the start. Standard library only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from subvoicer.core.errors import ModelsError

log = logging.getLogger(__name__)

CHUNK = 256 * 1024
TIMEOUT_S = 30
USER_AGENT = "SubtitleVoicer"


class DownloadError(ModelsError):
    """A message for the user, in Russian."""


class Cancelled(DownloadError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK * 4):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    target: Path,
    sha256: str,
    progress: Callable[[int], None] = lambda _done: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Download ``url`` to ``target``; ``progress`` gets the bytes done so far."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = part.stat().st_size if part.is_file() else 0
    if done and sha256_of(part) == sha256:
        os.replace(part, target)
        progress(done)
        log.info("Уже скачан целиком: %s", target)
        return
    headers = {"User-Agent": USER_AGENT}
    if done:
        headers["Range"] = f"bytes={done}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            if done and response.status == 206:  # partial content: go on after the kept bytes
                with part.open("rb") as kept:
                    while chunk := kept.read(CHUNK * 4):
                        digest.update(chunk)
                mode = "ab"
                log.info("Докачиваю %s с %d байт", target.name, done)
                progress(done)
            else:
                done, mode = 0, "wb"
            with part.open(mode) as out:
                while chunk := response.read(CHUNK):
                    if cancelled():
                        raise Cancelled("Скачивание отменено")  # the part stays for later
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    progress(done)
    except DownloadError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and done:  # the kept part does not fit the file: start over
            log.warning("Сервер не может продолжить %s — качаю заново", target.name)
            part.unlink(missing_ok=True)
            download(url, target, sha256, progress, cancelled)
            return
        raise DownloadError(f"Не удалось скачать {url}: {exc}") from exc
    except OSError as exc:  # URLError, HTTPError, timeouts and disk errors are all OSError
        raise DownloadError(f"Не удалось скачать {url}: {exc}") from exc
    if digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(
            f"Файл {target.name} скачался повреждённым (не совпала контрольная сумма SHA-256)"
        )
    os.replace(part, target)
    log.info("Скачано: %s (%d байт)", target, done)
