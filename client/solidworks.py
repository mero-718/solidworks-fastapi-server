import os
import json
import tempfile
from typing import Tuple, Optional
import httpx
from email import policy
from email.parser import BytesParser

async def fetch_solidworks_info_and_file(sw_api_url: str, upload_path: str) -> Tuple[dict, Optional[str]]:
    """
    POSTs the local file at upload_path to sw_api_url, parses the multipart/mixed response,
    returns (info_dict, saved_file_path_or_None).

    Requires: httpx (async). Uses stdlib email parser to decode multipart/mixed.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(upload_path, "rb") as f:
            files = {"file": (os.path.basename(upload_path), f, "application/octet-stream")}
            resp = await client.post(sw_api_url, files=files)

        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")

        if not content_type.lower().startswith("multipart/"):
            try:
                return resp.json(), None
            except Exception:
                raise RuntimeError("Unexpected non-multipart response and not JSON")

        body = await resp.aread()

    pseudo = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    msg = BytesParser(policy=policy.default).parsebytes(pseudo)

    info_dict = {}

    # Project-local tmp directory
    project_tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
    os.makedirs(project_tmp_dir, exist_ok=True)

    saved_file_path = None

    for part in msg.iter_parts():
        ctype = part.get_content_type()
        filename = part.get_filename()
        payload_bytes = part.get_payload(decode=True) or b""

        if ctype == "application/json" or (filename and filename.lower().endswith(".json")):
            info_dict = json.loads(payload_bytes.decode("utf-8"))
        else:
            # Save file inside project tmp folder
            fname = filename or "output.stl"
            saved_file_path = os.path.join(project_tmp_dir, fname)
            with open(saved_file_path, "wb") as f:
                f.write(payload_bytes)

    return info_dict, saved_file_path