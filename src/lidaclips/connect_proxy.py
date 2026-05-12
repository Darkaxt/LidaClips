import argparse
import select
import socket
import socketserver
from urllib.parse import urlsplit


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ConnectProxyHandler(socketserver.BaseRequestHandler):
    timeout = 30

    def handle(self) -> None:
        self.request.settimeout(self.timeout)
        header = self._read_header()
        if not header:
            return

        lines = header.split(b"\r\n")
        request_line = lines[0].decode("iso-8859-1", errors="replace")
        parts = request_line.split()
        if len(parts) != 3:
            self._send_error(400, "Bad Request")
            return

        method, target, version = parts
        if method.upper() == "CONNECT":
            self._handle_connect(target)
            return

        self._handle_http(method, target, version, lines[1:])

    def _read_header(self) -> bytes:
        chunks = []
        total = 0
        while total < 65536:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            data = b"".join(chunks)
            if b"\r\n\r\n" in data:
                return data.split(b"\r\n\r\n", 1)[0]
        return b""

    def _handle_connect(self, target: str) -> None:
        host, port = self._split_host_port(target, default_port=443)
        if not host:
            self._send_error(400, "Bad CONNECT target")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=self.timeout)
        except OSError:
            self._send_error(502, "Bad Gateway")
            return

        with upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._relay(self.request, upstream)

    def _handle_http(self, method: str, target: str, version: str, header_lines: list[bytes]) -> None:
        parsed = urlsplit(target)
        if not parsed.hostname:
            self._send_error(400, "Absolute URI required")
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        try:
            upstream = socket.create_connection((parsed.hostname, port), timeout=self.timeout)
        except OSError:
            self._send_error(502, "Bad Gateway")
            return

        with upstream:
            upstream.sendall(f"{method} {path} {version}\r\n".encode("ascii", errors="replace"))
            for line in header_lines:
                if not line.lower().startswith(b"proxy-connection:"):
                    upstream.sendall(line + b"\r\n")
            upstream.sendall(b"\r\n")
            self._relay(self.request, upstream)

    def _relay(self, client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        for item in sockets:
            item.setblocking(False)
        while True:
            readable, _, errored = select.select(sockets, [], sockets, self.timeout)
            if errored or not readable:
                return
            for source in readable:
                try:
                    data = source.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                destination = upstream if source is client else client
                try:
                    destination.sendall(data)
                except OSError:
                    return

    def _send_error(self, status: int, reason: str) -> None:
        body = f"{status} {reason}\n".encode("ascii", errors="replace")
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        self.request.sendall(response + body)

    def _split_host_port(self, value: str, default_port: int) -> tuple[str, int]:
        if value.startswith("[") and "]" in value:
            host, _, rest = value[1:].partition("]")
            port = int(rest[1:]) if rest.startswith(":") else default_port
            return host, port
        host, separator, port_text = value.rpartition(":")
        if separator and port_text.isdigit():
            return host, int(port_text)
        return value, default_port


def main() -> None:
    parser = argparse.ArgumentParser(description="Small HTTP CONNECT proxy for yt-dlp egress.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    with ThreadingTCPServer((args.host, args.port), ConnectProxyHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
