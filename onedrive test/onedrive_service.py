import os
import re
import json
import secrets
import msal
import requests
from urllib.parse import quote

CLIENT_ID = "0658ab66-b40c-4d28-8038-78412d51e828"
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Files.ReadWrite"]
REDIRECT_URI = "http://localhost:5000/callback"
TOKEN_FILE = "teacher_tokens.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

if not CLIENT_SECRET:
    raise RuntimeError("CLIENT_SECRET environment variable is not set.")

msal_app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET,
)

# state nonce -> teacher_id (use a database/session store in a real app)
_pending_states = {}


def _load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def _safe_name(text):
    """Remove characters that break paths/URLs or allow path traversal."""
    cleaned = re.sub(r'[\\/:*?"<>|#%&~{}\x00-\x1f]', "_", str(text))
    return cleaned.strip(" .") or "unnamed"


# 1. Generate the Microsoft login link for a teacher
def get_teacher_login_url(teacher_id):
    state = secrets.token_urlsafe(16)
    _pending_states[state] = teacher_id
    return msal_app.get_authorization_request_url(
        SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state,
    )


# 2. Save the teacher's refresh token after they click Accept
#    `state` is the value Microsoft sends back in the callback URL
def save_teacher_token(state, auth_code):
    teacher_id = _pending_states.pop(state, None)
    if teacher_id is None:
        return {"success": False, "error": "Invalid or expired state."}

    result = msal_app.acquire_token_by_authorization_code(
        auth_code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    if "refresh_token" not in result:
        return {"success": False, "error": result.get("error_description")}

    tokens = _load_tokens()
    tokens[teacher_id] = result["refresh_token"]
    _save_tokens(tokens)
    return {"success": True, "teacher_id": teacher_id}


# 3. Upload a student's file to that teacher's OneDrive
def upload_to_teacher_onedrive(teacher_id, local_file_path, doc_type, student_name):
    if not os.path.isfile(local_file_path):
        return {"success": False, "error": f"File not found: {local_file_path}"}

    tokens = _load_tokens()
    if teacher_id not in tokens:
        return {"success": False, "error": "Teacher has not connected OneDrive yet."}

    result = msal_app.acquire_token_by_refresh_token(
        tokens[teacher_id],
        scopes=SCOPES,
    )
    if "access_token" not in result:
        return {
            "success": False,
            "error": result.get("error_description", "Token expired. Teacher needs to reconnect."),
        }

    # Refresh tokens rotate, so save the new one
    if "refresh_token" in result:
        tokens[teacher_id] = result["refresh_token"]
        _save_tokens(tokens)

    access_token = result["access_token"]

    original_name = _safe_name(os.path.basename(local_file_path))
    clean_filename = f"{_safe_name(student_name)}_{original_name}"
    folder_category = "Assignments" if doc_type.lower() == "assignment" else "OD_Requests"

    item_path = quote(f"Faculty_Portal/{folder_category}/{clean_filename}")  # keeps "/" intact
    upload_url = (
        f"{GRAPH_BASE}/me/drive/root:/{item_path}:/content"
        f"?@microsoft.graph.conflictBehavior=rename"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
    }

    with open(local_file_path, "rb") as file_data:
        response = requests.put(upload_url, headers=headers, data=file_data, timeout=120)

    if response.status_code in (200, 201):
        return {"success": True, "file_url": response.json().get("webUrl")}
    return {"success": False, "error": f"{response.status_code}: {response.text}"}