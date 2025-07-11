from supabase import create_client
from uuid import uuid4
from decouple import config

SUPABASE_URL = config("SUPABASE_URL")
SUPABASE_KEY = config("SUPABASE_KEY")
BUCKET_NAME = config("SUPABASE_BUCKET")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_to_supabase(file, filename: str) -> str:
    unique_name = f"{uuid4().hex}_{filename}"
    path = f"{unique_name}"

    # Supabase expects a byte stream or path-like object
    content = file.read()  # Read content of InMemoryUploadedFile/File
    file.seek(0)  # Rewind for further use if needed

    response = supabase.storage.from_(BUCKET_NAME).upload(
        path, content, {"content-type": file.content_type}
    )

    if not response or response.get("error"):
        raise Exception(f"Upload to Supabase failed: {response.get('error', 'Unknown error')}")

    # Return public URL (assuming public bucket or policy allows access)
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{path}"
