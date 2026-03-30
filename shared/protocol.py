"""
Binary protocol for EcranDistant.

Frame messages  : [0x01][width:2B][height:2B][jpeg_bytes...]
Audio messages  : [0x13][samplerate:2B][channels:1B][zlib_pcm...]
File chunk      : [0x22][transfer_id:4B][data...]
Control messages: [type:1B][json_utf8...]
"""
import struct
import json
import hashlib
import zlib

# ── Message types ──────────────────────────────────────────────────────────────
MSG_FRAME      = 0x01   # Host → Client : video frame
MSG_MOUSE      = 0x02   # Client → Host : mouse event
MSG_KEY        = 0x03   # Client → Host : keyboard event
MSG_AUTH       = 0x04   # Client → Host : auth request
MSG_AUTH_OK    = 0x05   # Host → Client : auth success + screen info
MSG_AUTH_FAIL  = 0x06   # Host → Client : auth failure
MSG_PING       = 0x07   # Client → Host
MSG_PONG       = 0x08   # Host → Client
MSG_CONFIG     = 0x09   # Client → Host : change settings (quality, etc.)
MSG_SELECT_MON = 0x0A   # Client → Host : switch monitor
MSG_MON_CHANGED= 0x0B   # Host → Client : new resolution after monitor switch
MSG_CLIPBOARD  = 0x0C   # Both ways     : clipboard text sync
MSG_AUDIO_CFG  = 0x0D   # Client → Host : enable/disable audio stream
MSG_AUDIO      = 0x13   # Host → Client : audio frame (binary, zlib-compressed)

# ── File transfer ──────────────────────────────────────────────────────────────
MSG_FILE_SEND_REQ  = 0x20  # Client → Host : upload request  {id, name, size}
MSG_FILE_SEND_ACK  = 0x21  # Host → Client : {id, ok, reason?}
MSG_FILE_CHUNK     = 0x22  # Both ways     : [type:1B][id:4B][data...]
MSG_FILE_DONE      = 0x23  # Both ways     : {id, name?}
MSG_FILE_ABORT     = 0x24  # Both ways     : {id, reason}
MSG_FILE_BROWSE    = 0x25  # Client → Host : {id, path}
MSG_FILE_LIST      = 0x26  # Host → Client : {id, path, parent, entries}
MSG_FILE_GET_REQ   = 0x27  # Client → Host : download request {id, path}
MSG_FILE_GET_INFO  = 0x28  # Host → Client : {id, name, size}

# ── Chat ───────────────────────────────────────────────────────────────────────
MSG_CHAT           = 0x30  # Both ways     : {text, sender, ts}


# ── Helpers ────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def encode_frame(jpeg: bytes, width: int, height: int) -> bytes:
    return struct.pack('>BHH', MSG_FRAME, width, height) + jpeg


def encode_audio(pcm: bytes, samplerate: int, channels: int) -> bytes:
    compressed = zlib.compress(pcm, level=1)
    return struct.pack('>BHB', MSG_AUDIO, samplerate, channels) + compressed


def encode_json(msg_type: int, payload: dict) -> bytes:
    return bytes([msg_type]) + json.dumps(payload).encode('utf-8')


def encode_file_chunk(transfer_id: int, data: bytes) -> bytes:
    """[MSG_FILE_CHUNK:1B][transfer_id:4B big-endian][data...]"""
    return struct.pack('>BI', MSG_FILE_CHUNK, transfer_id) + data


def decode(data: bytes) -> dict:
    """Decode any message into a dict."""
    if not data:
        return {}
    if isinstance(data, str):
        data = data.encode('utf-8')

    t = data[0]

    if t == MSG_FRAME:
        w, h = struct.unpack('>HH', data[1:5])
        return {'type': 'frame', 'w': w, 'h': h, 'jpeg': data[5:]}

    if t == MSG_AUDIO:
        samplerate, channels = struct.unpack('>HB', data[1:4])
        pcm = zlib.decompress(data[4:])
        return {'type': 'audio', 'samplerate': samplerate, 'channels': channels, 'pcm': pcm}

    if t == MSG_FILE_CHUNK:
        tid = struct.unpack('>I', data[1:5])[0]
        return {'type': 'file_chunk', '_msg_type': MSG_FILE_CHUNK, 'id': tid, 'data': data[5:]}

    payload = json.loads(data[1:].decode('utf-8'))
    payload['_msg_type'] = t
    return payload
