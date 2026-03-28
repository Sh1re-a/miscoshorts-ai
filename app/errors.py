from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FriendlyError:
    category: str
    summary: str
    hint: str | None = None


def explain_exception(error: Exception) -> FriendlyError:
    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()

    if "gemini api key is required" in lowered or "gemini_api_key was not found" in lowered:
        return FriendlyError(
            category="config",
            summary="Gemini API key is missing.",
            hint="Paste a valid Gemini API key in the app or add GEMINI_API_KEY to .env.",
        )
    if "api key not valid" in lowered or "invalid api key" in lowered:
        return FriendlyError(
            category="api_key",
            summary="The Gemini API key was rejected.",
            hint="Check that the key is correct, active, and has access to the selected Gemini model.",
        )
    if "quota" in lowered or "rate limit" in lowered or "429" in lowered:
        return FriendlyError(
            category="api_quota",
            summary="Gemini is temporarily unavailable for this request.",
            hint="Wait a moment and try again, or check whether the Gemini project has remaining quota.",
        )
    if "ffmpeg" in lowered and ("not installed" in lowered or "not available" in lowered):
        return FriendlyError(
            category="dependency",
            summary="FFmpeg is missing.",
            hint="Install FFmpeg and restart the app. The setup scripts can do this automatically on supported machines.",
        )
    if "speech model setup failed" in lowered or "whisper transcription failed" in lowered or "faster-whisper transcription failed" in lowered:
        return FriendlyError(
            category="speech_model",
            summary="The local speech transcription model could not be prepared or used.",
            hint="Run the launcher again so the Whisper preflight can repair the model cache, then retry the render.",
        )
    if "permission denied" in lowered:
        return FriendlyError(
            category="permissions",
            summary="The app could not read or write one of its local folders.",
            hint="Move the project to a normal writable folder, then run it again.",
        )
    if "disk is full" in lowered or "no space left on device" in lowered:
        return FriendlyError(
            category="disk_space",
            summary="The machine ran out of disk space during processing.",
            hint="Free up disk space, then rerun the setup or render.",
        )
    if "only youtube video urls are supported" in lowered or "youtube url appears to be incomplete" in lowered:
        return FriendlyError(
            category="input",
            summary="The YouTube link is invalid or incomplete.",
            hint="Paste a full YouTube watch/share/shorts URL and try again.",
        )
    if "yt-dlp completed without producing a video file" in lowered:
        return FriendlyError(
            category="download",
            summary="The source video could not be downloaded successfully.",
            hint="Check that the YouTube video is available, public, and not blocked in your region, then try again.",
        )
    if "frontend build finished without creating" in lowered or "frontend not built" in lowered:
        return FriendlyError(
            category="frontend",
            summary="The local dashboard could not be prepared.",
            hint="Re-run the launcher and let it complete the frontend setup, or rebuild the frontend manually.",
        )
    if "subtitle" in lowered and "compatibility" in lowered:
        return FriendlyError(
            category="subtitles",
            summary="Subtitle rendering could not be prepared on this machine.",
            hint="Check the system fonts and rerun the app. The diagnostics log will contain the technical details.",
        )

    return FriendlyError(
        category="unknown",
        summary=message,
        hint="Open the latest log file in .miscoshorts/logs/ for the technical details if this keeps happening.",
    )
