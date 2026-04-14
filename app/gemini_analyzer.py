import os
import time
from google import genai
from google.genai import errors, types

from app.runtime import configure_logging, load_local_env

load_local_env()
logger, _LOG_PATH = configure_logging("gemini")

# Retry configuration for transient Gemini failures
_GEMINI_MAX_RETRIES = max(1, int(os.getenv("GEMINI_MAX_RETRIES", "6")))
_GEMINI_RETRY_DELAY_S = max(1.0, float(os.getenv("GEMINI_RETRY_DELAY_SECONDS", "5")))
_GEMINI_MAX_RETRY_DELAY_S = max(_GEMINI_RETRY_DELAY_S, float(os.getenv("GEMINI_MAX_RETRY_DELAY_SECONDS", "45")))
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def get_gemini_api_key(api_key=None):
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY was not found. Add your key to a .env file or enter it when the program starts."
        )
    return key


def find_viral_clip(whisper_segments, api_key=None):
    return find_viral_clips(whisper_segments, api_key=api_key, clip_count=1)


def _retry_delay_seconds(attempt_index: int) -> float:
    return min(_GEMINI_MAX_RETRY_DELAY_S, _GEMINI_RETRY_DELAY_S * (2 ** attempt_index))


def _is_retryable_gemini_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "high demand",
            "rate limit",
            "429",
            "resource exhausted",
            "resource has been exhausted",
            "temporarily unavailable",
            "unavailable",
            "deadline exceeded",
            "try again later",
            "internal error",
            "overloaded",
        )
    )


def _emit_progress(progress_callback, message: str) -> None:
    if progress_callback is None:
        return
    progress_callback("analyzing", message)


def find_viral_clips(whisper_segments, api_key=None, clip_count=3, progress_callback=None):
    logger.info("Requesting clip selection from Gemini for %s clip(s).", clip_count)

    timed_text = ""
    for segment in whisper_segments:
        timed_text += f"[{segment['start']:.1f}s] {segment['text']}\n"

    prompt = f"""Act as an elite video editor specialising in viral short-form vertical content
(TikTok / YouTube Shorts / Reels). Analyse this transcript with timestamps and
identify the {clip_count} strongest non-overlapping segments.

SELECTION CRITERIA (in priority order):
1. STRONG HOOK — The segment MUST open with something immediately attention-grabbing:
   a bold claim, surprising fact, emotional statement, or provocative question.
   The best hooks fit one of these viral trigger patterns:
   • "I never expected…" / "Nobody talks about…" / "Here's what they don't tell you…"
   • A contrarian statement that challenges a common belief
   • A shocking number, statistic, or outcome stated in the first sentence
   • A sudden reveal or unexpected twist in the speaker's story
   • A strong personal conviction delivered with visible emotion
   If the first sentence is boring, the Short will be skipped.
2. SELF-CONTAINED — Each segment must deliver a complete thought or argument.
   Never cut mid-sentence or mid-argument. The viewer must understand the point
   without having seen the full video.
3. EMOTIONAL ENGAGEMENT — Prefer moments with strong conviction, passion, humour,
   disagreement, or surprise. Avoid calm, monotone, or meandering discussion.
   Signs of high emotional engagement: raised voice, laughter, long pause before
   a reveal, use of words like "never", "always", "I can't believe", "the truth is".
4. CLARITY — Prefer sections with ONE clear speaker talking with energy.
   Avoid sections where multiple people talk over each other, where speech is
   filler-heavy ("um", "uh", long pauses).
   NOTE: It is perfectly fine when a speaker's voice continues OVER visuals
   (B-roll, slides, demos, screen shares) — as long as the SPOKEN WORDS alone
   are interesting and self-contained, the clip works great.  Only skip segments
   where the speaker *explicitly addresses* what's on screen and those words
   become meaningless without the visual (e.g. "look at this number here",
   "on this slide you can see…").
5. DISTINCT ANGLES — Each clip should cover a different topic, idea, or emotion.
   Do NOT pick two clips from the same conversation thread.

CONTENT-TYPE AWARENESS — this video may be any of the following:
• Digital meeting / video call / conference — skip greetings, admin talk,
  "can you hear me?", muted-mic moments, scheduling, and roll-call segments.
  Focus on the meatiest discussion, strongest opinions, and key decisions.
• Webinar / presentation / slides — skip introductions, "thank you for joining,"
  and housekeeping. Pick the most compelling arguments, surprising data points,
  expert insights, or bold predictions.  NEVER pick a segment that only makes
  sense when a slide or screen share is visible — the viewer will only see the
  speaker or a cropped frame.
• News / broadcast — skip anchor hand-offs, weather, and sports scores.
  Pick the most newsworthy, emotional, or opinion-heavy sound bites.
  Prioritise: exclusive revelations, eyewitness accounts, expert verdicts,
  or a moment where a person says something surprising or controversial on camera.
• Interview / podcast (two or more people) — pick moments where ONE person
  delivers a strong, standalone statement. The ideal clip is a single compelling
  monologue (20–60 s) where the speaker makes a bold claim, tells a personal
  story, or expresses a strong opinion.
  AVOID: back-and-forth crosstalk that loses context in isolation; soft
  "yeah, totally, I agree" exchanges; the host asking a question without
  the answer appearing in the same segment.
  PREFER: the guest's most quotable answer, a story with a clear arc ("I
  used to think X, but then…"), or a heated disagreement where one side
  makes a memorable point.
• Solo content / vlog / tutorial — pick the most hook-worthy, quotable moments.
  Prefer "aha" moments, demonstrations, and strong personal opinions.

IMPORTANT FILTERING — ALWAYS skip segments that contain:
• Explicit references to visuals the short viewer cannot decode without the
  original video: "look at this chart", "as you can see on screen", "let me
  share my screen", "on this slide", "next slide please", "I'll show you"
  — BUT voice-over narration that explains an idea WITHOUT pointing at the
  screen is FINE, even if the original video shows B-roll or slides.
• Filler-heavy passages with lots of "um", "uh", "you know", long pauses
• Meta-commentary about the meeting/call: "we're running out of time", "sorry
  for the technical difficulties", "can everyone see my screen?"

Each segment should be between 30 and 60 seconds long.

Transcript:
{timed_text}

Reply ONLY with this exact format (no extra explanations):
CLIP 1
TITLE: [Strong hook title — this becomes the headline overlay on the Short]
START: [Only the second number, for example 120.5]
END: [Only the second number, for example 155.0]
REASON: [Short reason why this segment is compelling]

CLIP 2
TITLE: [Strong hook title]
START: [Only the second number, for example 220.0]
END: [Only the second number, for example 260.0]
REASON: [Short reason]

Continue until CLIP {clip_count}."""

    client = genai.Client(api_key=get_gemini_api_key(api_key))
    last_error = None
    text = ""

    try:
        for attempt in range(_GEMINI_MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=_GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        response_mime_type="text/plain",
                    ),
                )
                text = (response.text or "").strip()
                if text:
                    break
                # Empty response — retry once the backend has had time to recover.
                last_error = RuntimeError("Gemini returned an empty response.")
                if attempt < _GEMINI_MAX_RETRIES - 1:
                    delay_seconds = _retry_delay_seconds(attempt)
                    logger.warning("Gemini returned an empty response. Retrying in %.1fs (%s/%s).", delay_seconds, attempt + 2, _GEMINI_MAX_RETRIES)
                    _emit_progress(
                        progress_callback,
                        f"CLIP_SELECTION | Gemini returned an empty response. Retrying in {delay_seconds:.0f}s ({attempt + 2}/{_GEMINI_MAX_RETRIES})...",
                    )
                    time.sleep(delay_seconds)
            except errors.APIError as error:
                last_error = RuntimeError(f"Gemini request failed: {error.message}")
                retryable = _is_retryable_gemini_error(error.message or "")
                if retryable and attempt < _GEMINI_MAX_RETRIES - 1:
                    delay_seconds = _retry_delay_seconds(attempt)
                    logger.warning(
                        "Gemini API temporary failure. Retrying in %.1fs (%s/%s): %s",
                        delay_seconds,
                        attempt + 2,
                        _GEMINI_MAX_RETRIES,
                        error.message,
                    )
                    _emit_progress(
                        progress_callback,
                        f"CLIP_SELECTION | Gemini is busy right now. Retrying in {delay_seconds:.0f}s ({attempt + 2}/{_GEMINI_MAX_RETRIES})...",
                    )
                    time.sleep(delay_seconds)
                    continue
                raise last_error
    finally:
        client.close()

    if not text:
        raise last_error or RuntimeError("Gemini returned an empty response after all retries.")

    return text
