"""
Relay server — runs on a machine with a public IP.

Both host and client connect here; the relay pairs them by session ID
and forwards raw bytes between them without inspecting the content.

Relay control messages : text JSON frames
Session messages       : binary frames (our protocol, forwarded as-is)
"""
import asyncio
import json
import logging
import secrets
import string
from http import HTTPStatus

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)

# {session_id: {'host': ws, 'client_holder': [ws|None], 'client_joined': Event}}
_sessions: dict = {}


def _new_session_id(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        sid = ''.join(secrets.choice(alphabet) for _ in range(length))
        if sid not in _sessions:
            return sid


async def _handler(ws):
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=15.0)
    except asyncio.TimeoutError:
        return

    try:
        msg = json.loads(first)
    except (json.JSONDecodeError, TypeError):
        return

    role = msg.get('role')
    if role == 'host':
        await _host_session(ws)
    elif role == 'client':
        await _client_session(ws, msg.get('session_id', ''))
    else:
        await ws.send(json.dumps({'type': 'error', 'reason': 'Unknown role'}))


# ── Host side ──────────────────────────────────────────────────────────────────

async def _host_session(ws):
    session_id = _new_session_id()
    client_holder = [None]
    client_joined = asyncio.Event()

    _sessions[session_id] = {
        'host': ws,
        'client_holder': client_holder,
        'client_joined': client_joined,
    }
    logger.info('[%s] Host registered from %s', session_id, ws.remote_address)

    try:
        await ws.send(json.dumps({'type': 'registered', 'session_id': session_id}))

        # Wait up to 10 minutes for a client to join
        await asyncio.wait_for(client_joined.wait(), timeout=600.0)

        client_ws = client_holder[0]
        await ws.send(json.dumps({'type': 'peer_connected'}))
        logger.info('[%s] Client paired, relaying…', session_id)

        # Forward host → client until connection closes
        async for data in ws:
            try:
                await client_ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                break

    except asyncio.TimeoutError:
        try:
            await ws.send(json.dumps({'type': 'timeout', 'reason': 'No client joined within 10 min'}))
        except Exception:
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _sessions.pop(session_id, None)
        logger.info('[%s] Session closed', session_id)


# ── Client side ────────────────────────────────────────────────────────────────

async def _client_session(ws, session_id: str):
    if not session_id or session_id not in _sessions:
        await ws.send(json.dumps({'type': 'error', 'reason': 'Session not found'}))
        return

    session = _sessions[session_id]
    if session['client_holder'][0] is not None:
        await ws.send(json.dumps({'type': 'error', 'reason': 'Session already has a client'}))
        return

    session['client_holder'][0] = ws
    session['client_joined'].set()

    try:
        await ws.send(json.dumps({'type': 'joined'}))
        logger.info('[%s] Client joined from %s', session_id, ws.remote_address)

        host_ws = session['host']
        # Forward client → host until connection closes
        async for data in ws:
            try:
                await host_ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                break

    except websockets.exceptions.ConnectionClosed:
        pass


# ── Entry point ────────────────────────────────────────────────────────────────

async def _health(connection, request):
    """Answer Render's HTTP health-check probes without breaking WebSocket."""
    if request.path in ('/', '/health'):
        return connection.respond(HTTPStatus.OK, "Relay OK\n")


async def start(host: str = '0.0.0.0', port: int = 9000):
    async with websockets.serve(_handler, host, port, process_request=_health):
        logger.info('Relay server running on %s:%d', host, port)
        await asyncio.Future()  # run forever
