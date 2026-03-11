import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()


def get_gemini_api_key(api_key=None):
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY was not found. Add your key to a .env file or enter it when the program starts."
        )
    return key


def find_viral_clip(whisper_segments, api_key=None):
    print("✨ Asking Gemini with timestamps...")

    genai.configure(api_key=get_gemini_api_key(api_key))

    timed_text = ""
    for segment in whisper_segments:
        timed_text += f"[{segment['start']:.1f}s] {segment['text']}\n"

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={"temperature": 0.7}
    )

    prompt = f"""
    Act as a professional video editor. Analyze this transcript with timestamps.
    Identify THE BEST segment for a viral Short (30-60 seconds).

    Transcript:
    {timed_text}

    Reply ONLY with this exact format (no extra explanations):
    TITLE: [Write a strong hook title here]
    START: [Only the second number, for example 120.5]
    END: [Only the second number, for example 155.0]
    REASON: [Short reason]
    """

    response = model.generate_content(prompt)
    return response.text