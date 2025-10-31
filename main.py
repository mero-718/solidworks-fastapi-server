from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import json
import os
from email import message_from_bytes
from email.policy import default
from typing import Tuple
import shutil
import tempfile
import asyncio
from client.solidworks import fetch_solidworks_info_and_file

DOTNET_API_BASE = "http://localhost:5000"

app = FastAPI(title="FastAPI ↔ ASP.NET Bridge")

origins = [
    "http://localhost:5000",   # React dev
    "http://127.0.0.1:5000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,      # Frontend domains allowed to call this API
    allow_credentials=True,
    allow_methods=["*"],        # Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],        # Allow all headers
)

# Templates directory
templates = Jinja2Templates(directory="templates")

class AttributeUpdate(BaseModel):
    filePath: str
    attributes: dict

@app.get("/")
def root():
    return {"message": "FastAPI Bridge to ASP.NET API is running 🚀"}

# -----------------------------
#  Route: open upload.html page
# -----------------------------
@app.get("/upload", response_class=HTMLResponse)
async def get_upload_page(request: Request):
    return templates.TemplateResponse("update.html", {"request": request})

@app.get("/api/getattributes")
async def get_attributes(filePath: str = Query(..., description="File name or ID to fetch attributes for")):
    """
    Forward GET request to ASP.NET Web API and return its response.
    Example: /api/getattributes?filePath=12
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{DOTNET_API_BASE}/getattributes", params={"filePath": filePath})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ASP.NET API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

@app.post("/api/updateattributes")
async def update_attributes(
    file: UploadFile = File(...),
    json_data: UploadFile = File(...)
):
    """
    Accept STL file + optional attributes.
    Forward them to the ASP.NET Web API.
    """
    if not (file.filename.lower().endswith(".stl") or file.filename.lower().endswith(".sldprt")):
        raise HTTPException(status_code=400, detail="Only .stl files are allowed")
    try:
        # Read file content
        file_bytes = await file.read()
        json_bytes = await json_data.read()

        # Build multipart form data for ASP.NET API
        files = {
            "file": (file.filename, file_bytes, file.content_type or "application/sla"),
            "data": (json_data.filename, json_bytes, "application/json"),
        }
        
        async with httpx.AsyncClient(timeout=200.0) as client:
            try:
                response = await client.post(f"{DOTNET_API_BASE}/updateattributes", files=files)
                response.raise_for_status()

                # Read response as bytes (STL file content)
                stl_content = response.content

                # Optionally get filename from Content-Disposition header
                content_disposition = response.headers.get("content-disposition")
                if content_disposition and "filename=" in content_disposition:
                    filename = content_disposition.split("filename=")[-1].strip('"')
                else:
                    filename = "output.stl"  # fallback filename

                # Save the STL file locally
                with open(filename, "wb") as f:
                    f.write(stl_content)

                return {
                    "status": "success",
                    "file_name": filename,
                    "file_size": len(stl_content)
                }

            except httpx.RequestError as e:
                return {"status": "error", "message": f"Request failed: {str(e)}"}

            except httpx.HTTPStatusError as e:
                return {"status": "error", "message": f"HTTP error: {e.response.text}"}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ASP.NET API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")
        

@app.post("/api/model")
async def receive_attributes(
    file: UploadFile = File(...)
):
    # Save uploaded file to a temp location to forward to .NET API
    SW_API_URL = "http://localhost:5000/SolidWork/model"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
    try:
        with tmp as out_f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)

        # Call the .NET endpoint and parse multipart/mixed
        try:
            info, saved_file_path = await fetch_solidworks_info_and_file(SW_API_URL, tmp.name)
        except Exception as ex:
            raise HTTPException(status_code=502, detail=f"Error calling SOLIDWORKS API: {ex}")

        # If you want to return the file directly to the client:
        if saved_file_path:
            # Return JSON with metadata and a link (or provide file directly)
            return JSONResponse({"info": info, "exported_file": os.path.basename(saved_file_path)})
        else:
            return JSONResponse({"info": info, "exported_file": None})
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
