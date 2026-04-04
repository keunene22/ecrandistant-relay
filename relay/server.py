"""
Relay server — aiohttp, handles HTTP health checks + WebSocket relay.

Architecture:
- _host_session  : drives the full bidirectional relay via asyncio tasks
- _client_session: just registers the client WS and waits for session end
"""
import asyncio
import json
import logging
import secrets
import string
from http import HTTPStatus

from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)

# session: {'host': ws, 'client_holder': [ws|None], 'client_joined': Event, 'done': Event}
_sessions: dict = {}

# alias → session_id courant (ex: 'BUREAU' → 'X4K2F1')
_aliases: dict = {}


def _new_session_id(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        sid = ''.join(secrets.choice(alphabet) for _ in range(length))
        if sid not in _sessions:
            return sid


async def _send_json(ws, data: dict):
    await ws.send_str(json.dumps(data))


# ── Bidirectional forwarder ────────────────────────────────────────────────────

async def _forward(src_ws, dst_ws, label='?'):
    """Forward BINARY messages only — TEXT messages are relay-internal control."""
    async for msg in src_ws:
        if msg.type == WSMsgType.BINARY:
            try:
                await dst_ws.send_bytes(msg.data)
            except Exception as e:
                logger.warning('[Forward %s] send error: %s', label, e)
                break  # destinataire déconnecté
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            logger.info('[Forward %s] src closed: %s', label, msg.type)
            break
        # TEXT (heartbeat_ack, keepalive…) → filtered, not forwarded
    logger.info('[Forward %s] done', label)


# ── Host handler ───────────────────────────────────────────────────────────────

async def _host_session(ws, requested_sid: str = '', alias: str = ''):
    # Si l'hôte demande un ID fixe et qu'il est libre, on l'utilise
    if requested_sid and requested_sid not in _sessions:
        session_id = requested_sid.upper()
    else:
        session_id = _new_session_id()

    # Enregistrer l'alias si fourni (ex: 'BUREAU' → session_id actuel)
    if alias:
        _aliases[alias.upper()] = session_id
    client_holder = [None]
    client_joined = asyncio.Event()
    session_done  = asyncio.Event()

    _sessions[session_id] = {
        'host':          ws,
        'client_holder': client_holder,
        'client_joined': client_joined,
        'done':          session_done,
    }
    logger.info('[%s] Host registered', session_id)

    try:
        await _send_json(ws, {'type': 'registered', 'session_id': session_id})

        # Wait for client with periodic heartbeats to keep Render's proxy alive
        deadline = 86400.0  # 24h — pas de timeout en production VPS
        elapsed  = 0.0
        while not client_joined.is_set():
            if elapsed >= deadline:
                await _send_json(ws, {'type': 'timeout', 'reason': 'No client joined within 10 min'})
                return
            try:
                await asyncio.wait_for(asyncio.shield(client_joined.wait()), timeout=10.0)
            except asyncio.TimeoutError:
                elapsed += 10.0
                await _send_json(ws, {'type': 'heartbeat'})

        client_ws = client_holder[0]
        await _send_json(ws, {'type': 'peer_connected'})
        logger.info('[%s] Relay started', session_id)

        # Launch both directions as concurrent tasks
        t1 = asyncio.create_task(_forward(ws,        client_ws, 'host→client'))
        t2 = asyncio.create_task(_forward(client_ws, ws,        'client→host'))

        # Stop as soon as one side disconnects
        done, pending = await asyncio.wait(
            [t1, t2], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

        logger.info('[%s] Relay ended', session_id)

    except Exception as e:
        logger.warning('[%s] Host session error: %s', session_id, e)
    finally:
        session_done.set()
        _sessions.pop(session_id, None)
        # Nettoyer l'alias quand la session se termine
        for k, v in list(_aliases.items()):
            if v == session_id:
                _aliases.pop(k, None)


# ── Client handler ────────────────────────────────────────────────────────────

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

    # Wait until _host_session finishes the relay (keeps this WS alive)
    await session['done'].wait()


# ── HTTP / WebSocket entry point ──────────────────────────────────────────────

async def root_handler(request):
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        ws = web.WebSocketResponse(heartbeat=None, receive_timeout=None,
                                   max_msg_size=0)  # 0 = illimité
        await ws.prepare(request)

        try:
            first = await asyncio.wait_for(ws.receive(), timeout=20.0)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if first.type != WSMsgType.TEXT:
            await ws.close()
            return ws

        try:
            msg = json.loads(first.data)
        except (json.JSONDecodeError, TypeError):
            await ws.close()
            return ws

        role = msg.get('role')
        if role == 'host':
            await _host_session(ws, msg.get('session_id', ''), msg.get('alias', ''))
        elif role == 'client':
            await _client_session(ws, msg.get('session_id', ''))
        else:
            await _send_json(ws, {'type': 'error', 'reason': 'Unknown role'})

        return ws

    # Health check (GET / HEAD)
    return web.Response(text="EcranDistant Relay OK\n")


async def alias_handler(request):
    """GET /alias/BUREAU → retourne le session_id courant pour cet alias."""
    alias = request.match_info.get('alias', '').upper()
    if not alias:
        return web.Response(status=400, text='Alias manquant')
    sid = _aliases.get(alias)
    if not sid:
        return web.Response(status=404, text='Aucune session active pour cet alias')
    return web.json_response({'session_id': sid, 'alias': alias})


async def client_page_handler(request):
    """Serve the web client HTML page."""
    import os
    html_path = os.path.join(os.path.dirname(__file__), '..', 'webclient', 'index.html')
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return web.Response(text=content, content_type='text/html')
    except FileNotFoundError:
        return web.Response(text='Web client not found.', status=404)


# ── Entry point ───────────────────────────────────────────────────────────────

async def start(host: str = '0.0.0.0', port: int = 9000):
    app = web.Application(client_max_size=20 * 1024 * 1024)  # 20 MB max message
    app.router.add_route('*', '/',               root_handler)
    app.router.add_route('*', '/relay',          root_handler)
    app.router.add_route('*', '/health',         root_handler)
    app.router.add_route('GET', '/client',       client_page_handler)
    app.router.add_route('GET', '/alias/{alias}', alias_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info('Relay server running on %s:%d', host, port)
    await asyncio.Future()
