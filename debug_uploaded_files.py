from api.models import UploadedFile as UploadedFileModel, CustomUser
from api.api import get_signed_url

def run_debug():
    user_email = "22ucc084@lnmiit.ac.in"  # Change as needed
    user = CustomUser.objects.get(email=user_email)

    print("request.user:", user)
    print("request.user.role:", user.role)

    files = (
        UploadedFileModel.objects.all()
        if user.role in ["admin", "faculty"]
        else UploadedFileModel.objects.filter(user=user)
    ).order_by("-uploaded_at")

    print("File count:", files.count())
    print("Files:", [(f.id, f.filename) for f in files])

    result = []

    for f in files:
        signed_url = ""
        try:
            if f.cdn_url:
                print(f"Signing {f.cdn_url}...")
                signed_url = get_signed_url(f.cdn_url) or ""
        except Exception as e:
            print(f"[ERROR] Failed to sign file {f.id} ({f.filename}): {e}")
            import traceback
            print(traceback.format_exc())
            signed_url = ""

        result.append({
            "id": f.id,
            "user": f.user_id,
            "filename": f.filename,
            "size": f.size,
            "uploaded_at": f.uploaded_at,
            "cdn_url": signed_url or "",
            "year": f.year or "",
        })

    print("Final result:", result)

run_debug()
