import app.config.firebase_admin_init  # noqa: F401
from firebase_admin import db
from datetime import datetime


def _history_ref(user_id):
    return db.reference(f"user/{user_id}/history")


def retrieve_history(user_id):
    data = _history_ref(user_id).get()

    if data is None:
        return []

    # New format: dictionary of Firebase push IDs
    if isinstance(data, dict):
        history = []

        for item_id, item in data.items():
            history.append({
                "id": item_id,
                "translation": item.get("translation", ""),
                "timestamp": item.get("timestamp", "")
            })

        # Newest first
        history.sort(
            key=lambda x: x["timestamp"],
            reverse=True
        )

        return history

    return []


def store_translation(user_id, translation):
    ref = _history_ref(user_id).push()

    item = {
        "translation": translation,
        "timestamp": datetime.now().isoformat()
    }

    ref.set(item)

    return {
        "id": ref.key,
        **item
    }


def delete_translation(user_id, translation_id):
    ref = _history_ref(user_id).child(translation_id)

    if ref.get() is None:
        return False

    ref.delete()
    return True

def delete_all_translations(user_id):
    ref = _history_ref(user_id)
    if ref.get() is None:
        return False
    ref.delete()
    return True