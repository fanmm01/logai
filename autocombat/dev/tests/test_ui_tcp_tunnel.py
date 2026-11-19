"""Integration tests for the standalone WebUI TCP forwarder."""
import socket
import socketserver
import threading
import unittest

from ui_tcp_tunnel import start_tcp_forwarder


def _recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = _recv_exact(self.request, 5)
        self.request.sendall(b'reply:' + request)
        while self.request.recv(1024):
            pass
        self.request.sendall(b'|closed')


class TcpTunnelTests(unittest.TestCase):
    def test_forwards_both_directions_and_half_close(self):
        upstream = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _EchoHandler)
        upstream.daemon_threads = True
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        forwarder = None
        try:
            forwarder = start_tcp_forwarder(
                '127.0.0.1', 0, '127.0.0.1', upstream.server_address[1])
            client = socket.create_connection(forwarder.server_address, timeout=2)
            with client:
                client.settimeout(2)
                client.sendall(b'hello')
                self.assertEqual(_recv_exact(client, 11), b'reply:hello')
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(_recv_exact(client, 7), b'|closed')
        finally:
            if forwarder:
                forwarder.shutdown()
                forwarder.server_close()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
