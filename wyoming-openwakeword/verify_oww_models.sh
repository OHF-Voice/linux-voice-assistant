#!/usr/bin/env bash
# verify_oww_models.sh — sanity-check all wake models
set -euo pipefail

cd /home/pi/wyoming-openwakeword
MODELDIR="wyoming_openwakeword/models"   # adjust if needed

GOOD=()
BAD=()

for f in "$MODELDIR"/*.tflite; do
  base="$(basename "$f" .tflite)"
  case "$base" in
    embedding_model|melspectrogram) continue ;;  # support files, not wake words
  esac

  printf "Testing %-20s ... " "$base"
  # Bind to a dummy port and kill after 6s; success == loaded without immediate error
  if timeout 6s ./script/run --uri tcp://127.0.0.1:0 --preload-model "$base" --debug \
       >/tmp/oww_test.log 2>&1 ; then
    echo "ok"
    GOOD+=("$base")
  else
    rc=$?
    if [ $rc -eq 124 ]; then
      echo "ok (timeout after load)"
      GOOD+=("$base")
    else
      echo "FAIL (rc=$rc)"
      BAD+=("$base")
      sed 's/^/  /' /tmp/oww_test.log | tail -n 10
    fi
  fi
done

echo
echo "Good models (${#GOOD[@]}): ${GOOD[*]}"
echo "Bad models  (${#BAD[@]}): ${BAD[*]-<none>}"

