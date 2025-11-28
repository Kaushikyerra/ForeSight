import cv2
import os
import logging
import math
from agents.image_deepfake_agent import analyze_image_with_rd_and_gemini

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_basic_metadata(video_path):
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {"error": "Could not open video file."}

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0
        cap.release()

        return {
            "duration_sec": round(duration, 2),
            "resolution": f"{width}x{height}",
            "fps": round(fps, 2),
            "total_frames": frame_count
        }
    except Exception as e:
        logger.error(f"Metadata extraction failed: {e}")
        return {"error": str(e)}

def analyze_video_frames(video_path):
    """
    Forensic Timeline Analysis:
    Checks the ENTIRE video duration by sampling 1 frame every 2.0 seconds.
    This ensures coverage of the whole video without browser timeouts.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"error": "Could not open video file"}

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30
    
    # STRATEGY: Analyze 1 frame every 2 seconds
    seconds_per_check = 2.0 
    frame_interval = int(fps * seconds_per_check)
    
    total_analyzed = 0
    fake_frames = 0
    highest_fake_score = 0.0
    frame_idx = 0
    
    temp_img = video_path + "_temp.jpg"
    
    logger.info(f"Starting Full Timeline Scan (Interval: Every {seconds_per_check}s / {frame_interval} frames)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Analyze current frame if it matches interval
        if frame_idx % frame_interval == 0:
            try:
                cv2.imwrite(temp_img, frame)
                
                logger.info(f"Analyzing timestamp {round(frame_idx/fps, 1)}s...")
                
                result = analyze_image_with_rd_and_gemini(temp_img)
                tamper_score = result.get("tamperingPercentage", 0.0)
                
                if tamper_score > highest_fake_score:
                    highest_fake_score = tamper_score
                
                # If score > 60%, mark this specific moment as FAKE
                if tamper_score > 60: 
                    fake_frames += 1
                    logger.warning(f"!!! FAKE DETECTED at {round(frame_idx/fps, 1)}s (Score: {tamper_score}%)")
                
                total_analyzed += 1
            except Exception as e:
                logger.warning(f"Frame analysis failed: {e}")

        frame_idx += 1

    cap.release()
    if os.path.exists(temp_img):
        try: os.remove(temp_img)
        except: pass

    fake_ratio = (fake_frames / total_analyzed * 100) if total_analyzed > 0 else 0

    return {
        "frames_analyzed": total_analyzed,
        "fake_frames_count": fake_frames,
        "fake_ratio_percent": round(fake_ratio, 2),
        "max_fake_score": round(highest_fake_score, 2),
        "analysis_strategy": "Full Timeline (1 frame / 2s)"
    }

def run_video_forensics(video_path):
    meta = extract_basic_metadata(video_path)
    visuals = analyze_video_frames(video_path)
    
    verdict = "Likely Original"
    if visuals.get("fake_frames_count", 0) > 0:
        verdict = "Tampering Detected"
    
    return {
        "verdict": verdict,
        "authenticity_score": (100 - visuals.get("max_fake_score", 0)) / 100.0,
        "metadata": meta,
        "visual_analysis": visuals
    }