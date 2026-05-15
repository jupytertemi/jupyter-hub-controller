"""MotionIQ profile definitions. Per-camera Frigate sensitivity tuning.

The ``Camera`` model exposes ``camera.motion_settings`` which the Frigate
config template renders into ``motion``, ``detect``, ``objects.filters``,
and ``review`` blocks of ``config.yaml``.
"""

GUARDIAN = "guardian"
AWARE = "aware"
QUIET = "quiet"

DEFAULT_PROFILE = AWARE

MOTION_PROFILES = {
    GUARDIAN: {
        "label": "Guardian",
        "description": "Maximum vigilance. Catches all motion.",
        "motion": {
            "threshold": 25, "contour_area": 15, "frame_alpha": 0.008,
            "frame_height": 100, "improve_contrast": True,
            "lightning_threshold": 0.9, "mqtt_off_delay": 30,
        },
        "detect": {
            "fps": 5, "max_disappeared": 25,
            "stationary": {"threshold": 50, "interval": 50, "max_frames": 3000},
        },
        "objects": {
            "person": {"min_score": 0.5, "threshold": 0.7, "min_area": 3000},
            "car": {"min_score": 0.6, "threshold": 0.7, "min_area": 10000},
            "dog": {"min_score": 0.5, "threshold": 0.65, "min_area": 2000},
            "cat": {"min_score": 0.5, "threshold": 0.65, "min_area": 1000},
            "bird": {"min_score": 0.45, "threshold": 0.6, "min_area": 500},
        },
        "review": {
            "alerts_labels": ["person", "car", "dog", "cat"],
            "detections_labels": ["person", "car", "dog", "cat", "bird"],
        },
    },
    AWARE: {
        "label": "Aware",
        "description": "Balanced. Default for daily use.",
        "motion": {
            "threshold": 30, "contour_area": 25, "frame_alpha": 0.01,
            "frame_height": 100, "improve_contrast": True,
            "lightning_threshold": 0.8, "mqtt_off_delay": 30,
        },
        "detect": {
            "fps": 5, "max_disappeared": 25,
            "stationary": {"threshold": 30, "interval": 50, "max_frames": 2000},
        },
        "objects": {
            "person": {"min_score": 0.55, "threshold": 0.7, "min_area": 5000},
            "car": {"min_score": 0.65, "threshold": 0.7, "min_area": 15000},
            "dog": {"min_score": 0.55, "threshold": 0.7, "min_area": 3000},
            "cat": {"min_score": 0.55, "threshold": 0.7, "min_area": 1500},
            "bird": {"min_score": 0.5, "threshold": 0.65, "min_area": 800},
        },
        "review": {
            "alerts_labels": ["person", "car", "dog", "cat"],
            "detections_labels": ["person", "car", "dog", "cat", "bird"],
        },
    },
    QUIET: {
        "label": "Quiet",
        "description": "Essential alerts only. Ignores idle subjects.",
        "motion": {
            "threshold": 40, "contour_area": 50, "frame_alpha": 0.02,
            "frame_height": 100, "improve_contrast": True,
            "lightning_threshold": 0.8, "mqtt_off_delay": 30,
        },
        "detect": {
            "fps": 5, "max_disappeared": 25,
            "stationary": {"threshold": 15, "interval": 50, "max_frames": 1500},
        },
        "objects": {
            "person": {"min_score": 0.6, "threshold": 0.75, "min_area": 8000},
            "car": {"min_score": 0.7, "threshold": 0.75, "min_area": 20000},
            "dog": {"min_score": 0.6, "threshold": 0.75, "min_area": 5000},
            "cat": {"min_score": 0.6, "threshold": 0.75, "min_area": 3000},
            "bird": {"min_score": 0.55, "threshold": 0.7, "min_area": 1500},
        },
        "review": {
            "alerts_labels": ["person", "car"],
            "detections_labels": ["person", "car", "dog", "cat"],
        },
    },
}


def list_profiles():
    """Return [{id, label, description, default}] in UI order."""
    return [
        {
            "id": pid,
            "label": MOTION_PROFILES[pid]["label"],
            "description": MOTION_PROFILES[pid]["description"],
            "default": pid == DEFAULT_PROFILE,
        }
        for pid in (GUARDIAN, AWARE, QUIET)
    ]
