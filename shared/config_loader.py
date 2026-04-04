"""
Chargement de config.json situé à côté du .exe (ou du script).
Utilisé par l'hôte et le client pour lire les paramètres fixes.
"""
import sys
import os
import json

_DEFAULT = {
    'password':   '',
    'session_id': '',
    'relay_url':  'wss://api.194.163.188.237.nip.io/relay',
}


def config_path() -> str:
    """Retourne le chemin de config.json (à côté du .exe ou du script)."""
    if getattr(sys, 'frozen', False):
        # Exécutable PyInstaller
        base = os.path.dirname(sys.executable)
    else:
        # Script Python normal
        base = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(base, 'config.json')


def load_config() -> dict:
    """Charge config.json. Retourne les valeurs par défaut si absent/invalide."""
    path = config_path()
    cfg = dict(_DEFAULT)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if k in _DEFAULT})
        except Exception:
            pass
    return cfg


def is_home_mode(cfg: dict) -> bool:
    """True si le config a tout ce qu'il faut pour démarrer sans rien taper."""
    return bool(cfg.get('password') and cfg.get('session_id') and cfg.get('relay_url'))
