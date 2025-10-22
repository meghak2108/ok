import os
import re
import tempfile
from pathlib import Path
import streamlit as st
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

# ---------------- CONFIG ----------------
CLIENT_SECRET_FILE = {"installed":{"client_id":"257082126321-j0vjhvdiieej5athd9mvk98trksts1ac.apps.googleusercontent.com","project_id":"clever-cogency-475005-p0","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_secret":"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","redirect_uris":["http://localhost"]}}
TOKEN_FILE = r"C:\Users\Hp\Downloads\token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
MAX_BYTES = 50 * 1024 ** 3  # 50 GB

# Map top-level directories to Drive folder IDs
DIRECTORY_FOLDER_IDS = {
    "ACS": "1wJUNj91l4w-Or8PEWeTJ7RUTGXRkXMDr",
    # Add more top-level folders here
}
# ---------------------------------------

st.title("Drive ZIP Upload with Folder Selection & Versioning")

# ---------------- Google Drive Service ----------------
def get_gdrive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        # Save token for future runs
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())
    
    service = build("drive", "v3", credentials=creds)
    return service

service = get_gdrive_service()
st.success("Authenticated successfully!")

# ---------------- Helper: fetch folders ----------------
def fetch_folders(parent_id):
    """Recursively fetch all folders under a parent folder."""
    folders = []
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    for f in results.get("files", []):
        subfolders = fetch_folders(f["id"])
        folders.append({"id": f["id"], "name": f["name"], "subfolders": subfolders})
    return folders

def flatten_folders(folders, prefix=""):
    """Flatten folder tree into a list of display names with IDs."""
    flat_list = []
    for f in folders:
        display_name = f"{prefix}/{f['name']}" if prefix else f"{f['name']}"
        flat_list.append((display_name, f["id"]))
        if f["subfolders"]:
            flat_list.extend(flatten_folders(f["subfolders"], display_name))
    return flat_list

# ---------------- Folder Selection ----------------
top_level_dir = st.selectbox("Select Top-level Directory", list(DIRECTORY_FOLDER_IDS.keys()))
top_folder_id = DIRECTORY_FOLDER_IDS[top_level_dir]

st.info("Fetching folder structure...")
folders_tree = fetch_folders(top_folder_id)
flat_folders = [(top_level_dir, top_folder_id)] + flatten_folders(folders_tree)
folder_display_names = [f[0] for f in flat_folders]

selected_folder_display = st.selectbox("Select Folder to Upload", folder_display_names)
selected_folder_id = [f[1] for f in flat_folders if f[0] == selected_folder_display][0]

# ---------------- File Uploader ----------------
uploaded_file = st.file_uploader(f"Upload .zip file for {selected_folder_display}", type=["zip"])
if uploaded_file is None:
    st.stop()

# Validate filename pattern
filename = Path(uploaded_file.name).name
pattern = re.compile(rf"^{re.escape(top_level_dir)}_V(\d+)(\.zip)?$", re.IGNORECASE)
match = pattern.match(filename)
if not match:
    st.error(f"Filename must be {top_level_dir}_V<number>.zip. Example: {top_level_dir}_V1.zip")
    st.stop()
version_number = int(match.group(1))

# Validate file size
uploaded_file.seek(0, os.SEEK_END)
size = uploaded_file.tell()
uploaded_file.seek(0)
if size > MAX_BYTES:
    st.error(f"File too large ({size/(1024**3):.2f} GB). Max 50 GB allowed.")
    st.stop()

# Save to temp
with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
    tmp.write(uploaded_file.read())
    tmp_path = tmp.name

# ---------------- Handle duplicate filenames ----------------
def get_existing_versions(folder_id, base_name):
    """Get all files in folder starting with base_name to increment version."""
    query = f"'{folder_id}' in parents and trashed=false and name contains '{base_name}'"
    results = service.files().list(q=query, fields="files(name)").execute()
    existing_versions = []
    for f in results.get("files", []):
        m = re.match(rf"{re.escape(base_name)}_V(\d+)\.zip", f["name"])
        if m:
            existing_versions.append(int(m.group(1)))
    return existing_versions

base_name = top_level_dir
existing_versions = get_existing_versions(selected_folder_id, base_name)
if existing_versions:
    new_version = max(existing_versions) + 1
    filename = f"{base_name}_V{new_version}.zip"
    st.info(f"Duplicate found. Uploading as new version: {filename}")

# ---------------- Upload ----------------
st.info(f"Uploading to {selected_folder_display}...")
media = MediaFileUpload(tmp_path, mimetype="application/zip", resumable=True)
request = service.files().create(
    body={"name": filename, "parents": [selected_folder_id]},
    media_body=media
)

response = None
progress_bar = st.progress(0)
status_text = st.empty()

while response is None:
    status, response = request.next_chunk()
    if status:
        progress_bar.progress(int(status.progress() * 100))
        status_text.text(f"Uploading... {int(status.progress()*100)}%")

st.success(f"Upload completed! File ID: {response['id']}")

# Cleanup
os.remove(tmp_path)

