# database.py
import os
import logging
from datetime import datetime
from dotenv import load_dotenv

# Try to import DB libraries, handle if missing
try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
    from motor.motor_asyncio import AsyncIOMotorClient
    DB_LIBS_AVAILABLE = True
except ImportError:
    DB_LIBS_AVAILABLE = False

load_dotenv()

# Logger configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG ---
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "forensight_db")

# --- INITIALIZATION ---
s3_client = None
cases_collection = None

if DB_LIBS_AVAILABLE:
    # 1. Setup S3
    if AWS_ACCESS_KEY and S3_BUCKET_NAME:
        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                region_name=AWS_REGION
            )
            logger.info("✅ S3 Client Initialized")
        except Exception as e:
            logger.warning(f"Failed to init S3: {e}")

    # 2. Setup MongoDB
    if MONGO_URI:
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URI)
            db = mongo_client[MONGO_DB_NAME]
            cases_collection = db["cases"]
            logger.info("✅ MongoDB Connected")
        except Exception as e:
            logger.warning(f"Failed to init MongoDB: {e}")
    else:
        logger.warning("⚠️ MONGO_URI not found in .env. Database features will be disabled.")
else:
    logger.warning("⚠️ boto3 or motor not installed. Database features disabled.")


# --- FUNCTIONS ---

def upload_to_s3(file_path, filename, folder="evidence"):
    """
    Uploads a local file to S3 inside a specific folder and returns a Presigned URL.
    """
    if s3_client is None or not S3_BUCKET_NAME:
        return None
    
    try:
        # Create a "folder" structure in S3 (e.g., session_id/image.jpg)
        object_name = f"{folder}/{filename}"
        
        # Upload the file
        s3_client.upload_file(file_path, S3_BUCKET_NAME, object_name)
        
        # Generate a Presigned URL (Valid for 7 days)
        # This allows the frontend to view the private S3 file
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': object_name},
            ExpiresIn=604800 # 7 days in seconds
        )
        return url
    except Exception as e:
        logger.error(f"S3 Upload Error: {e}")
        return None

async def create_case_record(case_id, file_metadata, analysis_report):
    """
    Creates a full DB record linking the Input File (S3 URL) and Output (Analysis).
    """
    if cases_collection is None:
        return 
        
    try:
        record = {
            "case_id": case_id,
            "timestamp": datetime.utcnow(),
            "status": "completed",
            "files": file_metadata,   # Stores filename & S3 URL
            "output": analysis_report # Stores the AI Forensic Report
        }
        await cases_collection.insert_one(record)
        logger.info(f"✅ Case {case_id} saved to DB.")
    except Exception as e:
        logger.error(f"DB Write Error: {e}")