import json

import pyrebase

firebase_path = "/etc/secrets/firebase.json" if os.path.exists("/etc/secrets/firebase.json") else "firebase.json"

firebaseJSON = open(firebase_path)
firebaseConfig = json.load(firebaseJSON)

# initialize firebase
firebase = pyrebase.initialize_app(firebaseConfig)
# access firebase authentication
auth = firebase.auth()


def register_account(email, password):
    try:
        # create user account in firebase
        user = auth.create_user_with_email_and_password(email, password)
        return user["localId"]
    except:
        return ""


def login_account(email, password):
    try:
        # verify user account and return user id
        login = auth.sign_in_with_email_and_password(email, password)
        return login["localId"]
    except:
        return ""
