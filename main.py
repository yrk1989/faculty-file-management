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

FRONTEND = "http://localhost:3000"

app = FastAPI(title="Faculty Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND, "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Faculty the frontend's course table refers to (matched by initials).
SEED_FACULTY = [
    ("Dr. S. Sampath Kumar", "SSK"), ("Dr. Veramachaneni Sree Hari", "VSH"),
    ("Dr. Nimeshika Venkatesan", "NV"), ("Dr. K. S. Gayathri", "KSG"),
    ("Dr. E. Suganya", "ES"), ("Dr. V. Durgadevi", "VDD"),
    ("Dr. A. Saravanan", "SA"), ("Dr. L. Ilakkiya", "LI"),
    ("Ms. B. Naveena", "BN"),
]


@app.on_event("startup")
def seed():
    if not storage.load("teachers"):
        for name, ini in SEED_FACULTY:
            storage.add("teachers", {
                "name": name, "email": f"{ini.lower()}@example.edu", "department": "IT",
                "initials": ini, "designation": "Faculty", "phone": "", "cabin": "",
                "research": "", "qualification": "", "photo": "",
            })


# ---------- Request shapes ----------
class TeacherIn(BaseModel):
    name: str
    email: str
    department: str | None = None
    initials: str | None = None
    designation: str | None = None
    phone: str | None = None
    cabin: str | None = None
    research: str | None = None
    qualification: str | None = None
    photo: str | None = None  # data URL


class TeacherUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    department: str | None = None
    initials: str | None = None
    designation: str | None = None
    phone: str | None = None
    cabin: str | None = None
    research: str | None = None
    qualification: str | None = None
    photo: str | None = None


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
    remark: str = ""


# ---------- Helpers ----------
def upload_via_temp(file: UploadFile, teacher_id: int, doc_type: str, student_name: str):
    """Save the upload to a temp file, send it to the teacher's OneDrive, clean up.
    doc_type: "assignment" | "od_permission" | "od_proof" """
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


def ensure_student(name: str, register_number: str, department: str | None):
    for s in storage.load("students"):
        if s["register_number"] == register_number:
            return s
    return storage.add("students", {
        "register_number": register_number, "name": name,
        "email": None, "department": department, "year": None,
    })


# ---------- OneDrive connection ----------
@app.get("/teachers/{teacher_id}/onedrive/connect")
def connect_onedrive(teacher_id: int):
    if not storage.get("teachers", teacher_id):
        raise HTTPException(404, "Teacher not found")
    return {"login_url": od.get_teacher_login_url(str(teacher_id))}


@app.get("/teachers/{teacher_id}/onedrive/status")
def onedrive_status(teacher_id: int):
    return {"connected": str(teacher_id) in od._load_tokens()}


@app.get("/callback")   # must match REDIRECT_URI in onedrive_service.py
def callback(code: str = None, state: str = None, error: str = None):
    if error or not code or not state:
        raise HTTPException(400, "OneDrive connection failed")
    result = od.save_teacher_token(state, code)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return RedirectResponse(f"{FRONTEND}/?connected=1")


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


@app.put("/teachers/{teacher_id}")
def update_teacher(teacher_id: int, data: TeacherUpdate):
    updated = storage.update("teachers", teacher_id, data.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Teacher not found")
    return updated


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
    student_name: str = Form(...),
    register_number: str = Form(...),
    department: str = Form(""),
    year_section: str = Form(""),
    teacher_id: int = Form(...),
    category: str = Form(...),
    event: str = Form(...),
    venue: str = Form(...),
    from_date: datetime = Form(...),
    to_date: datetime = Form(...),
    proof: UploadFile | None = File(None),   # now treated as the permission letter
):
    if not storage.get("teachers", teacher_id):
        raise HTTPException(404, "Teacher not found")
    if to_date <= from_date:
        raise HTTPException(400, "to_date must be after from_date")

    student = ensure_student(student_name, register_number, department)

    # CHANGED: the file sent with the request is the permission letter
    letter_url = None
    if proof and proof.filename:
        letter_url = upload_via_temp(proof, teacher_id, "od_permission", student["name"])

    return storage.add("od_requests", {
        "student_id": student["id"],
        "student_name": student_name,
        "register_number": register_number,
        "department": department,
        "year_section": year_section,
        "teacher_id": teacher_id,
        "category": category,
        "event": event,
        "venue": venue,
        "reason": f"{category}: {event}",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "status": "pending",
        "remark": "",
        "permission_letter_url": letter_url,   # CHANGED
        "proof_url": None,                     # CHANGED: filled after approval
        "proof_submitted_at": None,            # NEW
        "decided_at": None,                    # NEW
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


# CHANGED: a request can only be decided once
@app.patch("/od-requests/{od_id}")
def decide_od_request(od_id: int, data: ODDecision):
    rec = storage.get("od_requests", od_id)
    if not rec:
        raise HTTPException(404, "OD request not found")
    if rec["status"] != "pending":
        raise HTTPException(400, f"OD already {rec['status']}")
    return storage.update("od_requests", od_id, {
        "status": data.status,
        "remark": data.remark,
        "decided_at": datetime.utcnow().isoformat(),
    })


# NEW: proof upload, allowed only after approval
@app.post("/od-requests/{od_id}/proof")
def submit_od_proof(od_id: int, register_number: str = Form(...), file: UploadFile = File(...)):
    rec = storage.get("od_requests", od_id)
    if not rec:
        raise HTTPException(404, "OD request not found")
    if rec["register_number"] != register_number:
        raise HTTPException(403, "This OD does not belong to this student")
    if rec["status"] != "approved":
        raise HTTPException(400, "Proof can only be submitted for an approved OD")
    if rec.get("proof_url"):
        raise HTTPException(409, "Proof already submitted")

    url = upload_via_temp(file, rec["teacher_id"], "od_proof", rec["student_name"])
    return storage.update("od_requests", od_id, {
        "proof_url": url,
        "proof_submitted_at": datetime.utcnow().isoformat(),
    })


# NEW (optional): Excel report. Needs the build_od_excel change in section 3.
@app.get("/admin/od-report")
def od_report(admin_id: int):
    rows = [{**r, "od_id": f"OD-{r['id']}", "event_name": r.get("event"),
             "remarks": r.get("remark")} for r in storage.load("od_requests")]
    result = od.admin_export_od_excel(str(admin_id), records=rows)
    if not result["success"]:
        raise HTTPException(403, result["error"])
    return result
from faculty_profile import router as faculty_router
app.include_router(faculty_router)
