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

from typing import Literal

@app.post("/od-requests")
def create_od_request(
    student_id: int = Form(...),
    teacher_id: int = Form(...),
    reason: str = Form(...),
    from_date: datetime = Form(...),
    to_date: datetime = Form(...),
    proof: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    student = db.get(models.Student, student_id)
    if not student or not db.get(models.Teacher, teacher_id):
        raise HTTPException(404, "Student or teacher not found")
    if to_date < from_date:
        raise HTTPException(400, "to_date must be after from_date")

    proof_url = None
    if proof and proof.filename:
        suffix = os.path.splitext(proof.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(proof.file, tmp)
            tmp_path = tmp.name
        try:
            result = od.upload_to_teacher_onedrive(str(teacher_id), tmp_path, "od", student.name)
        finally:
            os.remove(tmp_path)
        if not result["success"]:
            raise HTTPException(502, result["error"])
        proof_url = result["file_url"]

    obj = models.ODRequest(student_id=student_id, teacher_id=teacher_id, reason=reason,
                           from_date=from_date, to_date=to_date, proof_url=proof_url)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

@app.get("/od-requests")
def list_od_requests(student_id: int | None = None, teacher_id: int | None = None,
                     status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.ODRequest)
    if student_id: q = q.filter_by(student_id=student_id)
    if teacher_id: q = q.filter_by(teacher_id=teacher_id)
    if status: q = q.filter_by(status=status)
    return q.order_by(models.ODRequest.created_at.desc()).all()

class ODDecision(BaseModel):
    status: Literal["approved", "rejected"]

@app.patch("/od-requests/{od_id}")
def decide_od_request(od_id: int, data: ODDecision, db: Session = Depends(get_db)):
    obj = db.get(models.ODRequest, od_id)
    if not obj:
        raise HTTPException(404, "OD request not found")
    obj.status = data.status
    db.commit(); db.refresh(obj)
    return obj
