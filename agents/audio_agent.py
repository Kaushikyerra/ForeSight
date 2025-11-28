# audio_agent.py
"""
Audio agent (AssemblyAI) - clean, focused module for uploading and transcribing audio files.

Usage:
    from audio_agent import analyze_audio_file

    result = analyze_audio_file("path/to/audio.mp3")
    print(result["transcript"])  # full text
    print(result["utterances"])  # list of speaker utterances (if provided)

Notes:
- Provide your AssemblyAI API key via the ASSEMBLYAI_API_KEY env var, OR pass it as api_key argument.
- This module does NOT call any LLMs or Gemini — it only handles transcription (per your request).
"""

import os
import time
import requests
from typing import Optional, Dict, Any

# Constants
ASSEMBLYAI_UPLOAD_URL = "https://api.assemblyai.com/v2/upload"
ASSEMBLYAI_TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"

# Default timeout/polling configuration (can be tuned)
POLL_INTERVAL_SECONDS = 3.0
POLL_TIMEOUT_SECONDS = 300.0  # 5 minutes


class AssemblyAIError(RuntimeError):
    """Raised when AssemblyAI returns an error or transcription fails."""


def _get_api_key(provided_key: Optional[str]) -> str:
    """
    Resolve the AssemblyAI API key from parameter or environment.
    Raises ValueError if not found.
    """
    if provided_key:
        return provided_key
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        # Optional: fallback to a literal key only if you understand the risks.
        # Remove the fallback in production.
        raise ValueError(
            "AssemblyAI API key not found. Set the ASSEMBLYAI_API_KEY environment variable "
            "or pass api_key argument to analyze_audio_file()."
        )
    return key


def upload_file_to_assemblyai(file_path: str, api_key: str, chunk_size: int = 16 * 1024) -> str:
    """
    Upload a local file (binary) to AssemblyAI and return the upload URL.

    FIXED: Using explicit Content-Type and letting 'requests' manage streaming for stability.
    """
    # --- FIXED CODE BLOCK ---
    headers = {
        "authorization": api_key, 
        "Content-Type": "application/octet-stream" # Use standard binary content type
    }
    with open(file_path, "rb") as f:
        # Send the file stream directly, removing manual 'transfer-encoding' which causes 400 errors.
        response = requests.post(ASSEMBLYAI_UPLOAD_URL, headers=headers, data=f)
    # --- END FIXED CODE BLOCK ---
    
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        # Try to include JSON error if possible
        payload = {}
        try:
            payload = response.json()
        except Exception:
            payload = {"text": response.text}
        raise AssemblyAIError(f"Upload failed: {payload}")
    data = response.json()
    upload_url = data.get("upload_url")
    if not upload_url:
        raise AssemblyAIError(f"Upload response missing upload_url: {data}")
    return upload_url


def request_transcription_from_assemblyai(
    audio_url: str,
    api_key: str,
    features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Start a transcription job and return the initial transcript object (contains id).
    features is a dict with keys like 'sentiment_analysis', 'speaker_labels', 'punctuate', 'format_text', ...
    """
    headers = {"authorization": api_key, "content-type": "application/json"}
    payload = {"audio_url": audio_url}
    if features:
        payload.update(features)

    response = requests.post(ASSEMBLYAI_TRANSCRIPT_URL, headers=headers, json=payload)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        # include JSON body if possible
        try:
            err = response.json()
        except Exception:
            err = {"text": response.text}
        raise AssemblyAIError(f"Transcription request failed: {err}")
    return response.json()


def poll_transcript_status(transcript_id: str, api_key: str, timeout: float = POLL_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """
    Poll the transcript endpoint until status == 'completed' or 'failed'.
    Returns the final transcript JSON on success or raises AssemblyAIError on failure/timeout.
    """
    poll_url = f"{ASSEMBLYAI_TRANSCRIPT_URL}/{transcript_id}"
    headers = {"authorization": api_key}
    start = time.time()

    while True:
        if (time.time() - start) > timeout:
            raise AssemblyAIError(f"Polling timed out after {timeout} seconds for transcript id {transcript_id}.")

        resp = requests.get(poll_url, headers=headers)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            try:
                err = resp.json()
            except Exception:
                err = {"text": resp.text}
            raise AssemblyAIError(f"Polling error: {err}")

        data = resp.json()
        status = data.get("status")
        if status == "completed":
            return data
        if status == "failed":
            raise AssemblyAIError(f"Transcription failed: {data.get('error', 'Unknown error')}")
        # still processing: sleep then loop
        time.sleep(POLL_INTERVAL_SECONDS)


def normalize_transcript_output(transcript_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert AssemblyAI transcript JSON into a consistent, compact dictionary:
    {
        "text": "...",             # raw text
        "utterances": [...],        # if available: list of {'speaker': n, 'text': "..."}
        "sentiment_analysis_results": [...],  # if available
        
    }
    """
    text = transcript_data.get("text", "")
    utterances = transcript_data.get("utterances") or []
    sentiment_results = transcript_data.get("sentiment_analysis_results") or []
    return {
        "text": text,
        "utterances": utterances,
        "sentiment_analysis_results": sentiment_results,
        "raw": transcript_data
    }


def analyze_audio_file(file_path: str, api_key: Optional[str] = None, features: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Top-level function to upload and transcribe a local audio file.

    - file_path: local path to audio file (mp3, wav, m4a, etc.)
    - api_key: optional AssemblyAI API key (otherwise read from ASSEMBLYAI_API_KEY env var)
    - features: optional dict of AssemblyAI transcript options (e.g. {'sentiment_analysis': True, 'speaker_labels': True, ...})

    Returns a dict:
    {
        "fileName": "audio.mp3",
        "transcript": { text, utterances, sentiment_analysis_results, raw },
        "transcript_id": "...",
        "status": "completed",
    }

    Raises:
        ValueError if the API key is missing,
        AssemblyAIError on upload/transcription failures.
    """
    key = _get_api_key(api_key)

    # Default features if none provided
    features = features or {
        "sentiment_analysis": True,
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }

    # 1) Upload file
    upload_url = upload_file_to_assemblyai(file_path, api_key=key)

    # 2) Request transcription
    transcript_initial = request_transcription_from_assemblyai(upload_url, api_key=key, features=features)
    transcript_id = transcript_initial.get("id")
    if not transcript_id:
        raise AssemblyAIError(f"Transcription request returned no id: {transcript_initial}")

    # 3) Poll until completion
    transcript_final = poll_transcript_status(transcript_id, api_key=key)

    # 4) Normalize and return
    normalized = normalize_transcript_output(transcript_final)
    return {
        "fileName": os.path.basename(file_path),
        "transcript_id": transcript_id,
        "transcript": normalized,
        "status": transcript_final.get("status", "unknown"),
    }


# -----------------------
# Example usage (if run as script)
# -----------------------
# Corrected the syntax for the main execution block
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Transcribe audio using AssemblyAI")
    parser.add_argument("file", help="Path to audio file")
    parser.add_argument("--api-key", help="AssemblyAI API key (optional, uses ASSEMBLYAI_API_KEY env var if omitted)")
    parser.add_argument("--no-sentiment", action="store_true", help="Disable sentiment_analysis to speed up job")
    args = parser.parse_args()

    # Build features
    default_features = {
        "sentiment_analysis": not args.no_sentiment,
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True
    }

    try:
        out = analyze_audio_file(args.file, api_key=args.api_key, features=default_features)
        print("Transcription completed.")
        print("Transcript ID:", out["transcript_id"])
        print("Text (first 400 chars):")
        print(out["transcript"]["text"][:400])
    except Exception as e:
        print("Error:", e)
        raise