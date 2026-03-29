"""
Relay server — runs on a machine with a public IP.

Both host and client connect here via WebSocket; the relay pairs them by
session ID and forwards raw bytes between them without inspecting content.

Uses aiohttp so that Render's HTTP health-check (HEAD /) also works.
"""
import asyncio
import json
import logging
import secrets
import string

from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)

# {session_id: {'host': ws, 'client_holder': [ws|None], 'client_joined': Event}}
_sessions: dict = {}


def _new_session_id(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        sid = ''.join(secrets.choice(alphabet) for _ in range(length))
        if sid not in _sessions:
            return sid


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _send_json(ws, data: dict):
    await ws.send_str(json.dumps(data))


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
    logger.info('[%s] Host registered', session_id)

    try:
        await _send_json(ws, {'type': 'registered', 'session_id': session_id})

        try:
            await asyncio.wait_for(client_joined.wait(), timeout=600.0)
        except asyncio.TimeoutError:
            await _send_json(ws, {'type': 'timeout', 'reason': 'No client joined within 10 min'})
            return

        client_ws = client_holder[0]
        await _send_json(ws, {'type': 'peer_connected'})
        logger.info('[%s] Client paired, relaying…', session_id)

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    await client_ws.send_str(msg.data)
                except Exception:
                    break
            elif msg.type == WSMsgType.BINARY:
                try:
                    await client_ws.send_bytes(msg.data)
                except Exception:
                    break
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break

    finally:
        _sessions.pop(session_id, None)
        logger.info('[%s] Session closed', session_id)


# ── Client side ────────────────────────────────────────────────────────────────

async def _client_session(ws, session_id: str):
    if not session_id or session_id not in _sessions:
        await _send_json(ws, {'type': 'error', 'reason': 'Session not found'})
        return

    session = _sessions[session_id]
    if session['client_holder'][0] is not None:
        await _send_json(ws, {'type': 'error', 'reason': 'Session already has a client'})
        return

    session['client_holder'][0] = ws
    session['client_joined'].set()

    await _send_json(ws, {'type': 'joined'})
    logger.info('[%s] Client joined', session_id)

    host_ws = session['host']
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                await host_ws.send_str(msg.data)
            except Exception:
                break
        elif msg.type == WSMsgType.BINARY:
            try:
                await host_ws.send_bytes(msg.data)
            except Exception:
                break
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            break


# ── HTTP / WebSocket handler ───────────────────────────────────────────────────

async def root_handler(request):
    """Handles both HTTP health-checks (GET/HEAD) and WebSocket upgrades."""
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        # WebSocket connection from host or client
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        try:
            first_msg = await asyncio.wait_for(ws.receive(), timeout=15.0)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if first_msg.type != WSMsgType.TEXT:
            await ws.close()
            return ws

        try:
            msg = json.loads(first_msg.data)
        except (json.JSONDecodeError, TypeError):
            await ws.close()
            return ws

        role = msg.get('role')
        if role == 'host':
            await _host_session(ws)
        elif role == 'client':
            await _client_session(ws, msg.get('session_id', ''))
        else:
            await _send_json(ws, {'type': 'error', 'reason': 'Unknown role'})

        return ws

    # Plain HTTP request (Render health-check, browser, etc.)
    return web.Response(text="EcranDistant Relay OK\n")


# ── Entry point ────────────────────────────────────────────────────────────────

async def start(host: str = '0.0.0.0', port: int = 9000):
    app = web.Application()
    app.router.add_route('*', '/', root_handler)
    app.router.add_route('*', '/health', root_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info('Relay server running on %s:%d', host, port)
    await asyncio.Future()  # run forever
