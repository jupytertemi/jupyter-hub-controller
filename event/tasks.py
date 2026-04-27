import logging
import os
import tempfile
from datetime import timedelta

import requests
from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from event.models import Event

logger = logging.getLogger(__name__)

# Host-side path where Django/Celery can write. Container path is what the
# transcoder watches and what the serializer rewrites to a client URL.
CLIPS_DIR_HOST = os.getenv(
    "CLIPS_DIR_HOST", "/root/jupyter-container/frigate/storage/clips"
)
CLIPS_DIR_CONTAINER = os.getenv("CLIPS_DIR_CONTAINER", "/media/frigate/clips")
FRIGATE_URL = os.getenv("FRIGATE_URL", "http://127.0.0.1:5000")

DOWNLOAD_TIMEOUT = 30
MIN_CLIP_BYTES = 4096
BACKOFF_SECONDS = [10, 30, 90, 300, 600]  # ~17min total window
SCAN_WINDOW_MINUTES = 30
DEDUP_TTL_SECONDS = 1800


def _is_broken_path(p):
    if not p:
        return True
    return p.startswith("http://frigate:5000/")


@shared_task(bind=True, ignore_result=True)
def scan_broken_video_paths(self):
    """Beat: scan recent events with empty/frigate_api video_path, enqueue fix."""
    cutoff = timezone.now() - timedelta(minutes=SCAN_WINDOW_MINUTES)
    qs = (
        Event.objects.filter(created_at__gte=cutoff)
        .filter(Q(video_path="") | Q(video_path__startswith="http://frigate:5000/"))
        .values_list("event_id", flat=True)[:100]
    )
    enqueued = 0
    for event_id in qs:
        if not event_id:
            continue
        lock_key = f"ensure_clip:{event_id}"
        if cache.add(lock_key, "1", DEDUP_TTL_SECONDS):
            ensure_local_clip_task.apply_async(args=[event_id], queue="hub_operations_queue")
            enqueued += 1
    if enqueued:
        logger.info(f"scan_broken_video_paths enqueued={enqueued}")


@shared_task(bind=True, ignore_result=True)
def ensure_local_clip_task(self, event_id):
    """Download Frigate clip to host clips dir; update event.video_path to
    container-visible path so clip_transcoder picks it up and serializer
    rewrites it correctly."""
    try:
        event = Event.objects.get(event_id=event_id)
    except Event.DoesNotExist:
        return

    if event.video_path.startswith(CLIPS_DIR_CONTAINER):
        return  # already local

    os.makedirs(CLIPS_DIR_HOST, exist_ok=True)
    target_host = os.path.join(CLIPS_DIR_HOST, f"event_{event_id}.mp4")
    target_container = f"{CLIPS_DIR_CONTAINER}/event_{event_id}.mp4"

    if os.path.exists(target_host) and os.path.getsize(target_host) >= MIN_CLIP_BYTES:
        Event.objects.filter(event_id=event_id).update(video_path=target_container)
        return

    url = f"{FRIGATE_URL}/api/events/{event_id}/clip.mp4"
    tmp_path = None
    try:
        with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as r:
            if r.status_code == 404:
                raise RuntimeError("frigate clip not ready (404)")
            r.raise_for_status()
            fd, tmp_path = tempfile.mkstemp(
                dir=CLIPS_DIR_HOST, prefix=f"event_{event_id}.", suffix=".mp4.part"
            )
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        if os.path.getsize(tmp_path) < MIN_CLIP_BYTES:
            raise RuntimeError(f"clip too small: {os.path.getsize(tmp_path)} bytes")
        os.replace(tmp_path, target_host)
        tmp_path = None
        Event.objects.filter(event_id=event_id).update(video_path=target_container)
        logger.info(
            f"ensure_local_clip saved event_id={event_id} "
            f"size={os.path.getsize(target_host)}"
        )
    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        attempt = self.request.retries
        if attempt < len(BACKOFF_SECONDS):
            countdown = BACKOFF_SECONDS[attempt]
            logger.warning(
                f"ensure_local_clip retry {attempt + 1}/{len(BACKOFF_SECONDS)} "
                f"in {countdown}s event_id={event_id}: {exc}"
            )
            raise self.retry(exc=exc, countdown=countdown, max_retries=len(BACKOFF_SECONDS))
        logger.error(f"ensure_local_clip giving up event_id={event_id}: {exc}")
        # Clear dedup lock so next scan can try again later (e.g. after long delay)
        cache.delete(f"ensure_clip:{event_id}")
