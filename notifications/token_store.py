"""APNs device-token store. Source of truth is a JSON file on disk; the
publisher's APNS_DEVICE_TOKENS env var is derived from this file.

Why JSON not DB: live_activity_publisher.py is a separate Python process
running outside Django (no ORM access without bootstrapping). A flat JSON
file lets the publisher read tokens directly without dragging in Django.
The file is updated atomically (tempfile + rename) so concurrent writers
never corrupt it.

Why a per-device dict (not a flat list of tokens): the same user can have
multiple iPhones; each must be tracked separately so we can replace a
specific device's stale token without nuking siblings.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from utils.update_env import update_env_value

# Lives in the same dir as .env so the publisher can find it without env
# var indirection. Survives container rebuild because hub-controller code
# is host-mounted, not baked.
TOKEN_STORE_PATH = Path("/root/jupyter-hub-controller/notification_tokens.json")
ENV_KEY = "APNS_DEVICE_TOKENS"

_lock = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_store():
    """Return the full dict {device_id: meta}. Empty dict if file missing /
    corrupt — never throws."""
    if not TOKEN_STORE_PATH.exists():
        return {}
    try:
        return json.loads(TOKEN_STORE_PATH.read_text())
    except Exception:
        # Corrupted file (e.g., partial write before atomic rename was wired).
        # Don't crash the API; treat as empty + recover on next register.
        return {}


def _write_store_unlocked(store):
    """Atomic write via tempfile + rename. Caller must hold the module lock."""
    TOKEN_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(TOKEN_STORE_PATH.parent),
        prefix=f"{TOKEN_STORE_PATH.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)
        os.replace(tmp_path, TOKEN_STORE_PATH)
    except Exception:
        # Best-effort cleanup of stray tempfile if rename failed.
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _sync_env_unlocked(store):
    """Write the comma-separated active-token list to .env so the publisher's
    existing 60s refresh thread picks it up. Caller must hold the module lock.
    """
    active_tokens = ",".join(meta["apns_token"] for meta in store.values())
    update_env_value(ENV_KEY, active_tokens)


def register_token(device_id, apns_token, environment, bundle_id, platform="ios"):
    """Insert or replace a device's token. Preserves the original
    registered_at when re-registering the same device. Returns the stored
    metadata."""
    with _lock:
        store = read_store()
        existing = store.get(device_id, {})
        meta = {
            "apns_token": apns_token,
            "environment": environment,
            "bundle_id": bundle_id,
            "platform": platform,
            "registered_at": existing.get("registered_at", _now_iso()),
            "last_seen_at": _now_iso(),
        }
        store[device_id] = meta
        _write_store_unlocked(store)
        _sync_env_unlocked(store)
        return meta


def remove_device(device_id):
    """Best-effort delete. Returns True if a row was removed."""
    with _lock:
        store = read_store()
        if device_id in store:
            del store[device_id]
            _write_store_unlocked(store)
            _sync_env_unlocked(store)
            return True
        return False


def remove_stale_tokens(stale_tokens):
    """Called from the publisher when Apple returns 410 Unregistered or
    400 BadDeviceToken. Removes all rows whose apns_token matches any in
    stale_tokens. Returns the number of rows removed.
    """
    if not stale_tokens:
        return 0
    stale_set = set(stale_tokens)
    with _lock:
        store = read_store()
        keep = {dev_id: m for dev_id, m in store.items() if m["apns_token"] not in stale_set}
        removed = len(store) - len(keep)
        if removed:
            _write_store_unlocked(keep)
            _sync_env_unlocked(keep)
        return removed


def cleanup_unused_tokens(stale_after_days=30):
    """Daily safety sweep. Removes rows whose last_seen_at is older than the
    threshold — covers customers who uninstalled without us seeing 410s.
    Returns the number removed.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - (stale_after_days * 86400)
    with _lock:
        store = read_store()
        keep = {}
        for dev_id, meta in store.items():
            try:
                last_seen = datetime.fromisoformat(meta["last_seen_at"]).timestamp()
            except Exception:
                # Missing/malformed timestamp → treat as fresh, don't sweep
                last_seen = datetime.now(timezone.utc).timestamp()
            if last_seen > cutoff:
                keep[dev_id] = meta
        removed = len(store) - len(keep)
        if removed:
            _write_store_unlocked(keep)
            _sync_env_unlocked(keep)
        return removed
