# Audio Options: Gain and Noise Suppression

## What do Gain and Noise Suppression do

### Gain

When you change gain, LVA(using the webrtc library) automatically adjusts the microphone input volume to keep it at a consistent level. If you're speaking quietly it boosts the signal, if you're speaking loudly it reduces it. Useful in the following ways:-

- Microphones that are too quiet by default
- Environments where you move around relative to the mic
- Keeping wake word detection consistent regardless of speaking volume

### Noise Suppression

When you change noise suppression, LVA(using the webrtc library) filters out constant background noise from the audio signal. It works by learning what "silence" sounds like in your environment and subtracting that from the audio. Useful in the following ways:

- Noisy environments
- Improving STT accuracy by sending cleaner audio to Home Assistant
- Reducing false wake word triggers from background noise

## Using Gain and Noise Suppression with LVA

LVA implements gain and Noise Suppression in two ways:

- [CLI Argument/ENV file](#cli-argumentenv-file)
- [Home Assistant Entity](#home-assistant-entity)

### CLI Argument/ENV file

#### CLI Argument

Using docker-entrypoint.sh the following flags can be used to set gain and noise suppression: 
- `--mic-auto-gain`(ranges from 0-31)
- `--mic-noise-suppression`(ranges from 0-4)

#### ENV file

You can add/edit these variables in the .env file to set gain and noise suppression:
- `MIC_AUTO_GAIN="0"`
- `MIC_NOISE_SUPPRESSION="0"`


### Home Assistant Entity

LVA exposes two slider entities to change these gain and noise suppression from which you can edit the gain and noise suppression at runtime.

💡 **Note:**  Setting the flag and ENV values to 0 turns them off and are not used.

💡 **Note:**  Keep in mind that when the flags are set they will overwrite the previous value in the preferences file and also they will be overridden if the value is changed in Home Assistant(Also applies to the ENV file but the ENV file will always overwrite the last value on startup).
