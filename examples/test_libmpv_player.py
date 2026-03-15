import time
import logging

from linux_voice_assistant.player.libmpv import LibMpvPlayer


logging.basicConfig(level=logging.DEBUG)

player = LibMpvPlayer()

player.play("https://icecast.radiofrance.fr/fip-midfi.mp3")
time.sleep(5)

player.pause()
time.sleep(2)

player.resume()
time.sleep(5)

print("Set volume to 80")
player.set_volume(80)
time.sleep(2)

print("Duck")
player.duck()
time.sleep(2)

print("Set volume to 40 while ducked")
player.set_volume(40)
time.sleep(2)

print("Unduck")
player.unduck()
time.sleep(2)

player.stop()
time.sleep(2)

print("Final state:", player.state())
