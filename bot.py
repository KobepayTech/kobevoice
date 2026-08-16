"""KobeVoice — a real-time voice agent built on Pipecat.

Audio flows through a single pipeline:

    transport.in -> STT -> user aggregator -> LLM -> TTS -> transport.out -> assistant aggregator

The aggregator pair owns conversation state: the user side turns transcripts into
context messages, the assistant side records what was actually spoken (so an
interrupted reply is stored truncated, matching what the caller heard).
"""

import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.fish.tts import FishAudioTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

# Opus 5 is the default. Voice is latency-sensitive, so this is the first knob to
# turn if replies feel slow — claude-sonnet-5 responds faster at some quality cost.
LLM_MODEL = os.getenv("KOBEVOICE_LLM_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """You are KobeVoice, a helpful voice assistant.

Your replies are converted to speech, so write for the ear: plain sentences, no
markdown, no bullet points, no emoji, no code blocks. Spell out symbols and
abbreviations the way a person would say them.

Keep answers short — one or two sentences unless the caller asks for detail.
Ask a clarifying question when a request is ambiguous rather than guessing.
"""


def _require(name: str) -> str:
    """Read a required API key, failing with a clear message instead of a 401."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def build_tts():
    """Construct the configured text-to-speech service.

    Both providers stream PCM back over a websocket, so they are interchangeable
    in the pipeline — only credentials and voice identifiers differ.
    """
    provider = os.getenv("KOBEVOICE_TTS", "cartesia").strip().lower()

    if provider == "cartesia":
        return CartesiaTTSService(
            api_key=_require("CARTESIA_API_KEY"),
            settings=CartesiaTTSService.Settings(
                voice=os.getenv("CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"),
            ),
        )

    if provider == "fish":
        # Fish Audio's hosted API (api.fish.audio). This is a different thing from
        # self-hosting the fish-speech weights — see the licensing note in README.
        return FishAudioTTSService(
            api_key=_require("FISH_API_KEY"),
            settings=FishAudioTTSService.Settings(voice=_require("FISH_VOICE_ID")),
        )

    raise RuntimeError(f"Unknown KOBEVOICE_TTS provider {provider!r}. Use 'cartesia' or 'fish'.")


# Every supported transport needs VAD on the input side so the pipeline can tell
# when the caller has stopped speaking and interrupt playback when they start again.
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    ),
    "daily": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    ),
    "twilio": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    ),
}


async def run_bot(transport) -> None:
    """Build and run the voice pipeline for one connected caller."""
    stt = DeepgramSTTService(api_key=_require("DEEPGRAM_API_KEY"))
    tts = build_tts()
    llm = AnthropicLLMService(
        api_key=_require("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(model=LLM_MODEL),
    )

    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}])
    aggregators = LLMContextAggregatorPair(
        context,
        # Sharing the VAD analyzer lets the user aggregator close a turn on the
        # same silence signal the transport uses, instead of a second guess.
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # Tear down a session the caller has abandoned instead of holding the
        # transport and provider connections open indefinitely.
        idle_timeout_secs=180,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected — greeting")
        # The system prompt alone doesn't produce speech; this frame runs the LLM
        # once so the agent talks first instead of waiting for the caller.
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected — ending session")
        await worker.cancel()

    await WorkerRunner().run(worker)


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat runner calls for each new session."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
