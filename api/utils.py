from supabase import create_client
from uuid import uuid4
from decouple import config

# Environment variables
SUPABASE_URL = config("SUPABASE_URL")
SUPABASE_KEY = config("SUPABASE_KEY")  # Use service role key if bypassing RLS
BUCKET_NAME = config("SUPABASE_BUCKET")

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_to_supabase(file, filename: str) -> str:
    """
    Uploads a file to Supabase Storage and returns the public URL.
    
    Raises:
        Exception: If the upload fails.
    """
    unique_name = f"{uuid4().hex}_{filename}"
    path = unique_name

    try:
        # Read and reset pointer
        content = file.read()
        file.seek(0)

        # Upload to Supabase Storage
        response = supabase.storage.from_(BUCKET_NAME).upload(
            path, content, {"content-type": file.content_type}
        )

        # DEBUG: Inspect response
        print("Upload response:", response)

        # If error object exists
        if hasattr(response, "error") and response.error:
            raise Exception(f"Upload failed: {response.error.message}")

        # If successful
        return path

    except Exception as e:
        # Extra debugging info
        print("Exception during upload:", e)
        raise e
