# PLAN: Query the master volume percentage of the default audio output device using pycaw and print it.
# RISK: safe

import sys
import subprocess

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pycaw", "comtypes"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

device = AudioUtilities.GetSpeakers()
if hasattr(device, "Activate"):
    endpoint = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
elif hasattr(device, "_dev") and hasattr(device._dev, "Activate"):
    endpoint = device._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
else:
    raise RuntimeError("Unable to access audio endpoint activation interface.")

volume = endpoint.QueryInterface(IAudioEndpointVolume)
scalar = volume.GetMasterVolumeLevelScalar()
percentage = round(scalar * 100)

print(f"Current master volume is {percentage}%")