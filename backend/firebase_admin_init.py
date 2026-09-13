import firebase_admin
from firebase_admin import credentials, auth as admin_auth
import os
from pathlib import Path
    
cred_path = (
    "/etc/secrets/firebase-admin.json"
    if os.path.exists("/etc/secrets/firebase-admin.json")
    else str(Path(__file__).with_name("firebase-admin.json"))
)
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
