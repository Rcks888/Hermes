"""
MT5 rpyc bridge server — runs inside Wine Python.
Started by mt5_connector.py, not directly by the user.

Usage (via Wine):
    wine python mt5_server.py [port]
"""
import sys
import rpyc
from rpyc.utils.server import ThreadedServer

import MetaTrader5 as mt5

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18812


class MT5Service(rpyc.Service):
    exposed_mt5 = mt5


if __name__ == "__main__":
    server = ThreadedServer(
        MT5Service,
        hostname="localhost",
        port=PORT,
        protocol_config={
            "allow_all_attrs": True,
            "allow_public_attrs": True,
            "allow_pickle": True,
        },
    )
    print(f"MT5 rpyc server listening on port {PORT}")
    server.start()
