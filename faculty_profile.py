"""
Faculty profile API: courses taken by year + research publications.
Uses the existing storage.py (JSON files).

Add to main.py:
    from faculty_profile import router as faculty_router
    app.include_router(faculty_router)

Creates data/faculty_courses.json and data/publications.json automatically.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import storage

router = APIRouter(tags=["Faculty profile"])

YEAR = r"^\d{4}-\d{2}$"          # e.g. 2024-25
PubType = Literal["journal", "conference", "book chapter"]


class CourseIn(BaseModel):
    course_code: str = Field(min_length=1, max_length=20)
    course_name: str = Field(min_length=1, max_length=150)
    academic_year: str = Field(pattern=YEAR)
    semester: str | None = None
    section: str | None = None


class CourseUpdate(BaseModel):
    course_code: str | None = None
    course_name: str | None = None
    academic_year: str | None = Field(default=None, pattern=YEAR)
    semester: str | None = None
    section: str | None = None


class PublicationIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    venue: str = Field(min_length=1, max_length=200)
    year: int = Field(ge=1950, le=2100)
    pub_type: PubType = "journal"
    authors: str | None = None
    link: str | None = None


class PublicationUpdate(BaseModel):
    title: str | None = None
    venue: str | None = None
    year: int | None = Field(default=None, ge=1950, le=2100)
    pub_type: PubType | None = None
    authors: str | None = None
    link: str | None = None


def _teacher_or_404(teacher_id: int) -> dict:
    teacher = storage.get("teachers", teacher_id)
    if not teacher:
        raise HTTPException(404, "Teacher not found")
    return teacher


def _delete(name: str, record_id: int):
    with storage._lock:
        items = storage.load(name)
        if not any(i["id"] == record_id for i in items):
            raise HTTPException(404, "Not found")
        storage.save(name, [i for i in items if i["id"] != record_id])


# ---------- Full profile (students and faculty both use this) ----------
@router.get("/teachers/{teacher_id}/profile")
def faculty_profile(teacher_id: int):
    teacher = _teacher_or_404(teacher_id)

    courses = [c for c in storage.load("faculty_courses") if c["teacher_id"] == teacher_id]
    courses_by_year: dict[str, list] = {}
    for c in sorted(courses, key=lambda c: (c["academic_year"], c["course_code"]), reverse=True):
        courses_by_year.setdefault(c["academic_year"], []).append(c)

    publications = sorted(
        (p for p in storage.load("publications") if p["teacher_id"] == teacher_id),
        key=lambda p: p["year"], reverse=True,
    )
    return {"teacher": teacher, "courses_by_year": courses_by_year, "publications": publications}


# ---------- Courses ----------
@router.post("/teachers/{teacher_id}/courses")
def add_course(teacher_id: int, data: CourseIn):
    _teacher_or_404(teacher_id)
    return storage.add("faculty_courses", {**data.model_dump(), "teacher_id": teacher_id})


@router.put("/faculty-courses/{course_id}")
def edit_course(course_id: int, data: CourseUpdate):
    updated = storage.update("faculty_courses", course_id, data.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Course not found")
    return updated


@router.delete("/faculty-courses/{course_id}")
def delete_course(course_id: int):
    _delete("faculty_courses", course_id)
    return {"status": "deleted"}


# ---------- Publications ----------
@router.post("/teachers/{teacher_id}/publications")
def add_publication(teacher_id: int, data: PublicationIn):
    _teacher_or_404(teacher_id)
    return storage.add("publications", {**data.model_dump(), "teacher_id": teacher_id})


@router.put("/publications/{pub_id}")
def edit_publication(pub_id: int, data: PublicationUpdate):
    updated = storage.update("publications", pub_id, data.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Publication not found")
    return updated


@router.delete("/publications/{pub_id}")
def delete_publication(pub_id: int):
    _delete("publications", pub_id)
    return {"status": "deleted"}