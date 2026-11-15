"""Threaded raw TCP forwarding for exposing the local WebUI through a tunnel."""
import select
import socket
import socketserver
import threading


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            upstream = socket.create_connection(self.server.target, timeout=5)
        except OSError:
            return

        client = self.request
        with upstream:
            upstream.settimeout(None)
            peers = {client: upstream, upstream: client}
            open_sockets = set(peers)
            while open_sockets:
                try:
                    readable, _, exceptional = select.select(open_sockets, [], open_sockets)
                    if exceptional:
                        return
                    for source in readable:
                        destination = peers[source]
                        data = source.recv(65536)
                        if data:
                            destination.sendall(data)
                        else:
                            open_sockets.remove(source)
                            try:
                                destination.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                except OSError:
                    return


class ThreadingTCPForwarder(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


def start_tcp_forwarder(host, port, target_host, target_port):
    """Start forwarding TCP byte streams and return the bound server."""
    server = ThreadingTCPForwarder((host, port), _ForwardHandler)
    server.target = (target_host, target_port)
    threading.Thread(target=server.serve_forever, name='webui-tcp-forwarder', daemon=True).start()
    return server
