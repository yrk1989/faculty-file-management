# main.py
import os, shutil, tempfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse
import onedrive_service as od   # your teammate's file, renamed

app = FastAPI()

@app.get("/teachers/{teacher_id}/onedrive/connect")
def connect_onedrive(teacher_id: str):
    # later: check the logged-in user IS this teacher
    return {"login_url": od.get_teacher_login_url(teacher_id)}

@app.get("/callback")   # must match REDIRECT_URI in the module
def callback(code: str = None, state: str = None, error: str = None):
    if error or not code or not state:
        raise HTTPException(400, "OneDrive connection failed")
    result = od.save_teacher_token(state, code)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return RedirectResponse("http://localhost:3000/teacher/dashboard")  # frontend URL

@app.post("/submissions")
def submit_assignment(
    assignment_id: int = Form(...),
    student_id: int = Form(...),
    file: UploadFile = File(...),
):
    # 1. look up assignment -> teacher_id, and student -> name (from your DB)
    teacher_id, student_name = "1", "Arun"   # placeholder

    # 2. save to a temp file
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    # 3. upload, then always clean up
    try:
        result = od.upload_to_teacher_onedrive(
            teacher_id, tmp_path, "assignment", student_name
        )
    finally:
        os.remove(tmp_path)

    if not result["success"]:
        raise HTTPException(502, result["error"])

    # 4. save result["file_url"] in the Submission table here
    return {"status": "submitted", "file_url": result["file_url"]}
