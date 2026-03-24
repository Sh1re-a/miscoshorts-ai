import os
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from app.paths import ENV_FILE

load_dotenv(dotenv_path=ENV_FILE)


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

    prompt = f"""
    Act as a professional video editor. Analyze this transcript with timestamps.
    Identify the {clip_count} strongest non-overlapping segments for viral Shorts.
    Each segment should be 25-60 seconds long, punchy, and distinct from the others.

    Transcript:
    {timed_text}

    Reply ONLY with this exact format (no extra explanations):
    CLIP 1
    TITLE: [Write a strong hook title here]
    START: [Only the second number, for example 120.5]
    END: [Only the second number, for example 155.0]
    REASON: [Short reason]

    CLIP 2
    TITLE: [Write a strong hook title here]
    START: [Only the second number, for example 220.0]
    END: [Only the second number, for example 260.0]
    REASON: [Short reason]

    Continue until CLIP {clip_count}.
    """

    client = genai.Client(api_key=get_gemini_api_key(api_key))
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="text/plain",
            ),
        )
    except errors.APIError as error:
        raise RuntimeError(f"Gemini request failed: {error.message}") from error
    finally:
        client.close()

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    return text