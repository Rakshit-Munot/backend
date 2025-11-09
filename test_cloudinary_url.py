#!/usr/bin/env python
"""Test Cloudinary URL construction for bills."""

# Simulate the URL construction logic
test_files = [
    {"name": "invoice.pdf", "format": "pdf", "resource_type": "raw"},
    {"name": "receipt 2024.jpg", "format": "jpg", "resource_type": "image"},
    {"name": "scan-document.png", "format": "png", "resource_type": "image"},
    {"name": "bill #123.pdf", "format": "pdf", "resource_type": "raw"},
]

import uuid

for file_info in test_files:
    original_name = file_info["name"]
    file_format = file_info["format"]
    resource_type = file_info["resource_type"]
    
    # Extract extension
    file_ext = ""
    if "." in original_name:
        file_ext = original_name.rsplit(".", 1)[-1].lower()
    
    # Generate unique filename
    unique_id = uuid.uuid4().hex[:12]
    clean_name = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
    clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in clean_name)[:50]
    
    # Build public_id
    public_id_base = f"bills/{clean_name}_{unique_id}"
    
    # Simulate Cloudinary response
    base_url = f"https://res.cloudinary.com/dfghzbmyz/{resource_type}/upload/{public_id_base}"
    
    # Add extension to URL
    if file_format and not base_url.lower().endswith(f".{file_format}"):
        if "?" in base_url:
            file_url = base_url.replace("?", f".{file_format}?")
        else:
            file_url = f"{base_url}.{file_format}"
    else:
        file_url = base_url
    
    print(f"Original: {original_name}")
    print(f"  Clean name: {clean_name}")
    print(f"  Public ID: {public_id_base}")
    print(f"  URL: {file_url}")
    print(f"  → Opens in browser as {resource_type}/{file_format}")
    print()
