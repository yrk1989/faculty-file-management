import os
import msal
import requests
from urllib.parse import urlparse, parse_qs

CLIENT_ID = "0658ab66-b40c-4d28-8038-78412d51e828"
CLIENT_SECRET = os.environ["CLIENT_SECRET"]   # don't hardcode secrets
AUTHORITY = "https://login.microsoftonline.com/common"  # or your tenant ID / "consumers"
SCOPES = ["Files.ReadWrite"]
REDIRECT_URI = "http://localhost:5000/callback"

app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET,
)

# 1. Generate the login link
auth_url = app.get_authorization_request_url(SCOPES, redirect_uri=REDIRECT_URI)
print("\n1. Open this link in your browser:")
print(auth_url)

# 2. Capture the authorization code
redirect_response = input("\n2. After clicking Accept, paste the FULL URL from the address bar:\n").strip()
params = parse_qs(urlparse(redirect_response).query)

if "code" not in params:
    raise SystemExit(f"No auth code found. Response params: {params}")
auth_code = params["code"][0]

# 3. Exchange the code for a token
token_result = app.acquire_token_by_authorization_code(
    auth_code,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI,
)

if "access_token" not in token_result:
    raise SystemExit(
        f"Token error: {token_result.get('error')}\n{token_result.get('error_description')}"
    )
access_token = token_result["access_token"]

# 4. Upload hello.txt to OneDrive/Faculty_Portal/Assignments
file_name = "hello.txt"
folder = "Faculty_Portal/Assignments"
upload_url = (
    f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder}/{file_name}:/content"
)

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/octet-stream",
}

with open(file_name, "rb") as f:
    response = requests.put(upload_url, headers=headers, data=f)

if response.status_code in (200, 201):
    print("\nSUCCESS! Check OneDrive -> Faculty_Portal -> Assignments")
else:
    print("\nError:", response.status_code, response.text)