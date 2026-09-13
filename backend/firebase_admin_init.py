import firebase_admin
from firebase_admin import credentials, auth as admin_auth
import os
    
cred_path = (
    "/etc/secrets/firebase-admin.json"
    if os.path.exists("/etc/secrets/firebase-admin.json")
    else "firebase-admin.json"
)
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)