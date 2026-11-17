import os
import shutil
import tempfile
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import onedrive_service as od
import storage

app = FastAPI(title="Faculty Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Request shapes ----------
class TeacherIn(BaseModel):
    name: str
    email: str
    department: str | None = None


class StudentIn(BaseModel):
    register_number: str
    name: str
    email: str | None = None
    department: str | None = None
    year: int | None = None


class AssignmentIn(BaseModel):
    teacher_id: int
    title: str
    description: str | None = None
    due_date: datetime | None = None


class ODDecision(BaseModel):
    status: Literal["approved", "rejected"]


# ---------- Helpers ----------
def upload_via_temp(file: UploadFile, teacher_id: int, doc_type: str, student_name: str):
    """Save the upload to a temp file, send it to the teacher's OneDrive, clean up."""
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        result = od.upload_to_teacher_onedrive(str(teacher_id), tmp_path, doc_type, student_name)
    finally:
        os.remove(tmp_path)
    if not result["success"]:
        raise HTTPException(502, result["error"])
    return result["file_url"]


# ---------- OneDrive connection ----------
@app.get("/teachers/{teacher_id}/onedrive/connect")
def connect_onedrive(teacher_id: int):
    if not storage.get("teachers", teacher_id):
        raise HTTPException(404, "Teacher not found")
    return {"login_url": od.get_teacher_login_url(str(teacher_id))}


@app.get("/callback")   # must match REDIRECT_URI in onedrive_service.py
def callback(code: str = None, state: str = None, error: str = None):
    if error or not code or not state:
        raise HTTPException(400, "OneDrive connection failed")
    result = od.save_teacher_token(state, code)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return RedirectResponse("http://localhost:3000/teacher/dashboard")


# ---------- Teachers ----------
@app.post("/teachers")
def create_teacher(data: TeacherIn):
    if any(t["email"] == data.email for t in storage.load("teachers")):
        raise HTTPException(409, "Teacher email already exists")
    return storage.add("teachers", data.model_dump())


@app.get("/teachers")
def list_teachers():
    return storage.load("teachers")


@app.get("/teachers/{teacher_id}")
def get_teacher(teacher_id: int):
    teacher = storage.get("teachers", teacher_id)
    if not teacher:
        raise HTTPException(404, "Teacher not found")
    return teacher


# ---------- Students ----------
@app.post("/students")
def create_student(data: StudentIn):
    if any(s["register_number"] == data.register_number for s in storage.load("students")):
        raise HTTPException(409, "Register number already exists")
    return storage.add("students", data.model_dump())


@app.get("/students")
def list_students():
    return storage.load("students")


@app.get("/students/{student_id}")
def get_student(student_id: int):
    student = storage.get("students", student_id)
    if not student:
        raise HTTPException(404, "Student not found")
    return student


# ---------- Assignments ----------
@app.post("/assignments")
def create_assignment(data: AssignmentIn):
    if not storage.get("teachers", data.teacher_id):
        raise HTTPException(404, "Teacher not found")
    return storage.add("assignments", data.model_dump(mode="json"))


@app.get("/assignments")
def list_assignments(teacher_id: int | None = None):
    items = storage.load("assignments")
    if teacher_id is not None:
        items = [a for a in items if a["teacher_id"] == teacher_id]
    return items


@app.get("/assignments/{assignment_id}/submissions")
def assignment_submissions(assignment_id: int):
    return [s for s in storage.load("submissions") if s["assignment_id"] == assignment_id]


@app.get("/students/{student_id}/pending-assignments")
def pending_assignments(student_id: int):
    submitted = {s["assignment_id"] for s in storage.load("submissions")
                 if s["student_id"] == student_id}
    return [a for a in storage.load("assignments") if a["id"] not in submitted]


# ---------- Submissions ----------
@app.post("/submissions")
def submit_assignment(
    assignment_id: int = Form(...),
    student_id: int = Form(...),
    file: UploadFile = File(...),
):
    assignment = storage.get("assignments", assignment_id)
    student = storage.get("students", student_id)
    if not assignment or not student:
        raise HTTPException(404, "Assignment or student not found")
    if any(s["assignment_id"] == assignment_id and s["student_id"] == student_id
           for s in storage.load("submissions")):
        raise HTTPException(409, "Already submitted")

    file_url = upload_via_temp(file, assignment["teacher_id"], "assignment", student["name"])
    record = storage.add("submissions", {
        "assignment_id": assignment_id,
        "student_id": student_id,
        "file_url": file_url,
        "submitted_at": datetime.utcnow().isoformat(),
    })
    return {"status": "submitted", **record}


# ---------- OD requests ----------
@app.post("/od-requests")
def create_od_request(
    student_id: int = Form(...),
    teacher_id: int = Form(...),
    reason: str = Form(...),
    from_date: datetime = Form(...),
    to_date: datetime = Form(...),
    proof: UploadFile | None = File(None),
):
    student = storage.get("students", student_id)
    if not student or not storage.get("teachers", teacher_id):
        raise HTTPException(404, "Student or teacher not found")
    if to_date < from_date:
        raise HTTPException(400, "to_date must be after from_date")

    proof_url = None
    if proof and proof.filename:
        proof_url = upload_via_temp(proof, teacher_id, "od", student["name"])

    return storage.add("od_requests", {
        "student_id": student_id,
        "teacher_id": teacher_id,
        "reason": reason,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "status": "pending",
        "proof_url": proof_url,
        "created_at": datetime.utcnow().isoformat(),
    })


@app.get("/od-requests")
def list_od_requests(student_id: int | None = None, teacher_id: int | None = None,
                     status: str | None = None):
    items = storage.load("od_requests")
    if student_id is not None:
        items = [i for i in items if i["student_id"] == student_id]
    if teacher_id is not None:
        items = [i for i in items if i["teacher_id"] == teacher_id]
    if status:
        items = [i for i in items if i["status"] == status]
    return sorted(items, key=lambda i: i["created_at"], reverse=True)


@app.patch("/od-requests/{od_id}")
def decide_od_request(od_id: int, data: ODDecision):
    updated = storage.update("od_requests", od_id, {"status": data.status})
    if not updated:
        raise HTTPException(404, "OD request not found")
    return updated
