import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from app.paths import ENV_FILE

load_dotenv(dotenv_path=ENV_FILE)

# Retry configuration for transient Gemini failures
_GEMINI_MAX_RETRIES = 3
_GEMINI_RETRY_DELAY_S = 2.0
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


def find_viral_clips(whisper_segments, api_key=None, clip_count=3):
    print("✨ Asking Gemini with timestamps...")

    timed_text = ""
    for segment in whisper_segments:
        timed_text += f"[{segment['start']:.1f}s] {segment['text']}\n"

    prompt = f"""Act as an elite video editor specialising in viral short-form vertical content
(TikTok / YouTube Shorts / Reels). Analyse this transcript with timestamps and
identify the {clip_count} strongest non-overlapping segments.

SELECTION CRITERIA (in priority order):
1. STRONG HOOK — The segment MUST open with something immediately attention-grabbing:
   a bold claim, surprising fact, emotional statement, or provocative question.
   If the first sentence is boring, the Short will be skipped.
2. SELF-CONTAINED — Each segment must deliver a complete thought or argument.
   Never cut mid-sentence or mid-argument. The viewer must understand the point
   without having seen the full video.
3. EMOTIONAL ENGAGEMENT — Prefer moments with strong conviction, passion, humour,
   disagreement, or surprise. Avoid calm, monotone, or meandering discussion.
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
• Interview / podcast (two or more people) — pick distinct story beats,
  stand-out quotes, genuine emotion, laughter, or heated disagreement.
  Prefer exchanges where one person delivers a strong statement — avoid back-
  and-forth crosstalk that loses context in 30 seconds.
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

Each segment should be 25–60 seconds long.

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
                # Empty response — retry with higher temperature
                last_error = RuntimeError("Gemini returned an empty response.")
                if attempt < _GEMINI_MAX_RETRIES - 1:
                    print(f"  ⚠️  Gemini returned empty response, retrying ({attempt + 2}/{_GEMINI_MAX_RETRIES})...")
                    time.sleep(_GEMINI_RETRY_DELAY_S)
            except errors.APIError as error:
                last_error = RuntimeError(f"Gemini request failed: {error.message}")
                if attempt < _GEMINI_MAX_RETRIES - 1:
                    print(f"  ⚠️  Gemini API error, retrying ({attempt + 2}/{_GEMINI_MAX_RETRIES})...")
                    time.sleep(_GEMINI_RETRY_DELAY_S * (attempt + 1))
    finally:
        client.close()

    if not text:
        raise last_error or RuntimeError("Gemini returned an empty response after all retries.")

    return text