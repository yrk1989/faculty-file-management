
import os
import re
import io
import json
import secrets
import threading
from datetime import datetime, date
from urllib.parse import quote

import msal
import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ----------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------

CLIENT_ID = "0658ab66-b40c-4d28-8038-78412d51e828"
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Files.ReadWrite"]
REDIRECT_URI = "http://localhost:5000/callback"

TOKEN_FILE = "teacher_tokens.json"
OD_FILE = "od_records.json"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

ADMIN_TEACHER_IDS = {
    t.strip()
    for t in os.environ.get("ADMIN_TEACHER_IDS", "").split(",")
    if t.strip()
}

if not CLIENT_SECRET:
    raise RuntimeError("CLIENT_SECRET environment variable is not set.")

msal_app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET,
)

_pending_states = {}
_file_lock = threading.Lock()


# ----------------------------------------------------------------------------
# JSON STORAGE HELPERS
# ----------------------------------------------------------------------------

def _load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_tokens():
    return _load_json(TOKEN_FILE, {})


def _save_tokens(tokens):
    _save_json(TOKEN_FILE, tokens)


def _load_ods():
    return _load_json(OD_FILE, {})


def _save_ods(records):
    _save_json(OD_FILE, records)


def _safe_name(text):
    """Remove characters that can cause path or URL problems."""
    cleaned = re.sub(
        r'[\\/:*?"<>|#%&~{}\x00-\x1f]',
        "_",
        str(text)
    )
    return cleaned.strip(" .") or "unnamed"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_admin(teacher_id):
    return str(teacher_id) in ADMIN_TEACHER_IDS


# ----------------------------------------------------------------------------
# MICROSOFT LOGIN
# ----------------------------------------------------------------------------

def get_teacher_login_url(teacher_id):
    state = secrets.token_urlsafe(16)
    _pending_states[state] = str(teacher_id)

    return msal_app.get_authorization_request_url(
        SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state,
    )


def save_teacher_token(state, auth_code):
    teacher_id = _pending_states.pop(state, None)

    if teacher_id is None:
        return {
            "success": False,
            "error": "Invalid or expired state."
        }

    result = msal_app.acquire_token_by_authorization_code(
        auth_code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    if "refresh_token" not in result:
        return {
            "success": False,
            "error": result.get("error_description")
        }

    with _file_lock:
        tokens = _load_tokens()
        tokens[teacher_id] = result["refresh_token"]
        _save_tokens(tokens)

    return {
        "success": True,
        "teacher_id": teacher_id,
        "is_admin": is_admin(teacher_id)
    }


# ----------------------------------------------------------------------------
# ONEDRIVE ACCESS TOKEN
# ----------------------------------------------------------------------------

def _get_access_token(teacher_id):
    """Returns (access_token, error_message)."""

    teacher_id = str(teacher_id)

    with _file_lock:
        tokens = _load_tokens()

        if teacher_id not in tokens:
            return None, "Teacher has not connected OneDrive yet."

        result = msal_app.acquire_token_by_refresh_token(
            tokens[teacher_id],
            scopes=SCOPES
        )

        if "access_token" not in result:
            return None, result.get(
                "error_description",
                "Token expired. Teacher needs to reconnect."
            )

        if "refresh_token" in result:
            tokens[teacher_id] = result["refresh_token"]
            _save_tokens(tokens)

    return result["access_token"], None


# ----------------------------------------------------------------------------
# ONEDRIVE FILE UPLOAD
# ----------------------------------------------------------------------------

def _upload(access_token, folder, filename, fileobj, conflict="rename"):
    """Upload a file to Faculty_Portal/<folder>/<filename>."""

    item_path = quote(
        f"Faculty_Portal/{folder}/{filename}"
    )

    url = (
        f"{GRAPH_BASE}/me/drive/root:/{item_path}:/content"
        f"?@microsoft.graph.conflictBehavior={conflict}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
    }

    response = requests.put(
        url,
        headers=headers,
        data=fileobj,
        timeout=120
    )

    if response.status_code in (200, 201):
        return {
            "success": True,
            "file_url": response.json().get("webUrl")
        }

    return {
        "success": False,
        "error": f"{response.status_code}: {response.text}"
    }


# ----------------------------------------------------------------------------
# NEW: GENERAL UPLOAD FUNCTION USED BY main.py
# ----------------------------------------------------------------------------

def upload_to_teacher_onedrive(
    teacher_id,
    local_file_path,
    doc_type,
    student_name
):
    """Upload assignments, OD permission letters, and OD proof."""

    folders = {
        "assignment": "Assignments",
        "od_permission": "OD_Permission",
        "od_proof": "OD_Proof",
    }

    if doc_type not in folders:
        return {
            "success": False,
            "error": "Invalid document type."
        }

    if not os.path.isfile(local_file_path):
        return {
            "success": False,
            "error": "File not found."
        }

    token, err = _get_access_token(str(teacher_id))

    if err:
        return {
            "success": False,
            "error": err
        }

    filename = (
        f"{_safe_name(student_name)}_"
        f"{_safe_name(os.path.basename(local_file_path))}"
    )

    try:
        with open(local_file_path, "rb") as f:
            return _upload(
                token,
                folders[doc_type],
                filename,
                f
            )

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ----------------------------------------------------------------------------
# OD FLOW
# ----------------------------------------------------------------------------

def submit_od_permission(
    teacher_id,
    student_name,
    event_name,
    from_date,
    to_date,
    reason,
    letter_path=None
):
    """
    Submit an OD permission request.

    Dates must use YYYY-MM-DD format.
    """

    try:
        d1 = date.fromisoformat(from_date)
        d2 = date.fromisoformat(to_date)

    except ValueError:
        return {
            "success": False,
            "error": "Dates must be in YYYY-MM-DD format."
        }

    if d2 < d1:
        return {
            "success": False,
            "error": "to_date cannot be before from_date."
        }

    teacher_id = str(teacher_id)

    if teacher_id not in _load_tokens():
        return {
            "success": False,
            "error": "Teacher has not connected OneDrive yet."
        }

    letter_url = None

    if letter_path:
        if not os.path.isfile(letter_path):
            return {
                "success": False,
                "error": f"File not found: {letter_path}"
            }

        token, err = _get_access_token(teacher_id)

        if err:
            return {
                "success": False,
                "error": err
            }

        name = (
            f"{_safe_name(student_name)}_"
            f"{_safe_name(os.path.basename(letter_path))}"
        )

        with open(letter_path, "rb") as f:
            up = _upload(
                token,
                "OD_Permission",
                name,
                f
            )

        if not up["success"]:
            return up

        letter_url = up["file_url"]

    od_id = "OD-" + secrets.token_hex(4).upper()

    record = {
        "od_id": od_id,
        "student_name": student_name,
        "teacher_id": teacher_id,
        "event_name": event_name,
        "from_date": from_date,
        "to_date": to_date,
        "reason": reason,
        "permission_letter_url": letter_url,
        "status": "pending",
        "remarks": "",
        "decided_by": None,
        "decided_at": None,
        "proof_url": None,
        "proof_submitted_at": None,
        "created_at": _now(),
    }

    with _file_lock:
        records = _load_ods()
        records[od_id] = record
        _save_ods(records)

    return {
        "success": True,
        "od_id": od_id
    }


# ----------------------------------------------------------------------------
# STEP 2: FACULTY APPROVES OR REJECTS OD
# ----------------------------------------------------------------------------

def decide_od(teacher_id, od_id, decision, remarks=""):

    teacher_id = str(teacher_id)
    decision = decision.lower()

    if decision not in ("approved", "rejected"):
        return {
            "success": False,
            "error": "decision must be 'approved' or 'rejected'."
        }

    with _file_lock:
        records = _load_ods()
        rec = records.get(od_id)

        if not rec:
            return {
                "success": False,
                "error": "OD request not found."
            }

        if str(rec["teacher_id"]) != teacher_id and not is_admin(teacher_id):
            return {
                "success": False,
                "error": "You are not allowed to decide this OD."
            }

        if rec["status"] != "pending":
            return {
                "success": False,
                "error": f"OD already {rec['status']}."
            }

        rec.update(
            status=decision,
            remarks=remarks,
            decided_by=teacher_id,
            decided_at=_now(),
        )

        _save_ods(records)

    return {
        "success": True,
        "od_id": od_id,
        "status": decision
    }


# ----------------------------------------------------------------------------
# STEP 3: STUDENT SUBMITS OD PROOF
# ----------------------------------------------------------------------------

def submit_od_proof(od_id, student_name, local_file_path):

    if not os.path.isfile(local_file_path):
        return {
            "success": False,
            "error": f"File not found: {local_file_path}"
        }

    records = _load_ods()
    rec = records.get(od_id)

    if not rec:
        return {
            "success": False,
            "error": "OD request not found."
        }

    if rec["student_name"].strip().lower() != student_name.strip().lower():
        return {
            "success": False,
            "error": "This OD does not belong to this student."
        }

    if rec["status"] != "approved":
        return {
            "success": False,
            "error": "Proof can only be submitted for an approved OD."
        }

    if rec["proof_url"]:
        return {
            "success": False,
            "error": "Proof already submitted for this OD."
        }

    token, err = _get_access_token(rec["teacher_id"])

    if err:
        return {
            "success": False,
            "error": err
        }

    name = (
        f"{od_id}_"
        f"{_safe_name(student_name)}_"
        f"{_safe_name(os.path.basename(local_file_path))}"
    )

    with open(local_file_path, "rb") as f:
        up = _upload(
            token,
            "OD_Proof",
            name,
            f
        )

    if not up["success"]:
        return up

    with _file_lock:
        records = _load_ods()

        records[od_id]["proof_url"] = up["file_url"]
        records[od_id]["proof_submitted_at"] = _now()

        _save_ods(records)

    return {
        "success": True,
        "file_url": up["file_url"]
    }


# ----------------------------------------------------------------------------
# TEACHER OD LIST
# ----------------------------------------------------------------------------

def list_ods_for_teacher(teacher_id, status=None):

    teacher_id = str(teacher_id)

    records = list(_load_ods().values())

    if not is_admin(teacher_id):
        records = [
            r for r in records
            if str(r["teacher_id"]) == teacher_id
        ]

    if status:
        records = [
            r for r in records
            if r["status"] == status
        ]

    return sorted(
        records,
        key=lambda r: r["created_at"],
        reverse=True
    )


# ----------------------------------------------------------------------------
# EXCEL REPORT
# ----------------------------------------------------------------------------

_EXCEL_COLUMNS = [
    ("OD ID", "od_id"),
    ("Student", "student_name"),
    ("Faculty ID", "teacher_id"),
    ("Event", "event_name"),
    ("From", "from_date"),
    ("To", "to_date"),
    ("Reason", "reason"),
    ("Status", "status"),
    ("Remarks", "remarks"),
    ("Decided By", "decided_by"),
    ("Decided At", "decided_at"),
    ("Proof Submitted", "proof_submitted_at"),
    ("Proof Link", "proof_url"),
    ("Permission Letter", "permission_letter_url"),
    ("Requested At", "created_at"),
]


def _fill_sheet(ws, rows, header_color):

    ws.append([h for h, _ in _EXCEL_COLUMNS])

    for cell in ws[1]:
        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor=header_color
        )

        cell.alignment = Alignment(
            horizontal="center"
        )

    for r in rows:
        ws.append([
            r.get(key) or ""
            for _, key in _EXCEL_COLUMNS
        ])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for i, (header, key) in enumerate(_EXCEL_COLUMNS, start=1):

        longest = max(
            [len(header)] +
            [len(str(r.get(key) or "")) for r in rows]
        )

        ws.column_dimensions[
            get_column_letter(i)
        ].width = min(longest + 2, 45)


# ----------------------------------------------------------------------------
# MODIFIED: BUILD EXCEL USING RECORDS FROM main.py
# ----------------------------------------------------------------------------

def build_od_excel(path="OD_Report.xlsx", records=None):
    """
    Build an Excel workbook with:
    - All Decided
    - Approved
    - Rejected

    If records are supplied, use those.
    Otherwise, use the OneDrive service's own OD records.
    """

    if records is None:
        records = list(_load_ods().values())

    records = sorted(
        records,
        key=lambda r: r.get("created_at", ""),
        reverse=True
    )

    approved = [
        r for r in records
        if r["status"] == "approved"
    ]

    rejected = [
        r for r in records
        if r["status"] == "rejected"
    ]

    wb = Workbook()

    ws_all = wb.active
    ws_all.title = "All Decided"

    _fill_sheet(
        ws_all,
        approved + rejected,
        "1F4E78"
    )

    _fill_sheet(
        wb.create_sheet("Approved"),
        approved,
        "2E7D32"
    )

    _fill_sheet(
        wb.create_sheet("Rejected"),
        rejected,
        "C62828"
    )

    wb.save(path)

    return path, len(approved), len(rejected)


# ----------------------------------------------------------------------------
# MODIFIED: ADMIN EXCEL EXPORT
# ----------------------------------------------------------------------------

def admin_export_od_excel(
    admin_id,
    local_path="OD_Report.xlsx",
    upload=True,
    records=None
):
    """
    Admin only.

    Creates the Excel report using supplied records or the
    OneDrive service's own OD records.

    Optionally uploads the report to the admin's OneDrive.
    """

    admin_id = str(admin_id)

    if not is_admin(admin_id):
        return {
            "success": False,
            "error": "Only an admin can export the OD report."
        }

    path, n_approved, n_rejected = build_od_excel(
        local_path,
        records=records
    )

    result = {
        "success": True,
        "local_file": path,
        "approved": n_approved,
        "rejected": n_rejected,
    }

    if upload:

        token, err = _get_access_token(admin_id)

        if err:
            result["upload_error"] = err
            return result

        with open(path, "rb") as f:
            up = _upload(
                token,
                "Admin_Reports",
                "OD_Report.xlsx",
                f,
                conflict="replace"
            )

        if up["success"]:
            result["onedrive_url"] = up["file_url"]

        else:
            result["upload_error"] = up["error"]

    return result
