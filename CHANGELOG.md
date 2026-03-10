# Changelog

## 1.1.6

### Bug Fixes
- Start listening after wakeup sound
- Prevent wake word from interrupting active voice pipeline
- Ensure wake word state is set after the wake sound

### Improvements
- Script/run error handling and return code consistency
- Move port check into Python application
- Help output on application parameters
- Added default sample rate to pulse documentation

## 1.1.5

### Bug Fixes
- Fix timer finished sound not looping
- Suppress spurious end-file event when starting playback
- Fix tts.speak and announce
- Fix duck/unduck on timers

### Improvements
- Volume persistence across restarts
- Enhance play method for playlist support
- Add version and hash to docker container
- Allow portainer stack to point directly to docker-compose.yml
- Improve documentation

## 1.1.4

### Improvements
- Added custom paths to docker for wakeword and sounds
- Added wake-word-dir variable
- Changed default pipewire configuration
- Updated documentation regarding wakeword and sounds
- Documentation corrections

## 1.1.3

### Bug Fixes
- Hotfix: typo in network-interface output order causing application crash

### Documentation
- Correct LVA_USER_ID variable name

## 1.1.2

### Breaking Changes
- MAC address detection has been fixed. After updating, it may be necessary 
  to remove and re-add the device in Home Assistant.
- The docker-compose.yml and .env.example files were updated. Please review 
  and adapt your configuration if required.

### Features
- Add variable for network-interface with auto-detection enabled by default
- Add MAC address autodetection based on detected or specified network interface
- Switch from listening on all IP addresses to binding to the IP of the default interface
- Move auto-name creation from shell to Python (name is now optional and auto-generated)
- Add first version of docker-compose for developers
- Changes EXTRA_ARGS variable to bash array for better variable expansion

### Bug Fixes
- Fix non-working variables (e.g. sound-files)
- Fix client-name validation (no spaces allowed)
- Fix example configuration

## 1.1.1

### Bug Fixes
- Handle STOP command correctly in media_player

## 1.1.0

### Features
- Support custom/external wake words
- Add an option to play thinking sound
- Add mute/unmute sounds
- Add sound customization
- Handle announcement separately from the media player
- Add Docker build pipeline and compose files
- Don't require name for list commands

### Bug Fixes
- Fix PEP 508 dependency operator
- Fix mpv pause state when loading new media
- Fix issues with audio handling

## 1.0.0

- Initial release
