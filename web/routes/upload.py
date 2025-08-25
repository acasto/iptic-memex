from __future__ import annotations

from typing import Any, Dict, Optional
import os

from starlette.requests import Request
from starlette.responses import JSONResponse


async def api_upload(request: Request):
    """Handle file uploads to a temp dir under web/uploads.

    Matches the previous inline handler in web/app.py for backward compatibility.
    """
    try:
        form = await request.form()
    except Exception as e:
        msg = str(e) or "Invalid form"
        if "python-multipart" in msg.lower():
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "recoverable": True,
                        "message": "Missing dependency 'python-multipart' for file uploads.",
                    },
                },
                status_code=400,
            )
        return JSONResponse(
            {"ok": False, "error": {"recoverable": True, "message": msg}},
            status_code=400,
        )

    files = form.getlist("files") if hasattr(form, "getlist") else []
    if not files:
        return JSONResponse(
            {
                "ok": False,
                "error": {"recoverable": True, "message": "No files in form"},
            },
            status_code=400,
        )

    # Save uploads to a temp dir under web/uploads
    upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
    upload_dir = os.path.abspath(upload_dir)
    os.makedirs(upload_dir, exist_ok=True)
    saved = []
    for up in files:
        try:
            filename = getattr(up, "filename", "upload.bin")
            # Basic sanitization
            safe = "".join(
                ch for ch in filename if ch.isalnum() or ch in ("-", "_", ".", " ")
            ) or "upload.bin"
            # Ensure unique name
            base, ext = os.path.splitext(safe)
            path = os.path.join(upload_dir, safe)
            i = 1
            while os.path.exists(path):
                path = os.path.join(upload_dir, f"{base}_{i}{ext}")
                i += 1
            # Write content
            content = await up.read()  # type: ignore[attr-defined]
            with open(path, "wb") as f:
                f.write(content)
            saved.append({"name": filename, "path": path, "size": len(content)})
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": {"recoverable": True, "message": str(e)}},
                status_code=500,
            )

    return JSONResponse({"ok": True, "files": saved})

