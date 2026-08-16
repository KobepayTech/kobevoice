"""Self-hosted Chatterbox TTS plugin for LiveKit Agents.

Chatterbox (Resemble AI, MIT) does zero-shot voice cloning from roughly ten
seconds of reference audio, which is why it's here: cloned agent voices without
sending a reference sample to a third party.

Two properties of the upstream model shape this file:

- **It has no streaming API.** `generate()` returns one complete waveform, so
  time-to-first-audio equals full synthesis time for whatever text it's given.
  We therefore advertise ``streaming=False`` and let LiveKit's ``StreamAdapter``
  feed us a sentence at a time — the agent starts speaking after sentence one
  rather than after the whole reply. See ``build_tts()`` below.
- **It is synchronous and compute-bound.** Calling it directly would block the
  event loop and stall every other call on the worker, so generation runs in a
  thread executor.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from livekit.agents import tokenize, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

# Chatterbox emits 24 kHz mono. Declared rather than hardcoded at the call site
# so the emitter and the model can't silently disagree.
NUM_CHANNELS = 1


@dataclass
class _Options:
    voice_sample: str | None
    exaggeration: float
    cfg_weight: float
    temperature: float
    device: str


class ChatterboxTTS(tts.TTS):
    """LiveKit TTS backed by a locally-hosted Chatterbox model."""

    def __init__(
        self,
        *,
        voice_sample: str | None = None,
        device: str | None = None,
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        temperature: float = 0.8,
    ) -> None:
        """
        Args:
            voice_sample: Path to a reference clip (~10s) to clone. None uses the
                model's default voice.
            device: "cuda", "mps", or "cpu". Defaults to $CHATTERBOX_DEVICE, else
                "cuda" — CPU runs ~12x slower than realtime and cannot hold a live
                call, so it is never chosen implicitly.
            exaggeration: Emotional intensity.
            cfg_weight: Classifier-free guidance weight; higher tracks the
                reference voice more closely.
            temperature: Sampling randomness.
        """
        if voice_sample is not None and not Path(voice_sample).is_file():
            # Fail at construction rather than on the first caller's turn.
            raise FileNotFoundError(f"voice_sample not found: {voice_sample}")

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=NUM_CHANNELS,
        )
        self._opts = _Options(
            voice_sample=voice_sample,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
            device=device or os.getenv("CHATTERBOX_DEVICE", "cuda"),
        )
        self._model = None
        self._load_lock = asyncio.Lock()

    async def _ensure_model(self):
        """Load weights once, off the event loop.

        Guarded by a lock so concurrent calls arriving together don't each start
        their own multi-second load.
        """
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                from chatterbox.tts import ChatterboxTTS as _Model

                self._model = await asyncio.to_thread(
                    _Model.from_pretrained, device=self._opts.device
                )
                if self._model.sr != self.sample_rate:
                    raise RuntimeError(
                        f"Chatterbox sample rate {self._model.sr} != declared "
                        f"{self.sample_rate}; audio would play at the wrong pitch."
                    )
        return self._model

    def prewarm(self) -> None:
        """Load weights at worker start so the first caller doesn't pay for it."""
        asyncio.ensure_future(self._ensure_model())

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _ChunkedStream(tts.ChunkedStream):
    """Synthesizes one span of text and emits it as PCM."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        chatterbox: ChatterboxTTS = self._tts  # type: ignore[assignment]
        model = await chatterbox._ensure_model()
        opts = chatterbox._opts

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=chatterbox.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        # Blocking and compute-bound — must not run on the event loop.
        wav = await asyncio.to_thread(
            model.generate,
            self._input_text,
            audio_prompt_path=opts.voice_sample,
            exaggeration=opts.exaggeration,
            cfg_weight=opts.cfg_weight,
            temperature=opts.temperature,
        )

        # torch float32 in [-1, 1] -> 16-bit little-endian PCM.
        samples = wav.squeeze().detach().cpu().numpy()
        # Clip before scaling: values slightly outside [-1, 1] would otherwise
        # wrap around and surface as loud clicks.
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        output_emitter.push(pcm.tobytes())
        output_emitter.flush()


def build_tts(
    *,
    voice_sample: str | None = None,
    device: str | None = None,
) -> tts.TTS:
    """Chatterbox wrapped so the agent speaks sentence-by-sentence.

    Without this wrapper the caller hears nothing until the entire reply has been
    synthesized. The adapter splits on sentence boundaries and synthesizes each in
    turn, so audio starts after the first sentence instead of the last.
    """
    return tts.StreamAdapter(
        tts=ChatterboxTTS(voice_sample=voice_sample, device=device),
        sentence_tokenizer=tokenize.basic.SentenceTokenizer(),
    )
