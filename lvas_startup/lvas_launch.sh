#!/bin/bash

sleep 10
export DISPLAY=:0
export XAUTHORITY=/home/stef/.Xauthority

/usr/bin/pkill -9 firefox
rm -rf /home/stef/.mozilla/firefox/*.default-release/lock

echo "Launching Firefox..."
/usr/bin/firefox --new-window https://ha.stefhartog.com/assist-landscape/0 --kiosk &

sleep 8
echo "Playing Chime..."
/usr/bin/paplay /home/stef/lvas/sounds/Presentation_Intro_Transition_5.wav
