"""Optional Style-Bert-VITS2 narration for the Web UI's bot lines.

Mirrors the g2p+synthesis round trip of
``pooh_body/scripts/style_bert_vits2_tts.py``'s ``synthesize()``, but for a
best-effort, non-ROS setting: the Web UI has no sound_play server to fall
back to, so a line that cannot be synthesized in time is simply shown as
text, same as an illustration that never finishes in time. There is no
retry or fallback voice here by design.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

import requests

DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class TTSConfig:
    """Synthesis server + voice settings. Defaults match new_model3 at the
    pitch/intonation it was fine-tuned to speak at (see
    style_bert_vits2_tts.py's SYNTHESIS_PARAMS comment)."""

    server_url: str
    model: str = "new_model3"
    model_file: str = "model_assets/new_model3/new_model3.safetensors"
    speaker: str = "new_model3"
    style: str = "Neutral"
    style_weight: float = 1.0
    speed: float = 0.7
    sdp_ratio: float = 0.3
    noise: float = 0.6
    noisew: float = 0.8
    pitch_scale: float = 1.0
    intonation_scale: float = 1.0
    timeout: float = DEFAULT_TIMEOUT
    # The synthesis language; /api/g2p reads Japanese only, so a voice in
    # another language is synthesized from the text alone.
    language: str = "JP"
    use_g2p: bool = True

    @classmethod
    def for_language(cls, language: str, server_url: str) -> "TTSConfig":
        """The voice for the session language ("ja" or "en", see VOICES)."""
        return cls(server_url=server_url, **VOICES[language])

    def request_base(self) -> dict:
        return dict(
            model=self.model,
            modelFile=self.model_file,
            speaker=self.speaker,
            style=self.style,
            styleWeight=self.style_weight,
            speed=self.speed,
            sdpRatio=self.sdp_ratio,
            noise=self.noise,
            noisew=self.noisew,
            pitchScale=self.pitch_scale,
            intonationScale=self.intonation_scale,
            language=self.language,
        )

    def cache_key(self, text: str) -> str:
        payload = repr((text, sorted(self.request_base().items())))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Each language's voice: Japanese is new_model3 (the dataclass's defaults).
# English is pooh_en, an ordinary (not JP-Extra) model, at the speed of its
# training data. Its server is another one (one model per server process):
# pass that one's URL (e.g. http://127.0.0.1:8100) as --tts-server-url.
VOICES: dict[str, dict] = {
    "ja": {},
    "en": dict(
        model="pooh_en",
        model_file="model_assets/pooh_en/pooh_en.safetensors",
        speaker="pooh_en",
        language="EN",
        use_g2p=False,
        speed=1.0,
    ),
}


def synthesize(config: TTSConfig, text: str) -> bytes:
    """One full g2p+synthesis round trip; raises requests.RequestException
    or ValueError (a non-JSON /api/g2p response) on failure."""
    with requests.Session() as session:
        request = dict(config.request_base(), text=text, moraToneList=[])
        if config.use_g2p:
            g2p = session.post(
                config.server_url + "/api/g2p", json={"text": text}, timeout=config.timeout)
            g2p.raise_for_status()
            request["moraToneList"] = g2p.json()
        response = session.post(
            config.server_url + "/api/synthesis", json=request, timeout=config.timeout)
        response.raise_for_status()
        return response.content


class TTSCache:
    """Process-lifetime WAV cache keyed by text+voice settings.

    Many lines are identical across participants (fixed utterances, common
    phrasing), so caching avoids re-synthesizing them and lets one slow
    first request benefit every later session. A per-key lock collapses a
    burst of identical requests (e.g. every participant's opening line
    firing at once) into a single synthesis instead of one per session.
    Nothing here is persisted to disk: a restart simply starts the cache
    over, which is fine since a missing cache entry just means that one
    line plays without audio.
    """

    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self._wavs: dict[str, bytes] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, key: str) -> bytes | None:
        return self._wavs.get(key)

    def synthesize(self, text: str) -> tuple[str, bytes]:
        """Return (cache_key, wav) for ``text``; raises on failure, and
        never caches one."""
        key = self.config.cache_key(text)
        cached = self._wavs.get(key)
        if cached is not None:
            return key, cached
        with self._lock_for(key):
            cached = self._wavs.get(key)
            if cached is not None:
                return key, cached
            wav = synthesize(self.config, text)
            self._wavs[key] = wav
            return key, wav
