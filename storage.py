import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.RLock()   # FastAPI runs plain `def` routes in threads


def _path(name):
    return os.path.join(DATA_DIR, f"{name}.json")


def load(name):
    with _lock:
        if not os.path.exists(_path(name)):
            return []
        with open(_path(name), "r", encoding="utf-8") as f:
            return json.load(f)


def save(name, items):
    with _lock:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        os.replace(tmp, _path(name))   # atomic: no half-written files


def add(name, record):
    with _lock:
        items = load(name)
        record["id"] = max((i["id"] for i in items), default=0) + 1
        items.append(record)
        save(name, items)
        return record


def get(name, record_id):
    return next((i for i in load(name) if i["id"] == record_id), None)


def update(name, record_id, changes):
    with _lock:
        items = load(name)
        for item in items:
            if item["id"] == record_id:
                item.update(changes)
                save(name, items)
                return item
        return None
