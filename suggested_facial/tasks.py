"""Celery tasks for suggested-facial clustering.

The `suggested_faces` Docker container is one-shot: its entrypoint runs the
clustering pass over recent face embeddings and exits. Without a scheduler,
the "Frequently seen" panel in the app shows stale data — only refreshes
when the container is manually started or on hub boot.

This module exposes ``trigger_suggested_faces_run`` as a Celery beat task
so the clustering re-runs on a sensible cadence.
"""
import logging
import subprocess

from celery import shared_task


@shared_task
def trigger_suggested_faces_run():
    """Start the suggested_faces container so it runs one clustering pass.

    The container has restart_policy=on-failure, so a clean exit (the normal
    path) keeps it stopped until something explicitly starts it. We use
    ``docker compose start`` rather than ``up -d`` to avoid recreating the
    container — the existing one is fine, we just want its entrypoint to run.
    """
    compose_dir = "/root/jupyter-container"
    try:
        result = subprocess.run(
            ["docker", "compose", "start", "suggested_faces"],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logging.info("suggested_faces clustering pass triggered")
            return "ok"
        logging.error(
            f"suggested_faces start failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return f"failed: {result.stderr.strip()[:200]}"
    except subprocess.TimeoutExpired:
        logging.error("suggested_faces start timed out after 30s")
        return "timeout"
    except Exception as e:
        logging.exception(f"suggested_faces start raised: {e}")
        return f"exception: {e}"
