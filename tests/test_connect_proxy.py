import socket
import socketserver
import threading
import unittest

from lidaclips.connect_proxy import ConnectProxyHandler, ThreadingTCPServer


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(1024)
        if data == b"ping":
            self.request.sendall(b"pong")


class ConnectProxyTests(unittest.TestCase):
    def test_connect_tunnels_bytes_to_upstream(self):
        with ThreadingTCPServer(("127.0.0.1", 0), EchoHandler) as upstream:
            upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
            upstream_thread.start()
            upstream_host, upstream_port = upstream.server_address

            with ThreadingTCPServer(("127.0.0.1", 0), ConnectProxyHandler) as proxy:
                proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
                proxy_thread.start()
                proxy_host, proxy_port = proxy.server_address

                with socket.create_connection((proxy_host, proxy_port), timeout=5) as client:
                    request = (
                        f"CONNECT {upstream_host}:{upstream_port} HTTP/1.1\r\n"
                        f"Host: {upstream_host}:{upstream_port}\r\n"
                        "\r\n"
                    )
                    client.sendall(request.encode("ascii"))
                    response = client.recv(4096)
                    self.assertIn(b"200 Connection Established", response)
                    client.sendall(b"ping")
                    self.assertEqual(client.recv(4096), b"pong")

                proxy.shutdown()

            upstream.shutdown()


if __name__ == "__main__":
    unittest.main()
