# agents/doc_misinfo_agent.py

import os
import json
import pypdf
import docx
from typing import Iterable, Dict, List

import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold

# ---------------- ENV & MODEL ----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Use Flash for speed and strong instruction following
GEMINI_MODEL = "models/gemini-2.5-flash"

# ---------- JSON SCHEMA ----------
# Defined exactly as the Dashboard expects it
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "misinformationAnalysis": {
            "type": "OBJECT",
            "properties": {
                "dangerScore": {"type": "NUMBER", "description": "Score 0-100 indicating threat level. 80+ for crimes."},
                "flags": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "claim": {"type": "STRING"},
                            "reasoning": {"type": "STRING"},
                        },
                    },
                },
                "explanation": {"type": "STRING"},
            },
        },
        "summary": {"type": "STRING"},
        "toneAnalysis": {
            "type": "OBJECT",
            "properties": {"detectedTone": {"type": "STRING"}},
        },
        "contentAnalysis": {
            "type": "OBJECT",
            "properties": {
                "sensitiveInfo": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "type": {"type": "STRING"},
                            "text": {"type": "STRING"},
                        },
                    },
                },
                "inappropriateContent": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
            },
        },
        "keywordDetection": {
            "type": "OBJECT",
            "properties": {
                "keywordsFound": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "keyword": {"type": "STRING"},
                            "context": {"type": "STRING"},
                        },
                    },
                },
            },
        },
        "factChecking": {
            "type": "OBJECT",
            "properties": {
                "claims": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "claim": {"type": "STRING"},
                            "verification": {"type": "STRING"},
                            "source": {"type": "STRING"},
                        },
                    },
                },
            },
        },
        "finalReport": {
            "type": "OBJECT",
            "properties": {
                "findings": {"type": "STRING"},
                "recommendations": {"type": "STRING"},
            },
        },
    },
}

# ---------- FILE READERS (Unchanged) ----------

def read_txt(filepath_or_buffer):
    if hasattr(filepath_or_buffer, "read"):
        return filepath_or_buffer.read().decode("utf-8", errors="ignore")
    else:
        with open(filepath_or_buffer, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

def read_pdf(filepath_or_buffer):
    try:
        reader = pypdf.PdfReader(filepath_or_buffer)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return text
    except Exception as e:
        print(f"[!] Error reading PDF: {e}")
        return ""

def read_docx(filepath_or_buffer):
    try:
        doc = docx.Document(filepath_or_buffer)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text
    except Exception as e:
        print(f"[!] Error reading DOCX: {e}")
        return ""

def read_files_from_paths(filepaths: Iterable[str]) -> str:
    full_text = ""
    paths_list = list(filepaths)
    print(f"[*] Verification Agent: Reading {len(paths_list)} file(s)...")

    for filepath in paths_list:
        try:
            if filepath.endswith(".txt"): content = read_txt(filepath)
            elif filepath.endswith(".pdf"): content = read_pdf(filepath)
            elif filepath.endswith(".docx"): content = read_docx(filepath)
            else: continue
            
            # Header formatting to help AI distinguish files
            full_text += f"\n--- START OF FILE: {os.path.basename(filepath)} ---\n"
            full_text += content
            full_text += f"\n--- END OF FILE: {os.path.basename(filepath)} ---\n"
            
        except Exception as e:
            print(f"  [!] Failed to process {filepath}: {e}")
    return full_text


# ---------- GEMINI ANALYSIS (SINGLE STEP FIX) ----------

def _init_gemini():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    genai.configure(api_key=GEMINI_API_KEY)

def run_gemini_analysis(text_content: str) -> Dict:
    """
    Analyzes text for forensic threats using a SINGLE structured output call.
    This guarantees the Danger Score is populated correctly.
    """
    _init_gemini()

    if not text_content.strip():
        # Return safe empty structure
        return {
            "misinformationAnalysis": {"dangerScore": 0, "flags": [], "explanation": "No text content found."},
            "summary": "Empty file.",
            "finalReport": {"findings": "No content.", "recommendations": "Check file input."}
        }

    print("[*] Verification Agent: Running Forensic Analysis (Structured Mode)...")

    system_prompt = """
    You are an expert Digital Forensic Investigator analyzing chat logs and documents.
    
    YOUR TASK: Detect criminal intent, social engineering, fraud, coercion, or security threats.
    
    SCORING RULES (misinformationAnalysis.dangerScore):
    - 0-20:  Normal conversation.
    - 21-50: Suspicious or high-pressure.
    - 51-80: Clear Scam / Threat / Harassment.
    - 81-100: Immediate Danger / Criminal Conspiracy / Clandestine Operation.

    CRITICAL: You MUST popuate the 'dangerScore' with a number based on the evidence.
    
    Analyze the provided text and output STRICT JSON matching the schema.
    """

    # We use a single generation step with 'response_schema' enforced
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=system_prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.0  # Zero temperature for deterministic scoring
        )
    )

    try:
        response = model.generate_content(
            text_content,
            safety_settings={HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE},
        )
        
        # Parse the response
        result_json = json.loads(response.text)
        
        # Sanity Check: Ensure dangerScore exists
        score = result_json.get("misinformationAnalysis", {}).get("dangerScore", 0)
        print(f"[*] Analysis Complete. Calculated Danger Score: {score}")
        
        return result_json

    except Exception as e:
        print(f"  [!] Analysis Failed: {e}")
        # Robust Fallback
        return {
            "misinformationAnalysis": {
                "dangerScore": 50, # Default to medium alert on error so user checks it
                "flags": [{"claim": "Analysis Error", "reasoning": str(e)}], 
                "explanation": "AI Analysis failed to process this file."
            },
            "summary": "Error during analysis.",
            "finalReport": {"findings": "System Error.", "recommendations": "Retry analysis."}
        }