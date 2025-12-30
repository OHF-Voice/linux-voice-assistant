#!/bin/bash
export DISPLAY=:0
journalctl --user -u lva.service -f | while read line; do
    if echo "$line" | grep -q "Detected wake word"; then
        xset dpms force on
        xset s reset
    fi
done
