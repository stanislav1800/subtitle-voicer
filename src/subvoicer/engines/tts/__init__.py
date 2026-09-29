"""tts engine family: engine id -> 'module:Class' (see engines/registry.py)."""

TTS: dict[str, str] = {
    "silero": "subvoicer.engines.tts.silero:SileroEngine",
    "sapi5": "subvoicer.engines.tts.sapi5:Sapi5Engine",
}
