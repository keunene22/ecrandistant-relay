"""
Entry point for the RELAY SERVER.
Deploy this on any machine with a public IP (VPS, cloud, etc.).

Usage:
    python main_relay.py [--host 0.0.0.0] [--port 9000]
"""
import asyncio
import argparse
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


def main():
    parser = argparse.ArgumentParser(description='EcranDistant relay server')
    parser.add_argument('--host', default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=9000, help='Port (default: 9000)')
    args = parser.parse_args()

    print()
    print('=' * 42)
    print('   EcranDistant  —  RELAY SERVER')
    print('=' * 42)
    print(f'   Listening on  {args.host}:{args.port}')
    print('   Press Ctrl+C to stop.\n')

    from relay.server import start
    try:
        asyncio.run(start(host=args.host, port=args.port))
    except KeyboardInterrupt:
        print('\nRelay stopped.')


if __name__ == '__main__':
    main()
