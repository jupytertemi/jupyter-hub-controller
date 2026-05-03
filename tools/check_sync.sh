#!/usr/bin/env bash
# tools/check_sync.sh — pre-deploy SHA sync gate for the vehicle-zones feature.
#
# Verifies that every file we changed has the same SHA256 across all the
# locations that matter:
#
#   * Local working copy on the dev's laptop
#   * GitHub branch tip (verified indirectly: local up-to-date with origin)
#   * Hub host filesystem (/root/jupyter-hub-controller, /root/jupyter-container/...)
#   * Running container (docker exec ... sha256sum)
#   * Image-baked layer (docker run --rm --entrypoint sha256sum <image> ...)
#
# Drift in any column means a deploy will silently regress when the next
# image rebuild / restart / pull / gold-image snapshot happens. Fix the drift
# BEFORE shipping. Output is a one-line-per-file table with PASS/FAIL.
#
# Usage:
#   tools/check_sync.sh                        # default: feat/vehicle-ai-zones-redesign + Mill-Valley
#   HUB_HOST=192.168.1.119 tools/check_sync.sh # different hub
#   FEATURE=halo-onboard tools/check_sync.sh   # different feature inventory (future)

set -uo pipefail

HUB_HOST="${HUB_HOST:-192.168.1.161}"
HUB_PASSWORD="${HUB_PASSWORD:-jupyter2026}"
FEATURE="${FEATURE:-vehicle-zones}"

LOCAL_HUB_REPO="${LOCAL_HUB_REPO:-/Users/topsycombs/jupytertemi/jupyter-hub-controller}"
LOCAL_VEHICLE_REPO="${LOCAL_VEHICLE_REPO:-/Users/topsycombs/jupytertemi/VehicleAI}"

ssh_run() {
  sshpass -p "$HUB_PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
    "root@${HUB_HOST}" "$@"
}

# --- helpers ---------------------------------------------------------------

local_sha() {
  # $1 = absolute path
  if [ -f "$1" ]; then
    shasum -a 256 "$1" | awk '{print substr($1,1,12)}'
  else
    echo "MISSING"
  fi
}

hub_host_sha() {
  # $1 = absolute path on hub
  ssh_run "test -f '$1' && sha256sum '$1' | awk '{print substr(\$1,1,12)}' || echo MISSING"
}

container_sha() {
  # $1 = container name, $2 = path inside container
  ssh_run "docker exec '$1' sh -c 'test -f \"$2\" && sha256sum \"$2\" | awk \"{print substr(\\\$1,1,12)}\" || echo MISSING' 2>/dev/null"
}

image_sha() {
  # $1 = image ref, $2 = path inside image
  ssh_run "docker run --rm --entrypoint sha256sum '$1' '$2' 2>/dev/null | awk '{print substr(\$1,1,12)}' || echo MISSING"
}

git_synced_with_remote() {
  # $1 = repo path. Returns 'yes' if the local branch has been pushed and the
  # remote tracking branch matches. (Doesn't fetch — assumes recent push.)
  local repo="$1"
  cd "$repo" || return 1
  local branch ahead behind
  branch=$(git symbolic-ref --short HEAD)
  ahead=$(git rev-list --count "@{u}..HEAD" 2>/dev/null || echo "?")
  behind=$(git rev-list --count "HEAD..@{u}" 2>/dev/null || echo "?")
  if [ "$ahead" = "0" ] && [ "$behind" = "0" ]; then
    echo "synced"
  else
    echo "ahead=$ahead behind=$behind"
  fi
}

# Color (only when stdout is a tty)
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; RESET=$'\033[0m'
else
  GREEN=""; RED=""; YELLOW=""; RESET=""
fi

verdict() {
  # Compares all non-empty args. PASS if all equal AND none MISSING; FAIL otherwise.
  local first="$1"; shift
  if [ "$first" = "MISSING" ]; then
    echo "${RED}FAIL${RESET}"
    return
  fi
  for s in "$@"; do
    [ -z "$s" ] && continue
    if [ "$s" = "MISSING" ] || [ "$s" != "$first" ]; then
      echo "${RED}FAIL${RESET}"
      return
    fi
  done
  echo "${GREEN}PASS${RESET}"
}

# --- file inventories ------------------------------------------------------

# Hub-controller files (host-deployed, no container, no image)
HUB_CONTROLLER_FILES=(
  "camera/views.py"
  "camera/serializers.py"
  "camera/tasks.py"
  "camera/models.py"
  "camera/migrations/0023_vehicle_detection_zone_and_m2m.py"
)

# VehicleAI files (containerized via bind-mount today; should be image-baked)
VEHICLE_AI_FILES=(
  "constants.py"
  "main_vehicle.py"
  "state_detector.py"
  "zone_gate.py"
)
VEHICLE_CONTAINER="number_plate_detection"
# Local-only namespace under jupytertemi/. NEVER ghcr.io/jupyter-hub/... — that
# was a wrong-namespace + broken-registry-pull pattern from earlier deploys.
# Local builds only; sync source of truth is github.com/jupytertemi/VehicleAI.
VEHICLE_IMAGE_REF="jupytertemi/pilot_vehicle_ai:dev"

# FaceAI (face_recognition container) — baked 2026-05-03 same playbook
LOCAL_FACE_REPO="${LOCAL_FACE_REPO:-/Users/topsycombs/jupytertemi/FaceAI}"
FACE_AI_FILES=(
  "npu_yield_listener.py"
  "constants.py"
  "database.py"
  "shared_enhance.py"
  "improved_matcher.py"
  "face_analysis/face_analysis.py"
)
FACE_CONTAINER="face_recognition"
FACE_IMAGE_REF="jupytertemi/pilot_face_recognition_ai:dev"

# --- run ------------------------------------------------------------------

echo "================================================================"
echo "Pre-deploy SHA sync gate — feature=${FEATURE} hub=${HUB_HOST}"
echo "================================================================"
echo

# Section 1: git-vs-origin coherence
echo "## Git vs remote tracking branch"
echo
printf "  %-50s %s\n" "$LOCAL_HUB_REPO" "$(git_synced_with_remote "$LOCAL_HUB_REPO")"
printf "  %-50s %s\n" "$LOCAL_VEHICLE_REPO" "$(git_synced_with_remote "$LOCAL_VEHICLE_REPO")"
echo
echo "(synced = local SHA matches GitHub SHA. Anything else means the gate"
echo " below compares against not-yet-pushed local state — push first.)"
echo

# Section 2: hub-controller files
echo "## hub-controller (host-deployed, no container)"
echo
printf "  %-50s %-12s %-12s %s\n" "FILE" "LOCAL" "HUB-HOST" "VERDICT"
for f in "${HUB_CONTROLLER_FILES[@]}"; do
  l=$(local_sha "$LOCAL_HUB_REPO/$f")
  h=$(hub_host_sha "/root/jupyter-hub-controller/$f")
  v=$(verdict "$l" "$h")
  printf "  %-50s %-12s %-12s %s\n" "$f" "$l" "$h" "$v"
done
echo

# Section 3: VehicleAI files
echo "## VehicleAI (containerized — image-baked, no bind-mount)"
echo
printf "  %-32s %-12s %-12s %-12s %-12s %s\n" "FILE" "LOCAL" "HUB-HOST" "CONTAINER" "IMAGE-BAKED" "VERDICT"
for f in "${VEHICLE_AI_FILES[@]}"; do
  l=$(local_sha "$LOCAL_VEHICLE_REPO/$f")
  h=$(hub_host_sha "/root/jupyter-container/pilot_vehicle_ai/$f")
  c=$(container_sha "$VEHICLE_CONTAINER" "/usr/src/app/$f")
  i=$(image_sha "$VEHICLE_IMAGE_REF" "/usr/src/app/$f")
  v=$(verdict "$l" "$h" "$c" "$i")
  printf "  %-32s %-12s %-12s %-12s %-12s %s\n" "$f" "$l" "$h" "$c" "$i" "$v"
done
echo

# Section 3b: FaceAI files
echo "## FaceAI (face_recognition container — image-baked, no bind-mount)"
echo
printf "  %-32s %-12s %-12s %-12s %-12s %s\n" "FILE" "LOCAL" "HUB-HOST" "CONTAINER" "IMAGE-BAKED" "VERDICT"
for f in "${FACE_AI_FILES[@]}"; do
  l=$(local_sha "$LOCAL_FACE_REPO/$f")
  h=$(hub_host_sha "/root/jupyter-container/pilot_face_recognition_ai/$f")
  c=$(container_sha "$FACE_CONTAINER" "/usr/src/app/$f")
  i=$(image_sha "$FACE_IMAGE_REF" "/usr/src/app/$f")
  v=$(verdict "$l" "$h" "$c" "$i")
  printf "  %-32s %-12s %-12s %-12s %-12s %s\n" "$f" "$l" "$h" "$c" "$i" "$v"
done
echo

# Section 4: DB migration applied
echo "## hub Postgres — migration 0023 applied?"
echo
applied=$(ssh_run "docker exec postgres psql -U postgres -d hub_controller -tAc \"SELECT name FROM django_migrations WHERE app='camera' AND name='0023_vehicle_detection_zone_and_m2m'\"" 2>/dev/null)
if [ -n "$applied" ]; then
  echo "  ${GREEN}PASS${RESET}  $applied"
else
  echo "  ${RED}FAIL${RESET}  migration 0023 NOT in django_migrations"
fi
echo

# Section 5: column / table existence
echo "## hub Postgres — schema objects"
echo
col_exists=$(ssh_run "docker exec postgres psql -U postgres -d hub_controller -tAc \"SELECT 1 FROM information_schema.columns WHERE table_name='camera_camera' AND column_name='vehicle_detection_zone'\"" 2>/dev/null)
m2m_exists=$(ssh_run "docker exec postgres psql -U postgres -d hub_controller -tAc \"SELECT 1 FROM information_schema.tables WHERE table_name='camera_camerasetting_vehicle_recognition_cameras'\"" 2>/dev/null)

[ "$col_exists" = "1" ] && echo "  ${GREEN}PASS${RESET}  camera_camera.vehicle_detection_zone exists" \
                       || echo "  ${RED}FAIL${RESET}  camera_camera.vehicle_detection_zone MISSING"
[ "$m2m_exists" = "1" ] && echo "  ${GREEN}PASS${RESET}  M2M join table exists" \
                       || echo "  ${RED}FAIL${RESET}  M2M join table MISSING"
echo

# Section 6: container health
echo "## VehicleAI container health"
echo
status=$(ssh_run "docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' '$VEHICLE_CONTAINER' 2>/dev/null")
echo "  ${VEHICLE_CONTAINER}  $status"

echo
echo "================================================================"
echo "Done. Any FAIL above must be reconciled before Flutter v162 ships."
echo "================================================================"
