import google.generativeai as genai
import os

GEMINI_API_KEY = "API_KEY_AQUI"
genai.configure(api_key=GEMINI_API_KEY)

def encontrar_clip_viral(segmentos_whisper):
    print("✨ Consultando a Gemini (con timestamps)...")

    texto_con_tiempos = ""
    for seg in segmentos_whisper:
        texto_con_tiempos += f"[{seg['start']:.1f}s] {seg['text']}\n"

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={"temperature": 0.7}
    )

    prompt = f"""
    Actúa como editor de video profesional. Analiza esta transcripción timestamped.
    Identifica EL MEJOR segmento para un Short viral (30-60 seg).

    Transcripción:
    {texto_con_tiempos}

    Responde SOLO con este formato exacto (sin explicaciones extra):
    TITULO: [Escribe un título gancho aquí]
    INICIO: [Solo el número del segundo, ej: 120.5]
    FIN: [Solo el número del segundo, ej: 155.0]
    RAZON: [Breve motivo]
    """

    response = model.generate_content(prompt)
    return response.text