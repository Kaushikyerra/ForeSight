import os
import json
import asyncio
import hashlib
import logging
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from google.generativeai.types import GenerationConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "models/gemini-2.5-flash"
if GEMINI_API_KEY: genai.configure(api_key=GEMINI_API_KEY)

try:
    from agents.image_deepfake_agent import analyze_image_with_rd_and_gemini
except ImportError: analyze_image_with_rd_and_gemini = None

try:
    from agents.audio_agent import analyze_audio_file
except ImportError: analyze_audio_file = None

try:
    from agents.doc_misinfo_agent import read_files_from_paths, run_gemini_analysis
except ImportError: read_files_from_paths, run_gemini_analysis = None, None

try:
    from agents.video_agent import run_video_forensics
except ImportError: run_video_forensics = None

try:
    from agents.blockchain_agent import log_verification_hash
except ImportError: log_verification_hash = None

def _make_hash(obj: Any) -> str:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode()).hexdigest()

def _parse_priority_from_instructions(instructions: str) -> List[str]:
    instr = (instructions or "").lower()
    buckets = []
    if any(w in instr for w in ("transcript", "audio")): buckets.append("audio")
    if any(w in instr for w in ("image", "photo", "fake")): buckets.append("images")
    if any(w in instr for w in ("video", "clip", "mp4")): buckets.append("video")
    if any(w in instr for w in ("doc", "file", "text")): buckets.append("documents")
    for t in ("documents", "video", "images", "audio"):
        if t not in buckets: buckets.append(t)
    return buckets

async def _run_pipeline_for_file(file_path: str, ftype: str, api_keys: Dict[str,str]) -> Dict[str,Any]:
    fname = os.path.basename(file_path)
    loop = asyncio.get_event_loop()
    try:
        if ftype == "images":
            if not analyze_image_with_rd_and_gemini: return {"file": fname, "error": "Image Agent missing"}
            if asyncio.iscoroutinefunction(analyze_image_with_rd_and_gemini):
                res = await analyze_image_with_rd_and_gemini(file_path)
            else:
                res = await loop.run_in_executor(None, analyze_image_with_rd_and_gemini, file_path)
            return {"file": fname, "type": "image", "report": res}

        elif ftype == "audio":
            if not analyze_audio_file: return {"file": fname, "error": "Audio Agent missing"}
            key = api_keys.get("assemblyai") or os.getenv("ASSEMBLYAI_API_KEY")
            res = await loop.run_in_executor(None, analyze_audio_file, file_path, key)
            return {"file": fname, "type": "audio", "report": res}
        
        elif ftype == "video":
            if not run_video_forensics: return {"file": fname, "error": "Video Agent missing"}
            res = await loop.run_in_executor(None, run_video_forensics, file_path)
            return {"file": fname, "type": "video", "report": res}

        elif ftype == "documents":
            if not read_files_from_paths: return {"file": fname, "error": "Doc Agent missing"}
            text = read_files_from_paths([file_path])
            res = await loop.run_in_executor(None, run_gemini_analysis, text)
            return {"file": fname, "type": "document", "report": res, "text": text}
        return {"file": fname, "type": "unsupported"}
    except Exception as e:
        logger.error(f"Pipeline failed for {fname}: {e}")
        return {"file": fname, "type": ftype, "error": str(e)}

async def _generate_meta_intelligence(aggregate_text: str, user_instructions: str, file_summaries: List[str]) -> Dict[str, Any]:
    if not GEMINI_API_KEY: return {"final_summary": "AI key missing.", "entities": [], "relations": []}
    
    context = f"""
    You are the 'Meta-Investigator' AI.
    USER INSTRUCTIONS: {user_instructions}
    FILE SUMMARIES: {json.dumps(file_summaries, indent=2)}
    EVIDENCE: {aggregate_text[:25000]}
    """
    model = genai.GenerativeModel("models/gemini-2.5-flash", system_instruction="Return JSON: {final_summary, entities, relations}")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, model.generate_content, context)
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return {"final_summary": response.text[:500], "entities": [], "relations": []}

async def meta_process(session_id: str, file_paths: List[str], user_instructions: str, api_keys: Optional[Dict[str,str]] = None) -> Dict[str,Any]:
    api_keys = api_keys or {}
    ordered_types = _parse_priority_from_instructions(user_instructions)
    buckets = {"images": [], "audio": [], "documents": [], "video": [], "unsupported": []}
    
    for p in file_paths:
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if ext in {"png", "jpg", "jpeg", "gif"}: buckets["images"].append(p)
        elif ext in {"mp4", "mov", "avi", "mkv"}: buckets["video"].append(p)
        elif ext in {"mp3", "wav", "m4a"}: buckets["audio"].append(p)
        elif ext in {"txt", "pdf", "docx"}: buckets["documents"].append(p)
        else: buckets["unsupported"].append(p)

    results = {"session_id": session_id, "results": []}
    aggregate_text_parts = []
    file_summaries = []

    for t in ordered_types:
        for f in buckets.get(t, []):
            item = await _run_pipeline_for_file(f, t, api_keys)
            results["results"].append(item)
            fname = os.path.basename(f)

            if t == "documents" and "text" in item:
                aggregate_text_parts.append(f"--- DOCUMENT: {fname} ---\n{item['text']}\n")
                danger = item.get("report", {}).get("misinformationAnalysis", {}).get("dangerScore", 0)
                file_summaries.append(f"{fname}: DANGER SCORE {danger}/100.")
            elif t == "audio" and "report" in item:
                transcript = item["report"].get("transcript", {}).get("text", "")
                if transcript: aggregate_text_parts.append(f"--- AUDIO: {fname} ---\n{transcript}\n")
            elif t == "video" and "report" in item:
                fake_ratio = item["report"].get("visual_analysis", {}).get("fake_ratio_percent", 0)
                file_summaries.append(f"{fname}: Video Fake Ratio: {fake_ratio}%")

    logger.info("Generating Meta Intelligence...")
    full_text = "\n".join(aggregate_text_parts)
    meta_output = await _generate_meta_intelligence(full_text, user_instructions, file_summaries)
    
    results.update(meta_output)
    proof_hash = _make_hash(results)
    results["proof_hash"] = proof_hash
    if log_verification_hash:
        try: results["blockchain_tx"] = log_verification_hash(f"0x{proof_hash}")
        except Exception as e: results["blockchain_tx"] = {"error": str(e)}

    return results