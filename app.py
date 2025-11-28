import os
import asyncio
import hashlib
import json
import logging
import uuid
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from typing import Dict, Any

# --- IMPORT DATABASE HELPERS ---
try:
    from database import upload_to_s3, create_case_record
except ImportError:
    upload_to_s3, create_case_record = None, None

# --- 1. LOAD ENV & LOGGING ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- 2. PATH CONFIG ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND_FOLDER = os.path.join(PROJECT_ROOT, 'Frontend')
UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# API Keys
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

# Allowed Extensions
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'mp3', 'wav', 'm4a',
    'mp4', 'mov', 'avi', 'mkv', 'txt', 'pdf', 'docx', 'json'
}

# --- 3. FLASK SETUP ---
app = Flask(__name__, 
            template_folder=FRONTEND_FOLDER, 
            static_folder=FRONTEND_FOLDER, 
            static_url_path='')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# --- 4. AGENT IMPORTS ---
AGENTS_LOADED = True
try:
    from agents.image_deepfake_agent import analyze_image_with_rd_and_gemini
    from agents.audio_agent import analyze_audio_file 
    from agents.doc_misinfo_agent import read_files_from_paths, run_gemini_analysis
    from agents.video_agent import run_video_forensics
    from agents.meta_agent import meta_process
    from agents.blockchain_agent import log_verification_hash
    from agents.rag_agent import ingest_text_to_rag, query_rag

except ImportError as e:
    logger.critical(f"Failed to import agents: {e}")
    AGENTS_LOADED = False

# --- DATA MODELS & HELPERS ---
class AnalysisResponse:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
    def model_dump(self): return self.__dict__

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def create_report_hash(data: dict) -> str:
    data_str = json.dumps(data, sort_keys=True, indent=None)
    return hashlib.sha256(data_str.encode()).hexdigest()

# --- PIPELINE FUNCTIONS ---

async def run_forensight_pipeline_image(file_path: str):
    logger.info(f"Running Image Pipeline: {file_path}")
    if asyncio.iscoroutinefunction(analyze_image_with_rd_and_gemini):
        results = await analyze_image_with_rd_and_gemini(file_path)
    else:
        results = await asyncio.to_thread(analyze_image_with_rd_and_gemini, file_path)
    
    final_report = {
        "verdict": results.get("verdict", "Unknown"),
        "authenticity_score": results.get("authenticity_score", 0.0),
        "details": {"file_type": "Image", "full_analysis": results}
    }
    return await _finalize_report(final_report)

async def run_forensight_pipeline_audio(file_path: str):
    logger.info(f"Running Audio Pipeline: {file_path}")
    if not ASSEMBLYAI_API_KEY: raise ValueError("ASSEMBLYAI_API_KEY missing")
    audio_results = await asyncio.to_thread(analyze_audio_file, file_path, api_key=ASSEMBLYAI_API_KEY)
    
    final_report = {
        "verdict": "Audio Processed", 
        "authenticity_score": 0.0, 
        "details": {
            "file_type": "Audio",
            "transcript_id": audio_results.get("transcript_id"),
            "full_transcript": audio_results.get("transcript", {}).get("text", "")
        }
    }
    return await _finalize_report(final_report)

async def run_forensight_pipeline_video(file_path: str):
    logger.info(f"Running Video Pipeline: {file_path}")
    video_results = await asyncio.to_thread(run_video_forensics, file_path)
    final_report = {
        "verdict": video_results.get("verdict", "Unknown"),
        "authenticity_score": video_results.get("authenticity_score", 0.0),
        "details": {"file_type": "Video", "analysis": video_results.get("visual_analysis", {})}
    }
    return await _finalize_report(final_report)

async def run_forensight_pipeline_document(file_path: str, case_id: str = None):
    logger.info(f"Running Document Pipeline: {file_path}")
    text_content = read_files_from_paths([file_path])
    gemini_data = await asyncio.to_thread(run_gemini_analysis, text_content)
    danger = gemini_data.get("misinformationAnalysis", {}).get("dangerScore", 0)
    
    # Ingest to RAG if available
    if ingest_text_to_rag and case_id:
        try:
            filename = os.path.basename(file_path)
            await asyncio.to_thread(ingest_text_to_rag, case_id, text_content, filename)
            logger.info(f" Document ingested to RAG: {filename}")
        except Exception as e:
            logger.warning(f" RAG ingestion failed: {e}")
    
    final_report = {
        "verdict": f"Danger Score: {danger}/100",
        "authenticity_score": (100 - danger) / 100.0,
        "details": {"file_type": "Document", "analysis": gemini_data}
    }
    return await _finalize_report(final_report)

async def _finalize_report(report_data):
    if log_verification_hash:
        try:
            tx = log_verification_hash(f"0x{create_report_hash(report_data)}")
            report_data["blockchain_tx"] = tx
        except Exception: pass
    return AnalysisResponse(**report_data).model_dump()

# --- ROUTES ---

@app.route('/', methods=['GET'])
def index():
    return render_template('dashboard.html')

@app.route('/verify', methods=['POST'])
async def verify_file():
    if not AGENTS_LOADED: return jsonify({"error": "Agents failed to load"}), 500
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if not allowed_file(file.filename): return jsonify({"error": "Invalid file"}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    
    try:
        # 1. Run Forensic Analysis
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in {'png', 'jpg', 'jpeg', 'gif'}: report = await run_forensight_pipeline_image(file_path)
        elif ext in {'mp3', 'wav', 'm4a'}: report = await run_forensight_pipeline_audio(file_path)
        elif ext in {'mp4', 'mov', 'avi', 'mkv'}: report = await run_forensight_pipeline_video(file_path)
        elif ext in {'txt', 'pdf', 'docx', 'json'}: 
            # Generate case_id early for RAG ingestion
            case_id = str(uuid.uuid4())
            report = await run_forensight_pipeline_document(file_path, case_id)
        else: return jsonify({"error": "Unsupported type"}), 400
        
        # 2. Upload to S3 and Save to Database
        if upload_to_s3 and create_case_record:
            # Use the case_id generated earlier for document files, generate new one for others
            if ext in {'txt', 'pdf', 'docx', 'json'}:
                s3_url = upload_to_s3(file_path, filename, folder=case_id)
            else:
                case_id = str(uuid.uuid4())
                s3_url = upload_to_s3(file_path, filename, folder=case_id)
            
            # Metadata block
            file_meta = {
                "filename": filename, 
                "s3_url": s3_url, 
                "local_path": file_path
            }
            
            # Save everything to DB
            await create_case_record(case_id, file_meta, report)
            
            # Append ID/URL to response so frontend knows
            report["case_id"] = case_id
            report["s3_url"] = s3_url

        return jsonify({"status": "success", "report": report})

    except Exception as e:
        logger.error(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/verify_with_instructions', methods=['POST'])
async def meta_verify():
    if 'files' not in request.files: return jsonify({"error": "No files"}), 400
    files = request.files.getlist('files')
    
    # Generate Session ID (This acts as the Case ID folder)
    session_id = str(uuid.uuid4())
    session_dir = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    saved_paths = []
    file_metadata_list = []

    # 1. Process Files
    for f in files:
        filename = secure_filename(f.filename)
        path = os.path.join(session_dir, filename)
        f.save(path)
        saved_paths.append(path)
        
        # Upload to S3 (Folder = Session ID)
        s3_link = None
        if upload_to_s3:
            s3_link = upload_to_s3(path, filename, folder=session_id)
        
        file_metadata_list.append({
            "filename": filename,
            "s3_url": s3_link
        })

    try:
        # 2. Run Meta Analysis
        report = await meta_process(session_id, saved_paths, request.form.get('instructions', ""), {"assemblyai": ASSEMBLYAI_API_KEY})
        
        # 3. Save to Database
        if create_case_record:
            await create_case_record(session_id, file_metadata_list, report)

        return jsonify({"status": "success", "meta_report": report})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/rag_query', methods=['POST'])
async def rag_query():
    """Query the RAG system for forensic insights"""
    if not query_rag:
        return jsonify({"error": "RAG system not available"}), 503
    
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "Query required"}), 400
    
    query = data.get('query', '').strip()
    case_id = data.get('case_id', None)
    
    if not query:
        return jsonify({"error": "Query cannot be empty"}), 400
    
    try:
        result = await asyncio.to_thread(query_rag, query, case_id)
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        logger.error(f"RAG query error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print(f"--- ForenSIGHT Server Starting ---")
    app.run(debug=True, host='0.0.0.0', port=8000)