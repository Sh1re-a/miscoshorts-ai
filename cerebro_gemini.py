import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()


def hamta_gemini_api_nyckel(api_nyckel=None):
    nyckel = (api_nyckel or os.getenv("GEMINI_API_KEY", "")).strip()
    if not nyckel:
        raise ValueError(
            "GEMINI_API_KEY hittades inte. Lagg in nyckeln i en .env-fil eller skriv in den nar programmet startar."
        )
    return nyckel


def hitta_viralt_klipp(whisper_segment, api_nyckel=None):
    print("✨ Fragar Gemini med tidsstamplar...")

    genai.configure(api_key=hamta_gemini_api_nyckel(api_nyckel))

    text_med_tider = ""
    for segment in whisper_segment:
        text_med_tider += f"[{segment['start']:.1f}s] {segment['text']}\n"

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={"temperature": 0.7}
    )

    prompt = f"""
    Agera som en professionell videoredigerare. Analysera den har transkriptionen med tidsstamplar.
    Identifiera DET BASTA segmentet for en viral Short (30-60 sekunder).

    Transkription:
    {text_med_tider}

    Svara ENDAST med exakt det har formatet (utan extra forklaringar):
    TITEL: [Skriv en stark och lockande titel har]
    START: [Endast sekundtalet, till exempel 120.5]
    SLUT: [Endast sekundtalet, till exempel 155.0]
    ORSAK: [Kort motivering]
    """

    response = model.generate_content(prompt)
    return response.text