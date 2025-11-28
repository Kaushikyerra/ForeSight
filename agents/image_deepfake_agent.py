# agents/image_deepfake_agent.py

import os
import time
from urllib.parse import urlparse, parse_qs
import requests
from requests.exceptions import Timeout, RequestException
import google.generativeai as genai

# --- REMOVED fastapi import to fix Pydantic crash ---
class HTTPException(Exception):
    """Simple stub to replace FastAPI's exception"""
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

# ---------------- CONFIG ----------------
# (Keep the rest of your file exactly the same below this line)
RD_API_KEY = os.getenv("RD_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not RD_API_KEY:
    raise RuntimeError("RD_API_KEY is not set. Add RD_API_KEY to your .env file.")
# ... rest of the code ...

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set. Add GEMINI_API_KEY to your .env file.")

genai.configure(api_key=GEMINI_API_KEY)

# 🚀 Only Gemini 2.5 Flash — as requested
GEMINI_MODEL = "models/gemini-2.5-flash"

PRESIGNED_ENDPOINT = "https://api.prd.realitydefender.xyz/api/files/aws-presigned"
MEDIA_DETAIL_ENDPOINT = "https://api.prd.realitydefender.xyz/api/media/users/{request_id}"

POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 480  # 8 minutes max


# ---------------- REALITY DEFENDER HELPERS ----------------

def request_presigned_url(file_name: str):
    headers = {"X-API-KEY": RD_API_KEY, "Content-Type": "application/json"}
    payload = {"fileName": file_name}

    # ⬆ timeout increased to 60 seconds
    resp = requests.post(PRESIGNED_ENDPOINT, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()

    data = resp.json()
    response_block = data.get("response") or {}

    signed_url = (
        response_block.get("signedUrl")
        or data.get("signedUrl")
        or data.get("url")
    )
    request_id = (
        data.get("requestId")
        or response_block.get("requestId")
        or data.get("mediaId")
    )

    if not signed_url:
        raise HTTPException(500, "Failed to obtain presigned URL from RD.")

    return signed_url, request_id


def upload_file_to_signed_url(signed_url: str, file_path: str):
    # timeout already 60 which is okay
    with open(file_path, "rb") as f:
        resp = requests.put(signed_url, data=f, timeout=60)
    if resp.status_code not in [200, 201, 202, 204]:
        raise HTTPException(500, f"Upload failed {resp.status_code}: {resp.text[:200]}")     


def extract_request_id_from_url(url: str):
    qs = parse_qs(urlparse(url).query)
    return qs.get("x-amz-meta-requestid", [None])[0]


def get_rd_result(request_id: str):
    headers = {"X-API-KEY": RD_API_KEY, "Content-Type": "application/json"}
    url = MEDIA_DETAIL_ENDPOINT.format(request_id=request_id)

    start = time.time()
    while True:
        # ⬆ timeout increased to 60 seconds
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 404:
            # Not ready yet
            time.sleep(POLL_INTERVAL_SECONDS)
            # Check global timeout too
            if time.time() - start > MAX_WAIT_SECONDS:
                raise HTTPException(504, "RD analysis timeout.")
            continue

        resp.raise_for_status()
        rd_data = resp.json()

        status = (
            rd_data.get("resultsSummary", {}).get("status")
            or rd_data.get("overallStatus")
        )

        # Done
        if status not in ["ANALYZING", "PROCESSING", "QUEUED", None]:
            return rd_data

        # Timeout
        if time.time() - start > MAX_WAIT_SECONDS:
            raise HTTPException(504, "RD analysis timeout.")

        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------- GEMINI HELPERS ----------------

def get_mime_type(file_path: str) -> str:
    ext = file_path.lower().split(".")[-1]
    return "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"


def generate_explanation(image_path: str, tamper_pct: float) -> str:
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    mime_type = get_mime_type(image_path)

    prompt = f"""
You are a deepfake detection expert.

The tamperingPercentage represents the estimated probability this media is manipulated (0 = no tampering, 100 = highly manipulated).

The tamperingPercentage is: {tamper_pct}%

Write a concise but detailed 5–7 sentence report for a regular person:
- Clearly mention the tampering percentage as part of the analysis
- If it looks real → explain WHY visually (texture, lighting consistency, edges, etc.)
- If manipulated → explain WHAT looks fake or mismatched
- Do NOT mention Reality Defender or Gemini
- Do NOT return JSON, only natural English text
"""

    model = genai.GenerativeModel(GEMINI_MODEL)
    resp = model.generate_content(
        [prompt, {"mime_type": mime_type, "data": image_bytes}]
    )

    return (resp.text or "No explanation available.").strip()


# ---------------- PUBLIC FUNCTION FOR ORCHESTRATOR ----------------

def analyze_image_with_rd_and_gemini(file_path: str) -> dict:
    """
    Core image deepfake analysis used by the main Forensight pipeline.

    Returns a dict with:
    - verdict
    - authenticity_score (0–1)
    - tamperingPercentage (0–100)
    - explanation (Gemini text report)
    - rd_raw (raw RD JSON if available)
    """
    original_name = os.path.basename(file_path)

    try:
        # 1) Ask RD for a signed URL
        signed_url, request_id = request_presigned_url(original_name)

        # 2) Upload the local file to RD
        upload_file_to_signed_url(signed_url, file_path)

        # 3) If request_id missing, try to pull it from signed URL query params
        if not request_id:
            request_id = extract_request_id_from_url(signed_url)
        if not request_id:
            raise HTTPException(500, "Request ID missing from RD response.")

        # 4) Poll RD until the analysis is completed
        rd_data = get_rd_result(request_id)

        # Reality Defender usually puts final score in resultsSummary.metadata.finalScore
        metadata = rd_data.get("resultsSummary", {}).get("metadata", {}) or {}
        tamper_pct = float(metadata.get("finalScore", 0.0))

        # 5) Gemini explanation for the same image
        explanation = generate_explanation(file_path, tamper_pct)

        # 6) Convert tampering% → authenticity score 0–1
        authenticity_score = max(0.0, min(1.0, (100.0 - tamper_pct) / 100.0))

        # 7) Human-readable verdict
        if tamper_pct < 25:
            verdict = "Likely Original"
        elif tamper_pct < 60:
            verdict = "Possibly Manipulated"
        else:
            verdict = "Likely Deepfake / Manipulated"

        return {
            "verdict": verdict,
            "authenticity_score": round(authenticity_score, 2),
            "tamperingPercentage": tamper_pct,
            "explanation": explanation,
            "rd_raw": rd_data,
        }

    except Timeout:
        # Clean, friendly timeout response (no crash)
        return {
            "verdict": "Unknown",
            "authenticity_score": 0.0,
            "tamperingPercentage": 0.0,
            "explanation": "Image analysis service (Reality Defender) timed out. Please retry after some time.",
            "rd_raw": {"error": "RD timeout"},
        }

    except HTTPException as e:
        # Convert to a normal dict so orchestrator doesn’t hard-crash
        return {
            "verdict": "Error",
            "authenticity_score": 0.0,
            "tamperingPercentage": 0.0,
            "explanation": f"Image analysis failed: {e.detail}",
            "rd_raw": {"error": str(e)},
        }

    except RequestException as e:
        # Any other requests-related issue (DNS, connection, etc.)
        return {
            "verdict": "Error",
            "authenticity_score": 0.0,
            "tamperingPercentage": 0.0,
            "explanation": f"Network error while calling Reality Defender: {e}",
            "rd_raw": {"error": str(e)},
        }

    except Exception as e:
        # Catch-all safety
        return {
            "verdict": "Error",
            "authenticity_score": 0.0,
            "tamperingPercentage": 0.0,
            "explanation": f"Unexpected error in image analysis: {e}",
            "rd_raw": {"error": str(e)},
        }
