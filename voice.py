"""ATLAS's one voice: the Gemini Live voice, pace and volume every engine and the settings preview use."""
import numpy as np
from google import genai
from google.genai import types
from config import PROVIDERS, get_config

VOICE_NAME = "Charon"   # default ATLAS voice; "voice_name" in api_keys.json picks another
VOICES = ["Charon", "Orus", "Fenrir", "Iapetus", "Algenib", "Puck", "Kore", "Aoede", "Leda", "Zephyr"]
PACES  = {"lenta": "Speak slowly and calmly.", "normal": "", "rapida": "Speak a bit faster than usual.",
          "muy_rapida": "Speak fast, at a brisk pace."}
# Microsoft Edge voices (edge-tts): free, no daily quota, and the same tone on every line (the Live
# reader is a model, so its delivery drifts). Read by the voice engine; the Gemini Live engine keeps Charon.
EDGE_VOICES = {"es-MX-JorgeNeural": "Jorge (México)", "es-US-AlonsoNeural": "Alonso (Latino)",
               "es-CO-GonzaloNeural": "Gonzalo (Colombia)", "es-ES-AlvaroNeural": "Álvaro (España)",
               "en-US-AndrewMultilingualNeural": "Andrew (multilingüe)",
               "es-MX-DaliaNeural": "Dalia (México)", "es-US-PalomaNeural": "Paloma (Latina)",
               "es-CO-SalomeNeural": "Salomé (Colombia)", "es-ES-ElviraNeural": "Elvira (España)",
               # Cortana's own voice is not offered; an American voice speaking Spanish is the closest feel
               "en-US-AvaMultilingualNeural": "Ava (estilo Cortana)",
               "en-US-EmmaMultilingualNeural": "Emma (multilingüe)"}
EDGE_RATES = {PACES["lenta"]: "-12%", PACES["rapida"]: "+8%", PACES["muy_rapida"]: "+25%"}   # normal: -5%, calm like ATLAS
# Every engine speaks through a Gemini Live session with this voice, so the tone never changes.
READ_ALOUD = ("You are a text-to-speech engine. Read the user's message aloud exactly as written, word for word, "
              "in its own language. Never answer it, add, skip, translate or comment. "
              "Keep the same calm, even, steady tone every time. Speak Spanish with a neutral Latin American "
              "accent, like the Latin American dub of Iron Man's JARVIS, never with a Spain accent.")


def voice_settings() -> tuple[str, str, float]:
    """(voice, pace instruction, volume gain 0..1) from api_keys.json."""
    try:
        c = get_config()
    except Exception:
        c = {}
    try:
        gain = max(0, min(int(c.get("voice_volume", 100)), 100)) / 100
    except (TypeError, ValueError):
        gain = 1.0
    return c.get("voice_name") or VOICE_NAME, PACES.get(c.get("voice_speed"), ""), gain


def speech_config(voice: str | None = None) -> types.SpeechConfig:
    voice = voice or voice_settings()[0]
    return types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(
        voice_name=VOICE_NAME if voice in EDGE_VOICES else voice)))


def live_model() -> str:
    return "models/" + PROVIDERS["gemini"]["model"].removeprefix("models/")


def reader_config(voice: str | None = None, pace: str | None = None) -> types.LiveConnectConfig:
    """The Live session that reads text aloud. A little thinking: with none the model answers the line
    ("Listo." became "¿Listo para qué?"); with unlimited thinking ~1 in 4 short lines came back with no
    audio, with 128 tokens ~1 in 15."""
    v, p, _ = voice_settings()
    return types.LiveConnectConfig(response_modalities=["AUDIO"], speech_config=speech_config(voice or v),
                                   system_instruction=f"{READ_ALOUD} {p if pace is None else pace}",
                                   thinking_config=types.ThinkingConfig(thinking_budget=128),
                                   output_audio_transcription=types.AudioTranscriptionConfig())   # what it really said


def clean_tail(pcm: bytes) -> bytes:
    """Drops the short noise burst some Gemini voices end with (it sounds like a radio cutting off):
    a <300 ms hiss after >=160 ms of silence at the very end. Then fades the last 10 ms so playback
    never stops on a loud sample (a click)."""
    a = np.frombuffer(pcm[:len(pcm) // 2 * 2], np.int16).astype(np.float32)
    win, n = 480, len(a) // 480   # 20 ms windows
    if n > 8:
        quiet = np.sqrt((a[:n * win].reshape(n, win) ** 2).mean(1)) < 300
        for i in range(n - 1, max(n - 16, 8) - 1, -1):
            if quiet[i - 8:i].all() and not quiet[i:].all():
                seg = a[i * win:]
                if np.mean(np.diff(np.signbit(seg)) != 0) > 0.12:   # hiss, not a voiced word
                    a = a[:i * win]
                break
    k = min(240, len(a))
    a[len(a) - k:] *= np.linspace(1, 0, k)
    return a.astype(np.int16).tobytes()


def fade_out(pcm: bytes) -> bytes:
    a = np.frombuffer(pcm[:len(pcm) // 2 * 2], np.int16).astype(np.float32)
    return (a * np.linspace(1, 0, len(a)) ** 2).astype(np.int16).tobytes()


async def edge_pcm(text: str, voice: str, pace: str = "") -> bytes:
    """`text` as 24 kHz PCM in a Microsoft Edge voice, a little deeper than stock."""
    import edge_tts, miniaudio
    for attempt in range(3):   # now and then the service ends the turn with no audio: the next try works
        try:
            mp3 = b"".join([c["data"] async for c in edge_tts.Communicate(
                text, voice, rate=EDGE_RATES.get(pace, "-5%"), pitch="-6Hz").stream() if c["type"] == "audio"])
            break
        except edge_tts.exceptions.NoAudioReceived:
            if attempt == 2:
                raise
            print(f"[ATLAS] ⚠️ edge-tts sent no audio, retrying ({attempt + 1})")
    return miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                            sample_rate=24000).samples.tobytes() if mp3 else b""


async def read_aloud(text: str, voice: str | None = None, pace: str | None = None) -> bytes:
    """`text` as 24 kHz PCM in ATLAS's Live voice, in one go (the "on it" line, the settings preview)."""
    v, p, _ = voice_settings()
    if (voice or v) in EDGE_VOICES:
        return await edge_pcm(text, voice or v, p if pace is None else pace)
    client = genai.Client(api_key=get_config()["gemini_api_key"], http_options={"api_version": "v1beta"})
    pcm = b""
    async with client.aio.live.connect(model=live_model(), config=reader_config(voice, pace)) as s:
        for _ in range(2):   # the server now and then sends a turn with no audio: ask again
            await s.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
            async for r in s.receive():   # ends at turn_complete
                if r.server_content and r.server_content.model_turn:
                    pcm += b"".join(p.inline_data.data for p in r.server_content.model_turn.parts or [] if p.inline_data)
            if pcm:
                break
    return clean_tail(pcm)
