"""Test rapide : vérifie que le son système est bien capturé."""
import time
import sounddevice as sd
import numpy as np

DEVICE = 20   # Mixage stéréo
SR     = 48000
CH     = 2

print("=" * 50)
print("TEST CAPTURE AUDIO — device 20 (Mixage stéréo)")
print("Lance VLC ou de la musique, tu dois voir les barres bouger.")
print("Ctrl+C pour arrêter.")
print("=" * 50)

def cb(indata, frames, t, status):
    vol = int(np.abs(indata).mean())
    bars = min(vol // 3, 40)
    print(f'\r  [{("█" * bars).ljust(40)}] {vol:4d}  ', end='', flush=True)

try:
    with sd.InputStream(device=DEVICE, channels=CH, samplerate=SR,
                        dtype='int16', blocksize=2048, callback=cb):
        while True:
            time.sleep(0.1)
except KeyboardInterrupt:
    print("\n\nTest terminé.")
except Exception as e:
    print(f"\nERREUR : {e}")
