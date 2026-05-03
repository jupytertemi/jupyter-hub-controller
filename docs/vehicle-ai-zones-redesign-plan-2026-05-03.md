# Vehicle AI Zones — Redesign Plan

**Date**: 2026-05-03
**Author**: backend dev
**For**: Flutter dev to implement the app side; backend dev (me) to ship the schema + API + AI consumption side
**Branch**: `feat/vehicle-ai-zones-redesign` on both `jupytertemi/jupyter-hub-controller` and `jupytertemi/jupyter-app-rebuild`.

> **2026-05-03 — Final scope (locked)**
>
> Sections below were scoped down after iteration with Temi + Flutter dev. The implemented version is **the minimal plan that satisfies "force zone drawing first" plus multi-camera support**. Decoupled arrival/departure fields, parking_polygon rename, parallel endpoint, and legacy-flag system were all dropped as scope creep.
>
> **What's actually shipping:**
>
> - **Migration 0023** adds exactly two things:
>   - `Camera.vehicle_detection_zone: JSONField(null=True, blank=True)` (4-point quad)
>   - `CameraSetting.vehicle_recognition_cameras: ManyToManyField(Camera, ...)` (multi-camera selection)
> - Existing `VehicleCalibrationSerializer` extends to accept optional `detection_zone`. No new serializer.
> - Existing `CameraVehicleCalibrationView` checks the M2M (or legacy ForeignKey) for write authorization. No new endpoint.
> - `camera/templates/frigate_config.yml` adds a per-camera `zones.vehicle_detection_zone` block when the field is non-null. Existing `update_frigate_config` Celery render task fires on POST/DELETE.
> - VehicleAI's `state_detector.py::process_detection()` gates on `points-in-polygon(bbox_center, vehicle_detection_zone)` before any state-machine logic. If the field is null, no gate (legacy behavior preserved).
> - Toggle-OFF preserves the M2M (mirrors Loitering). Forward-only navigation across cameras in the wizard.
>
> **Existing fields kept untouched (NOT replaced):**
> - `vehicle_entry_point_x/y` — entry arrow tail position (legacy, still used)
> - `vehicle_approach_angle_deg` — entry arrow direction
> - `vehicle_park_polygon` — 4-point park rectangle
> - `vehicle_recognition_camera: ForeignKey` — kept for backwards compat alongside the new M2M
>
> **Earlier draft sections below describe the larger version of this plan that was scoped down.** Read for context if needed; the final shape is what's in this banner.

> **2026-05-03 — Additional Flutter requirements (locked):**
>
> 1. **Back-and-forth navigation must work cleanly within a camera's wizard.** Test matrix:
>    - Step 1 (zone) → Continue → Step 2 (arrow) → Back → Step 1: zone is preserved (not redrawn)
>    - Step 1 → Step 2 → Step 3 (park rect) → Back → Step 2: arrow position preserved
>    - Step 3 → Back → Back → Back: returns to camera picker / AI Engines, all in-flight state discarded with confirm dialog
>    - At Step 1 with a partially-drawn zone, Back asks "Discard zone progress?" before exiting
>    - Forward-navigation Continue buttons disabled until step requirements met (zone has 4 points, arrow placed, park rectangle has 4 points)
> 2. **No bypass on any step.** Detection zone (new step), arrow placement, and park rectangle are ALL required to advance. No Skip buttons. The Continue button stays disabled until the current step is complete. Save button on the confirm step is disabled until all three primitives are drawn.
> 3. **Deleting a zone disables vehicle recognition for that camera (backend cascade).** When the user resets a camera's zones from the wizard or AI Engines screen (DELETE `/cameras/<slug>/vehicle-calibration`), the backend now: clears all five vehicle fields; removes the camera from `vehicle_recognition_cameras` M2M and `vehicle_recognition_camera` FK; and if no cameras remain in either path, sets `license_vehicle_recognition=False`. Flutter UI must refetch `/camera-settings/<id>/` after a successful DELETE and reflect the disabled state if it was the last camera.
>
> Backend-side tests for the cascade are in `camera/tests/test_vehicle_calibration_view.py` (`test_delete_*` methods). Flutter-side tests should cover the back/forward state preservation matrix above + the post-delete UI refresh.

---

## Why we're doing this

Three problems with the current design:

1. **Detection zone is implicitly auto-derived** from the entry arrow + park rectangle. That conflates "where vehicle detection should run at all" with "where arrival/departure transitions happen." A user who wants vehicles detected across the full driveway but cares about a single arrival arrow ends up forced into a detection footprint shaped like the arrow + rectangle. Bad coupling.
2. **Single-camera-only restriction** (`CameraSetting.vehicle_recognition_camera` is a `ForeignKey`). Every other multi-zone AI in this codebase — Loitering — uses `ManyToManyField`. Vehicle AI is the outlier and there's no architectural reason for it.
3. **Wizard surfaces the wrong primitive first.** Users currently jump straight into placing the arrival/departure arrow without first declaring "this is the area I care about." That's the inversion of the natural mental model and forces re-runs every time the framing or arrow changes.

Fix: introduce an explicit **detection zone** as the foundation primitive, layer arrival/departure points + parking line on top, and lift the multi-camera restriction by mirroring the Loitering AI pattern.

---

## Current state (verified by reading the code)

### Backend — `jupyter-hub-controller`

**`camera/models.py`**
- `Camera` has 4 vehicle calibration fields, normalized 0-1:
  - `vehicle_entry_point_x: FloatField`
  - `vehicle_entry_point_y: FloatField`
  - `vehicle_approach_angle_deg: FloatField` (0-360 exclusive)
  - `vehicle_park_polygon: JSONField` (4-point axis-aligned rectangle, TL/TR/BR/BL)
- `CameraSetting` has:
  - `license_vehicle_recognition: BooleanField` (toggle)
  - `vehicle_recognition_camera: ForeignKey(Camera, ...)` ← **single-camera bottleneck**

For comparison, Loitering AI has both:
- `loitering_camera: ForeignKey` (legacy, kept for backwards-compat)
- `loitering_cameras: ManyToManyField` (current)

**`camera/serializers.py::VehicleCalibrationSerializer`**
- Validates `entry_point_x/y ∈ [0,1]`, `approach_angle_deg ∈ [0, 360)`, `park_polygon` is 4 points axis-aligned with positive width/height, entry point not inside park polygon.
- `from_camera()` / `apply_to_camera()` / `clear_on_camera()` methods.

**`camera/views.py::CameraVehicleCalibrationView`**
- `GET/POST/DELETE /cameras/<slug>/vehicle-calibration`
- POST guards on `license_vehicle_recognition=True AND vehicle_recognition_camera=this_camera`.

**Hub-side AI** (`jupytertemi/VehicleAI`):
- `state_detector.py` consumes the entry point + park polygon for state transitions (Approaching/Parked/Departing).

### Flutter — `jupyter-app-rebuild`

**`lib/src/modules/vehicle_calibration/presentation/screens/`**
- `vehicle_calibration_wizard.dart`: 4 steps — 0=picker, 1=arrow, 2=rectangle, 3=save.
- `step_d_arrow_placement.dart`: places entry point + rotates approach angle.
- `step_e_park_rectangle.dart`: draws the axis-aligned park rectangle.
- `step_f_validation.dart`: previews + submits to backend.
- `fullscreen_canvas_modal.dart`: shared canvas overlay.

**`ai_engines_screen.dart`**: toggles `license_vehicle_recognition` and lets the user pick **one** camera.

---

## Target state

### Decoupled primitives, per camera

| Primitive | Shape | Backend field | Required to enable VehicleAI for this camera? |
|---|---|---|---|
| **Detection zone** | 4-point quadrilateral, normalized 0-1, matches Parcel/Loitering schema | `vehicle_detection_zone: JSONField` | **Yes** — the foundation. No detection runs outside this. |
| **Arrival point** | Single point (x, y) normalized 0-1 | `vehicle_arrival_x`, `vehicle_arrival_y` | Optional (calibration). If absent, AI still detects but doesn't fire arrival/departure events. |
| **Departure point** | Single point (x, y) normalized 0-1 | `vehicle_departure_x`, `vehicle_departure_y` | Optional. Same as above. |
| **Parking polygon** | 4-point polygon (matches existing `vehicle_park_polygon` shape) | `vehicle_parking_polygon: JSONField` | Optional. User can draw thin/wide as desired — a thin rectangle visually reads as a "parking line." |

The current 4 fields (`vehicle_entry_point_*`, `vehicle_approach_angle_deg`, `vehicle_park_polygon`) become legacy. See migration section below.

### Multi-camera selection

Add `vehicle_recognition_cameras: ManyToManyField(Camera, related_name="vehicle_recognition_cameras_setting", blank=True)` to `CameraSetting`. Keep the legacy `vehicle_recognition_camera: ForeignKey` for backwards compat (write-through during transition window).

---

## Backend changes (I'll ship these)

### 1. Migration `0023_vehicle_zones_redesign.py`

```python
# camera/migrations/0023_vehicle_zones_redesign.py
class Migration(migrations.Migration):
    dependencies = [("camera", "0022_vehicle_calibration")]

    operations = [
        # Detection zone (foundation)
        migrations.AddField(
            model_name="camera",
            name="vehicle_detection_zone",
            field=models.JSONField(null=True, blank=True),
        ),
        # Arrival / departure points (decoupled from approach angle)
        migrations.AddField(
            model_name="camera",
            name="vehicle_arrival_x",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_arrival_y",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_departure_x",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_departure_y",
            field=models.FloatField(null=True, blank=True),
        ),
        # Parking line (replaces 4-point park polygon)
        migrations.AddField(
            model_name="camera",
            name="vehicle_parking_polygon",
            field=models.JSONField(null=True, blank=True),
        ),
        # Multi-camera M2M on CameraSetting (mirror Loitering)
        migrations.AddField(
            model_name="camerasetting",
            name="vehicle_recognition_cameras",
            field=models.ManyToManyField(
                blank=True,
                related_name="vehicle_recognition_cameras_setting",
                to="camera.camera",
            ),
        ),
        # NOTE: legacy fields (vehicle_entry_point_x/y, vehicle_approach_angle_deg,
        # vehicle_park_polygon, vehicle_recognition_camera) are kept this release.
        # Deprecation + removal happens in a follow-up migration once the rebuild
        # is fully on the new schema and the AI engine consumes only new fields.
    ]
```

### 2. New serializer `VehicleZonesSerializer` (replaces VehicleCalibrationSerializer)

```python
class VehicleZonesSerializer(serializers.Serializer):
    # Required: detection zone is the foundation. 4-point quadrilateral to match
    # Parcel/Loitering schema and the rebuild's existing camera_zone_screen.dart
    # gesture handling. Quads can't self-intersect with corner-ordered vertices,
    # so no simple-polygon check needed.
    detection_zone = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(min_value=0.0, max_value=1.0),
            min_length=2, max_length=2,
        ),
        min_length=4, max_length=4,
    )
    # Optional: arrival/departure (calibration)
    arrival_point = serializers.ListField(
        child=serializers.FloatField(min_value=0.0, max_value=1.0),
        min_length=2, max_length=2, required=False, allow_null=True,
    )
    departure_point = serializers.ListField(
        child=serializers.FloatField(min_value=0.0, max_value=1.0),
        min_length=2, max_length=2, required=False, allow_null=True,
    )
    # Optional: parking polygon. 4 points matching existing vehicle_park_polygon
    # shape. User can draw thin (line-like) or wide as desired.
    parking_polygon = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(min_value=0.0, max_value=1.0),
            min_length=2, max_length=2,
        ),
        min_length=4, max_length=4, required=False, allow_null=True,
    )

    def validate(self, attrs):
        # If arrival/departure/parking_polygon are provided, they must lie inside detection_zone.
        zone = attrs["detection_zone"]
        for key in ("arrival_point", "departure_point"):
            pt = attrs.get(key)
            if pt is not None and not _point_in_polygon(pt, zone):
                raise ValidationError(f"{key} must lie inside detection_zone.")
        pp = attrs.get("parking_polygon")
        if pp is not None:
            for pt in pp:
                if not _point_in_polygon(pt, zone):
                    raise ValidationError("parking_polygon points must lie inside detection_zone.")
        # Arrival ≠ departure (collapsed geometry guard)
        ap, dp = attrs.get("arrival_point"), attrs.get("departure_point")
        if ap and dp and ap == dp:
            raise ValidationError("arrival_point and departure_point cannot be identical.")
        return attrs

    @staticmethod
    def from_camera(camera): ...
    @staticmethod
    def apply_to_camera(camera, validated_data): ...
    @staticmethod
    def clear_on_camera(camera): ...
```

**Backwards compat read** — `VehicleZonesSerializer.from_camera(camera)` should return `None` if `vehicle_detection_zone` is null AND no legacy fields are set, OR a "legacy-converted" payload if only legacy fields are present (auto-derive a tight detection zone around the legacy entry+park, surfaced with `legacy: true` in the response). Lets the rebuild detect "this is a pre-redesign camera, prompt the user to redo zones."

### 3. View update — `CameraVehicleZonesView` (replaces CameraVehicleCalibrationView)

Same shape — `GET/POST/DELETE /cameras/<slug>/vehicle-zones`. The old endpoint stays alive for one release pointing at the same backing fields via `VehicleCalibrationSerializer` for any clients still on the old shape.

POST guard updates: must check the camera is in **either** `vehicle_recognition_camera` (legacy) **or** `vehicle_recognition_cameras` (new). Phase out the legacy single-camera check once rebuild is fully migrated.

### 4. Multi-camera toggle endpoint

Existing `POST /camera-settings/` with body `{"license_vehicle_recognition": true, "vehicle_recognition_cameras": [<slug1>, <slug2>, ...]}` should already work (DRF `PrimaryKeyRelatedField`/`SlugRelatedField` on the M2M). Confirm + add a test.

### 5. Frigate config integration (CW#172 — templates only)

The `detection_zone` polygon must propagate into Frigate's per-camera config so Frigate only forwards bounding boxes inside the zone. **Per CW#172 (golden rule): direct edits to `/root/jupyter-container/frigate/config/config.yaml` get overwritten on next render. All Frigate config changes go through `camera/templates/frigate_config.yml` → Celery render task.**

Two changes:

**a) Template update — `camera/templates/frigate_config.yml`**

Add a per-camera `zones` block when the camera has a `vehicle_detection_zone`:
```yaml
{% if camera.vehicle_detection_zone %}
zones:
  vehicle_detection_zone:
    coordinates: "{{ camera.vehicle_detection_zone | frigate_polygon_string }}"
    objects:
      - car
      - truck
      - motorcycle
{% endif %}
```

Frigate uses pixel coordinates (e.g. `0.4,0.7,0.5,0.7,0.5,0.8,...`), our DB stores normalized 0-1. Add a `frigate_polygon_string` Jinja filter (or template tag) that multiplies by camera frame width/height before joining. The filter lives in `camera/templatetags/frigate_filters.py`.

**b) Celery render trigger**

The `CameraVehicleZonesView.post()` and `delete()` must enqueue the existing Celery task that re-renders the Frigate template and reloads Frigate. Pattern already used elsewhere — copy from `loitering.tasks.update_loitering_config` invocation site. Task signature: `update_frigate_config.apply_async(queue="camera_queue")`.

The toggle endpoint (`PATCH /camera-settings/`) when adding/removing cameras from `vehicle_recognition_cameras` must also enqueue the same render task. Toggling OFF must emit a render that REMOVES the zone block from those cameras.

**c) MediaMTX side**

No changes needed. Vehicle zones don't affect streaming config. Confirmed by code reading — only object detection cares about zones, and that's Frigate-only.

### 6. AI consumption path (`VehicleAI/state_detector.py`)

This is the actual detection pipeline that needs to USE the zones, not just store them.

- **Detection zone gate**: `state_detector.py::process_detection()` reads `detection_zone` from the camera record (via the existing config GET hub→VehicleAI sync). At the start of every frame, run a points-in-polygon test on each detection's bbox center. If outside, drop the detection before any state-machine logic. Belt-and-suspenders given Frigate is already filtering — but defensive against template drift.
- **Arrival/departure logic**: replace the single-entry-point + angle logic with arrival/departure point pair when both present. Approach direction is computed from the arrival→departure vector. If only `arrival_point` is set (no departure), fall back to "vehicle entered detection zone from the arrival side." If neither set, no Approaching events fire (zone-only mode is valid — user just gets in-zone vs out-of-zone).
- **Parking line**: "vehicle parked" is now "vehicle bbox center is within ~50px of the parking line midpoint for ≥2 sec." Replaces the 4-point park polygon. If unset, no Parked events fire.
- **Backwards compat**: if the camera only has legacy fields (no new ones), fall through to the old logic. One config-source-of-truth path per camera at a time.

---

## API contract (for Flutter dev)

### `GET /cameras/<slug>/vehicle-zones` → 200

```json
{
  "detection_zone": [[x1,y1], [x2,y2], ..., [xn,yn]],
  "arrival_point": [x, y] | null,
  "departure_point": [x, y] | null,
  "parking_polygon": [[x1,y1], [x2,y2]] | null,
  "legacy": false
}
```

If only legacy fields present: `"legacy": true` with auto-derived `detection_zone` and `null` for the new optional fields.
If unset: 404 `{"detail": "Vehicle zones not set for this camera."}`.

### `POST /cameras/<slug>/vehicle-zones` → 200

Body: same shape as GET response (minus `legacy`). Returns the saved payload.

Errors:
- 400 if `detection_zone` self-intersects, has <3 or >12 points, or any point ∉ [0,1].
- 400 if any optional point/line is outside `detection_zone`.
- 400 if `arrival_point == departure_point`.
- 403 if `license_vehicle_recognition=False` or this camera isn't in the M2M.

### `DELETE /cameras/<slug>/vehicle-zones` → 204

Clears all six fields. Camera falls back to "no zones configured" (AI does no detection on this camera until reconfigured).

### `PATCH /camera-settings/<id>/` → 200

For toggling + updating multi-camera selection:
```json
{
  "license_vehicle_recognition": true,
  "vehicle_recognition_cameras": ["<slug1>", "<slug2>"]
}
```

### Snapshot fetch (unchanged)
`GET /cameras/<slug>/snapshot` — already 3-tier fallback (RTSP → Frigate → cached). Wizard uses this for the canvas background.

---

## Flutter changes (Flutter dev to implement)

### A. AI Engines screen — multi-camera selection

Currently single-select. Change to multi-select like Loitering's UI:
- `License & Vehicle recognition` toggle
- When ON: list of all RTSP/Ring cameras with checkboxes
- For each checked camera: secondary state showing "Configure zones →" button → launches the per-camera wizard
- Saved state: `vehicle_recognition_cameras` array in the PATCH payload

**Reference**: copy the loitering selection cell pattern; same widget structure, swap labels + the toggle's bound key.

### B. Wizard refactor — `vehicle_calibration_wizard.dart` → `vehicle_zones_wizard.dart`

Step structure changes from current 4 to **5** steps (per camera, looped if multiple cameras selected):

| Step | Title | Purpose | Required to advance |
|---|---|---|---|
| 0 | Intro / camera-in-flight indicator | "Configure vehicle detection for **<Camera Name>**" + thumbnail. If multi-camera, show "Camera 2 of 5" badge. | tap Continue |
| 1 | Draw detection zone | Tap to place 3-12 points to outline a simple polygon. Real-time validation: rejects self-intersecting polygons. Visual: filled translucent overlay. **Required.** | ≥3 points and not self-intersecting |
| 2 | Place arrival point | Tap once inside the detection zone to place the green arrival marker. **Optional** — Skip button advances to step 3. | None (skippable) |
| 3 | Place departure point | Tap once inside the detection zone to place the yellow departure marker. **Optional** — Skip button advances to step 4. | If arrival was placed, departure must differ |
| 4 | Draw parking line | Tap-drag to draw a 2-point line inside the detection zone. **Optional**. | Both endpoints inside detection_zone |
| 5 | Confirm + save | Show all overlays composited on the snapshot. Save → POST /vehicle-zones. On success: if more cameras to configure, advance to that camera's step 0; else exit. | Save tap |

**Multi-camera flow**: when user enters the wizard from AI Engines screen with N cameras checked, the wizard loops through them N times. Bottom-of-screen indicator shows progress: "Camera 2/5". Back button on step 0 of camera 2+ should return to that camera's selection state, NOT the prior camera's wizard (no rewind across cameras — confirm-and-move-on).

### C. New widget files (suggested layout)

```
lib/src/modules/vehicle_calibration/  →  rename to  vehicle_zones/
  presentation/screens/
    vehicle_zones_wizard.dart          (replaces vehicle_calibration_wizard)
    step_0_intro.dart                  (NEW)
    step_1_detection_zone.dart         (NEW — polygon drawing)
    step_2_arrival_point.dart          (replaces step_d_arrow_placement, simplified)
    step_3_departure_point.dart        (NEW — separate from arrival)
    step_4_parking_polygon.dart           (replaces step_e_park_rectangle)
    step_5_confirm.dart                (replaces step_f_validation)
    fullscreen_canvas_modal.dart       (shared, extend with polygon-drawing tools)
  data/models/
    vehicle_zones_model.dart           (replaces vehicle_calibration_model)
  data/datasources/
    vehicle_zones_remote_datasource.dart  (replaces vehicle_calibration_remote_datasource)
```

### D. State machine for the wizard cubit

```
enum WizardStep { intro, zone, arrival, departure, parking, confirm }

VehicleZonesState {
  cameraQueue: List<CameraEntity>
  cameraIndex: int
  currentStep: WizardStep
  detectionZone: List<Point>?
  arrivalPoint: Point?
  departurePoint: Point?
  parkingLine: (Point, Point)?
  isSaving: bool
  saveError: String?
}

// Transitions:
//   intro → zone     [continue tap]
//   zone → arrival   [≥3 valid points + continue tap]
//   arrival → departure  [tap inside zone OR skip]
//   departure → parking  [tap inside zone OR skip; if arrival placed, must differ]
//   parking → confirm  [valid line OR skip]
//   confirm → save → next-camera-intro (if more) | exit (if last)
//
//   ANY step → previous step  [back button, except cross-camera]
```

---

## Simulation & validation (the part you specifically asked for)

The Flutter dev should ship **integration tests** alongside the implementation that simulate every transition + every API call. Here's the test matrix.

### 1. API simulation tests (Dart, against a mock server)

For each test, assert the exact URL, method, headers, and body shape:

| Test | Simulates | Expected request | Expected response | Asserts |
|---|---|---|---|---|
| `multi_camera_toggle_on` | User flips toggle and selects 3 cameras | `PATCH /camera-settings/<id>` body `{license_vehicle_recognition: true, vehicle_recognition_cameras: [s1,s2,s3]}` | 200 | UI shows "Configure zones" button per camera |
| `multi_camera_toggle_off` | User flips toggle off | `PATCH /camera-settings/<id>` body `{license_vehicle_recognition: false}` | 200 | Wizard entry points hidden |
| `fetch_zones_unset` | First-time wizard for a camera | `GET /cameras/<slug>/vehicle-zones` | 404 | Wizard initializes empty state |
| `fetch_zones_existing` | Re-edit pre-configured zones | `GET /cameras/<slug>/vehicle-zones` | 200 with payload | Wizard pre-populates from response |
| `fetch_zones_legacy` | Camera with only old fields | `GET /cameras/<slug>/vehicle-zones` | 200 with `legacy: true` | Wizard shows "These zones are from an older version, please re-confirm" UX |
| `save_zones_full` | All optional fields filled | `POST /cameras/<slug>/vehicle-zones` body has zone+arrival+departure+line | 200 | Cubit emits saved state |
| `save_zones_minimal` | Only detection zone, no optional | `POST` body has only `detection_zone` | 200 | Save succeeds |
| `save_zones_self_intersecting` | Polygon crosses itself | `POST` | 400 | Wizard surfaces error, returns to step 1 |
| `save_zones_arrival_outside` | Arrival point outside detection zone | `POST` | 400 | Wizard returns to step 2 with error |
| `save_zones_arrival_eq_departure` | Both points identical | `POST` | 400 | Wizard returns to step 3 with error |
| `delete_zones` | User taps "Reset zones" | `DELETE /cameras/<slug>/vehicle-zones` | 204 | UI clears overlays |
| `snapshot_fetch_success` | Wizard step 0 loads snapshot | `GET /cameras/<slug>/snapshot` | 200 jpg bytes | Canvas background renders |
| `snapshot_fetch_404` | Camera offline | `GET /cameras/<slug>/snapshot` | 404 | Wizard shows "Camera offline — using last known image" or fallback message |

### 2. Wizard navigation tests (`flutter_test` widget tests)

For each test, drive the widget tree through taps + assertions:

| Test | Steps | Asserts |
|---|---|---|
| `fwd_full_path` | Tap through all 5 steps with valid input | Final step displays composited overlay; Save button enabled |
| `fwd_skip_optional` | Skip arrival, departure, parking | Confirm step shows zone only; Save still enabled |
| `back_within_camera` | At step 3, tap Back twice | Returns to step 1 with detection zone preserved |
| `back_at_step_0` | At step 0 of camera 1, tap Back | Returns to AI Engines screen |
| `back_at_step_0_camera_2` | At step 0 of camera 2 (multi-camera), tap Back | Confirms "Skip remaining cameras?" dialog |
| `multi_camera_progress_indicator` | 3 cameras checked, advance through camera 1 | Progress shows "Camera 1/3" → "Camera 2/3" after first save |
| `multi_camera_save_failure` | Camera 2 save returns 400 | Wizard shows error, stays on camera 2 confirm step, doesn't skip to camera 3 |
| `polygon_self_intersect_realtime` | Place 4 points forming a bowtie | Continue button disabled, error tooltip shown |
| `polygon_min_3_points` | Place 2 points | Continue button disabled |
| `polygon_max_12_points` | Try to place 13th point | Tap is rejected, max-reached message shown |
| `arrival_outside_zone_realtime` | Place arrival point outside polygon | Tap is rejected, "Place inside the detection zone" message shown |
| `parking_polygon_drag` | Drag from point A to point B inside zone | Two-endpoint line rendered |
| `parking_polygon_endpoint_outside` | Drag with endpoint outside zone | Drag clamps to zone edge OR rejects |

### 3. End-to-end smoke (manual but documented)

After Flutter + backend ship, the Flutter dev runs this sequence on a real hub (Mill-Valley) with at least 2 cameras and verifies each step:

1. Toggle Vehicle AI ON in AI Engines.
2. Select 2 cameras.
3. Configure camera 1 zones with all 4 primitives.
4. Confirm Mill-Valley `/cameras/<slug1>/vehicle-zones` GET returns the saved payload.
5. Configure camera 2 zones with detection zone only (skip optional).
6. Confirm `/cameras/<slug2>/vehicle-zones` GET returns the saved payload with `null` optional fields.
7. Drive a vehicle past camera 1's detection zone — confirm event fires.
8. Drive a vehicle outside camera 1's detection zone — confirm NO event fires.
9. Toggle Vehicle AI OFF.
10. Confirm `/camera-settings/<id>/` GET returns `license_vehicle_recognition=false` and the M2M is cleared OR the cameras are still in M2M but disabled (decide which behavior is intended; document it).

### 4. Backend tests (I'll ship these alongside the migration)

`camera/tests/test_vehicle_zones_serializer.py` — pure-Python validation:
- detection zone shape: 3-12 points, self-intersecting rejected
- arrival/departure inside zone constraint
- collapsed geometry (arrival == departure) rejected
- legacy-conversion path returns `legacy: true` with auto-derived zone
- empty / null optional fields accepted

`camera/tests/test_vehicle_zones_view.py` — integration:
- GET 404 on unset camera
- GET 200 with new schema
- GET 200 with `legacy: true` for cameras with only old fields
- POST 200 happy path
- POST 400 for each validation failure mode
- POST 403 if camera not in M2M
- DELETE 204 clears all 6 fields
- M2M PATCH on `/camera-settings/` works for adding + removing cameras

### 5. DB-commit + Frigate-render + AI-pipeline tests (the chain you specifically asked for)

The above tests verify the API works. These additional tests verify the **zones actually flow into the database, into Frigate's config, and into the detection pipeline**. The whole point of the wizard is moot if the zones stop at the API layer.

#### 5a. DB commit verification (`test_vehicle_zones_db_commit.py`)

For each POST in the integration tests, also verify:
| Test | After POST returns 200 | Asserts |
|---|---|---|
| `db_commit_full_payload` | Re-fetch the Camera row from DB | `camera.vehicle_detection_zone` matches the polygon sent, `vehicle_arrival_x/y` matches arrival, `vehicle_departure_x/y` matches departure, `vehicle_parking_polygon` matches line — byte-equal, not loosely equal |
| `db_commit_partial_payload` | POST with only `detection_zone`, re-fetch row | Optional fields are NULL in DB (not empty string, not `[]`, not `0.0`) |
| `db_commit_overwrites_existing` | POST a payload, then POST a different payload, re-fetch | Second payload is the only one persisted; first is gone |
| `db_commit_delete_clears_all_six` | POST, then DELETE, re-fetch | All six new fields are NULL; M2M membership unchanged |
| `db_commit_m2m_membership` | PATCH `/camera-settings/` with cameras [s1,s2], re-fetch | `setting.vehicle_recognition_cameras.all()` returns exactly {s1,s2}; toggling off and back on preserves order |
| `db_commit_legacy_isolation` | POST new-schema payload to a camera with legacy fields set | Legacy fields untouched (no inadvertent clear); new fields populated alongside |

These are 6 tests, all using Django's TestCase + ORM round-trip. ~50 lines each.

#### 5b. Frigate config render verification (`test_vehicle_zones_frigate_render.py`)

The Celery render task writes the rendered Frigate config to `/root/jupyter-container/frigate/config/config.yaml` (template source: `camera/templates/frigate_config.yml`). Tests must verify the rendered file actually contains the zone:

| Test | Setup | Assert |
|---|---|---|
| `render_includes_zone_block` | Camera with `vehicle_detection_zone=[[0.1,0.1],[0.9,0.1],...]`, fire `update_frigate_config.apply()` (synchronous in test) | Rendered YAML has `zones.vehicle_detection_zone.coordinates` = pixel-space polygon string for that camera |
| `render_omits_zone_when_unset` | Camera with `vehicle_detection_zone=None` | Rendered YAML has no `zones.vehicle_detection_zone` for that camera |
| `render_normalized_to_pixel` | Camera with frame 1280x720 and zone `[[0.1,0.1],[0.9,0.9]]` | Rendered coordinates string equals `"128,72,1152,648"` (or the zone's polygon coordinates string format Frigate expects) |
| `render_multiple_cameras` | 3 cameras, each with a different zone | Rendered YAML has 3 zone blocks under the correct camera keys, no cross-talk |
| `render_after_delete` | Camera had a zone, DELETE called, render fires | Rendered YAML no longer has the zone block; no orphan reference |
| `render_after_m2m_remove` | Camera removed from `vehicle_recognition_cameras` (toggle-off path) | Render fires, zone block gone for that camera, retained for cameras still in M2M |
| `render_celery_task_enqueued` | POST /vehicle-zones | Celery task `update_frigate_config` is enqueued (assert via `apply_async` mock or direct broker inspection) |

Note: this is integration-test territory — needs a running test Frigate-config-output path or a test-doubled write target. Use `override_settings(FRIGATE_CONFIG_PATH="/tmp/test_frigate_config.yml")` to keep tests hermetic.

#### 5c. Detection pipeline gate verification (`test_vehicle_zones_pipeline_gate.py`)

The detection pipeline must actually USE the zone — not just be configured with it. Two layers to test, since both Frigate and VehicleAI act as filters:

**Frigate-layer gate** (object detection):
| Test | Setup | Assert |
|---|---|---|
| `frigate_drops_detection_outside_zone` | Synthetic Frigate event for a vehicle bbox outside the zone | Event is NOT forwarded to MQTT `frigate/events` (verified by subscribing to broker in test) |
| `frigate_forwards_detection_inside_zone` | Synthetic event for a vehicle bbox inside the zone | Event IS forwarded |

These need a running Frigate-doubled-with-test-rules or a unit test against the Frigate zone-evaluation config. If hard to integration-test, settle for a config-correctness test: parse the rendered YAML, run the zone polygon through Frigate's documented in-zone rule, assert in/out behavior matches expectation.

**VehicleAI-layer gate** (state detector):
| Test | Setup | Assert |
|---|---|---|
| `state_detector_drops_outside_zone` | Mock detection event with bbox center outside `detection_zone` | `state_detector.process_detection()` returns early; no state transition fires; no MQTT publish to `/events` |
| `state_detector_processes_inside_zone` | Same camera, bbox center inside zone | State machine runs normally; transition fires if applicable |
| `state_detector_no_zone_no_gate` | Camera has no `detection_zone` (legacy path) | All detections processed (don't accidentally drop everything for legacy cameras) |
| `state_detector_arrival_only` | Zone + arrival point set, no departure | Approaching events fire, Departing events don't |
| `state_detector_full_calibration` | Zone + arrival + departure + parking line | Full state machine: Approaching → Parked (when bbox center near parking line) → Departing → Departed |
| `state_detector_parking_polygon_50px_threshold` | Vehicle bbox within 50px of parking line midpoint for ≥2 sec | Parked event fires |
| `state_detector_parking_polygon_far` | Vehicle bbox 100px from parking line | Parked event does NOT fire |

These are unit tests in the VehicleAI repo (`jupytertemi/VehicleAI/tests/`), using replay fixtures from production events.

#### 5d. End-to-end smoke (the closure proof)

Single Mill-Valley test, manually executed by the Flutter dev or me, screen-recorded as evidence:

1. Configure zone on Mill-Valley camera "front-driveway" via the new wizard (multi-camera with at least 2 cameras).
2. Verify in DB: `Camera.objects.get(slug_name="front-driveway").vehicle_detection_zone` returns the polygon.
3. Verify Frigate config: `cat /root/jupyter-container/frigate/config/config.yaml | grep -A 5 vehicle_detection_zone` shows the rendered zone block.
4. Verify Frigate sees it: `docker logs frigate | grep -i "loaded zone"` shows the zone loaded after config reload.
5. Drive a vehicle through the zone → MQTT `frigate/events` fires with the vehicle.
6. Drive a vehicle outside the zone → No MQTT event.
7. Verify VehicleAI: `docker logs vehicle_detection | grep -E "in_zone|approaching|parked"` shows zone-aware processing.
8. Verify event end-to-end: cloud event log shows the vehicle event with correct camera + zone.
9. Repeat for camera #2 with a different zone shape — confirm both cameras behave independently.

Without all of 5a + 5b + 5c + 5d passing, the feature isn't shippable. The API tests prove correctness in isolation; these tests prove the zones reach the place that actually matters.

---

## Migration path (existing data)

There's only one camera in the wild today with vehicle calibration set: Mill-Valley's calibrated camera (Phase 1 work, task #57). For that camera, the legacy fields stay populated until the user re-runs the new wizard. The serializer's `legacy: true` flag tells the rebuild to surface a "please reconfigure" prompt.

No data migration script needed — the new fields are additive.

Once the rebuild is on the new schema and the AI engine consumes only the new fields (verifiable via fleet telemetry), a follow-up migration drops the 4 legacy `vehicle_*` fields and the legacy `vehicle_recognition_camera` ForeignKey.

---

## Acceptance criteria

Backend (mine):
- [ ] Migration 0023 applies cleanly on Mill-Valley + a fresh hub.
- [ ] `VehicleZonesSerializer` tests: 100% green, ≥15 cases.
- [ ] `CameraVehicleZonesView` tests: 100% green, ≥10 cases.
- [ ] Legacy `CameraVehicleCalibrationView` still serves the old endpoint shape.
- [ ] M2M PATCH on `/camera-settings/` works for vehicle.
- [ ] AI engine reads new fields when present, falls through to legacy when not.

Flutter (yours):
- [ ] Multi-camera selection in AI Engines screen.
- [ ] 5-step wizard implemented per the state machine.
- [ ] All 13 API simulation tests pass.
- [ ] All 12 widget navigation tests pass.
- [ ] End-to-end smoke (10 steps) executed on Mill-Valley with at least 2 cameras and screen-recorded.

Cross-cutting:
- [ ] Feat branches on both repos. PR descriptions reference this plan.
- [ ] No regressions in Loitering AI multi-camera flow (it's the precedent we're copying — make sure the copy doesn't break the source).
- [ ] AI event firing: drive a vehicle in-zone fires an event, out-of-zone does not.

---

## Sequencing (proposed)

1. **Day 1 (mine)**: Migration 0023 + serializer + view + view tests. PR up for review.
2. **Day 1 (yours)**: AI Engines multi-camera UI (no wizard refactor yet). PR up for review.
3. **Day 2 (yours)**: Wizard refactor steps 0-2 (intro, detection zone, arrival). API simulation tests for those steps.
4. **Day 3 (yours)**: Wizard steps 3-5 (departure, parking line, confirm). Remaining API simulation tests + widget navigation tests.
5. **Day 3 (mine)**: VehicleAI engine consumption update. Replay-against-bench validation.
6. **Day 4**: End-to-end smoke on Mill-Valley with both Flutter + backend deployed. Screen-record. Both PRs merge.

If we want to parallelize tighter: I can ship migration 0023 + serializer + view in a single backend PR by EOD today, you can stub against the API contract immediately (mock responses) and start the wizard refactor while I patch the AI engine in parallel.

---

## Open questions for you (Temi)

1. **Toggle-OFF behavior**: when user toggles Vehicle AI off, should the M2M clear, or should it preserve the camera list so toggling back ON keeps the same set? (Loitering preserves; recommend matching.)
2. ~~Detection zone cap~~ — RESOLVED 2026-05-03 per Flutter dev feedback: fixed 4-point quad matching Parcel/Loitering schema. No simple-polygon check needed.
3. ~~Parking line orientation~~ — RESOLVED: stays 4-point polygon (`vehicle_parking_polygon`) matching existing schema. User can draw thin/wide as desired.
4. **Multi-camera back navigation**: currently spec says "no rewind across cameras." Confirm or change.
5. **Existing Mill-Valley calibration**: blow away on first wizard launch, or keep for now and prompt user to confirm? (Lean: keep with `legacy: true`; user reconfigures when ready.)

Once those five are answered I'll cut migration 0023 and the serializer.
