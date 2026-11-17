from urllib.parse import urlparse, parse_qs
import onedrive_service

teacher = "prof_sharma"

# 1. Pretend the teacher clicked "Connect OneDrive"
print("1. Open this link and log in as Prof. Sharma:")
print(onedrive_service.get_teacher_login_url(teacher))

# 2. Capture the callback code + state and save the permanent token
url = input("\n2. Paste the full redirect URL here:\n").strip()
params = parse_qs(urlparse(url).query)

if "code" not in params or "state" not in params:
    raise SystemExit(f"Missing code or state in URL. Got: {list(params)}")

code = params["code"][0]
state = params["state"][0]

save_status = onedrive_service.save_teacher_token(state, code)  # state first!
print("Token Saved:", save_status)

if not save_status.get("success"):
    raise SystemExit("Fix the token step before trying the upload.")

# 3. Pretend a student uploaded an assignment!
upload_status = onedrive_service.upload_to_teacher_onedrive(
    teacher_id=teacher,
    local_file_path="hello.txt",
    doc_type="assignment",
    student_name="Rahul",
)
print("Upload Result:", upload_status)