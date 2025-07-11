from supabase import create_client
from uuid import uuid4
from decouple import config

SUPABASE_URL = config("SUPABASE_URL")
SUPABASE_KEY = config("SUPABASE_KEY")  # Use service role key if you want to bypass RLS
BUCKET_NAME = config("SUPABASE_BUCKET")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_to_supabase(file, filename: str) -> str:
    unique_name = f"{uuid4().hex}_{filename}"
    path = f"{unique_name}"

    # Read content from the Django InMemoryUploadedFile
    content = file.read()
    file.seek(0)  # Reset pointer in case you need to use file again later

    # Upload file to Supabase Storage
    response = supabase.storage.from_(BUCKET_NAME).upload(
        path, content, {"content-type": file.content_type}
    )

    # Check for errors
    if response.get("error"):
        raise Exception(f"Upload to Supabase failed: {response['error']['message']}")

    # Return public URL (only works if bucket is public or access policy allows it)
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{path}"
