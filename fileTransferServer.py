#!/usr/bin/env python3
"""
File transfer server with length-prefixed framing.
Protocol: client sends framed "GET <filename>"; server replies with framed file
contents or framed "ERROR: message". Works through stammer-proxy.
Part 2: Modify to support multiple concurrent clients (e.g. fork() or select()).
"""

import socket
import sys
import os
import select
import threading

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))
import params
import framing

# --- Command-line parameters (see lib/params) ---
switchesVarDefaults = (
    (('-l', '--listenPort'), 'listenPort', 50001),
    (('-d', '--directory'), 'directory', '.'),   # base directory for file requests
    (('-m', '--mode'), 'mode', 'select'),        # 'select' (default) or 'threads'
    (('-?', '--usage'), 'usage', False),
)

progname = "fileTransferServer"
paramMap = params.parseParams(switchesVarDefaults)
listenPort = paramMap['listenPort']
directory = paramMap['directory']
mode = str(paramMap.get('mode', 'select')).strip().lower()
if paramMap['usage']:
    params.usage()

listenAddr = ''
try:
    listenPort = int(listenPort)
except ValueError:
    print("Invalid listenPort: %s" % listenPort)
    sys.exit(1)

# Canonical base directory for serving files. We resolve this once so that
# path safety checks (safe_path) are done against a fixed absolute path.
base_dir = os.path.abspath(directory)
if not os.path.isdir(base_dir):
    print("Directory does not exist: %s" % base_dir)
    sys.exit(1)


def safe_path(requested_path):
    """
    Return absolute path if it is under base_dir; else None (unsafe).

    OS/security: The client sends a string like "README.md" or "../../etc/passwd".
    If we blindly joined that to base_dir and opened it, we could escape the
    intended directory (path traversal). The kernel would happily open any
    path our process has permission to read. So we:
      1) Strip leading slashes and remove ".." so the client can't go up
         past base_dir.
      2) Join with base_dir and resolve to an absolute path.
      3) Check that the result still starts with base_dir (same prefix).
    Only then do we treat it as safe. This is the same idea as chroot or
    "document root" in a web server: the process only exposes one subtree.
    """
    requested_path = requested_path.lstrip('/').replace('..', '')
    requested_path = requested_path or '.'
    abs_path = os.path.abspath(os.path.join(base_dir, requested_path))
    if not abs_path.startswith(base_dir):
        return None
    return abs_path


def handle_client(conn, addr):
    """
    Handle one client: read framed GET request, send framed file or error.

    conn is a connected socket (file descriptor). Data flow in terms of
    memory and the OS:
      - recv_frame(conn): bytes arrive from the network into the kernel's
        socket receive buffer; recv() copies from that buffer into our
        process's user-space memory (the 'raw' bytes object). So we have
        one complete request message in RAM.
      - We open the file and read it with f.read(): the kernel copies file
        data from disk (or page cache) into our process's address space
        ('data'). That may be a lot of memory for a large file.
      - send_frame(conn, data): sendall() copies our 'data' from user space
        into the kernel's socket send buffer. The kernel then transmits
        that buffer to the client (possibly in many TCP segments). We don't
        block until "client got it"; we block until "kernel has accepted
        it." The kernel handles the rest (flow control, retransmits, etc.).
    """
    try:
        raw = framing.recv_frame(conn)
        if not raw:
            return
        msg = raw.decode('utf-8', errors='replace').strip()
        if not msg.upper().startswith('GET '):
            framing.send_frame(conn, b'ERROR: Expected GET <filename>')
            return
        filename = msg[4:].strip()
        if not filename:
            framing.send_frame(conn, b'ERROR: Missing filename')
            return
        path = safe_path(filename)
        if path is None:
            framing.send_frame(conn, b'ERROR: Invalid path')
            return
        if not os.path.isfile(path):
            framing.send_frame(conn, ('ERROR: Not a file or not found: %s' % filename).encode('utf-8'))
            return
        # Open file: the OS returns a file descriptor; 'with' ensures we
        # release it (close()) when done. read() pulls file data into
        # process memory (data). For very large files you'd stream in
        # chunks instead of loading the whole file into RAM.
        with open(path, 'rb') as f:
            data = f.read()
        framing.send_frame(conn, data)
    except (ConnectionError, BrokenPipeError, OSError):
        pass
    finally:
        # shutdown(SHUT_RDWR) tells the kernel we're done both sending and
        # receiving on this socket; the other side will see EOF on read and
        # can close. Then close() releases the file descriptor so the kernel
        # can reuse that fd number and free the in-kernel socket structures.
        try:
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        except OSError:
            pass


def main():
    # Create a socket: AF_INET = IPv4, SOCK_STREAM = TCP. Under the hood the
    # OS allocates a file descriptor and socket buffers (send/recv queues)
    # in kernel space. The Python socket object wraps that fd.
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR: allow binding to the same port shortly after the previous
    # server exited. Without it, the kernel may keep the (addr,port) in
    # TIME_WAIT and bind() fails with "Address already in use."
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # bind() associates the socket with (listenAddr, listenPort). '' means
    # "all interfaces" (we accept connections to any of the host's IPs).
    # The kernel now knows to route incoming SYNs for this port to this fd.
    listen_sock.bind((listenAddr, listenPort))
    # listen(1): mark the socket as passive (listening). The 1 is the
    # "backlog": how many pending connections the kernel may queue before
    # we accept() them. When a client connects, the kernel completes the TCP
    # handshake and puts the new connection in the backlog until we accept.
    listen_sock.listen(128)
    print("%s: listening on %s:%s (base directory: %s, mode: %s)" % (progname, listenAddr or '0.0.0.0', listenPort, base_dir, mode))

    # ---------------- Part 2 ----------------
    # Concurrent server design (two interchangeable modes):
    # - mode=select: single-threaded select.select() event loop (advanced OS friendly)
    # - mode=threads: one thread per client using handle_client()

    if mode in ("thread", "threads", "t"):
        # Thread-per-client: simplest way to support concurrent clients.
        # The main thread just accepts; each worker thread blocks in recv_frame/read/send_frame.
        while True:
            conn, addr = listen_sock.accept()
            print("Connection from", addr)
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        return

    # Default: select() single-threaded multiplexing.
    class ConnState:
        __slots__ = ("addr", "recv_buf", "need", "send_buf")

        def __init__(self, addr):
            self.addr = addr
            self.recv_buf = bytearray()
            self.need = None  # None => need 4-byte length; else need payload length bytes
            self.send_buf = bytearray()

    def _frame_bytes(payload):
        # Match framing.send_frame(): 4-byte big-endian length prefix.
        import struct
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return struct.pack(">I", len(payload)) + payload

    def _close_state(sock, st, inputs, outputs, states):
        # Best-effort cleanup; used when a client disconnects or an error happens.
        try:
            if sock in inputs:
                inputs.remove(sock)
        except ValueError:
            pass
        try:
            if sock in outputs:
                outputs.remove(sock)
        except ValueError:
            pass
        states.pop(sock, None)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _process_request(req_bytes: bytes) -> bytes:
        msg = req_bytes.decode("utf-8", errors="replace").strip()
        if not msg.upper().startswith("GET "):
            return b"ERROR: Expected GET <filename>"
        filename = msg[4:].strip()
        if not filename:
            return b"ERROR: Missing filename"
        path = safe_path(filename)
        if path is None:
            return b"ERROR: Invalid path"
        if not os.path.isfile(path):
            return ("ERROR: Not a file or not found: %s" % filename).encode("utf-8")
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return b"ERROR: Could not read file"

    def _try_parse_one_frame(st: ConnState):
        # Return one complete framed payload bytes if available, else None.
        import struct
        if st.need is None:
            if len(st.recv_buf) < 4:
                return None
            (length,) = struct.unpack(">I", bytes(st.recv_buf[:4]))
            del st.recv_buf[:4]
            st.need = length
        if len(st.recv_buf) < st.need:
            return None
        payload = bytes(st.recv_buf[: st.need])
        del st.recv_buf[: st.need]
        st.need = None
        return payload

    listen_sock.setblocking(False)
    inputs = [listen_sock]
    outputs = []
    states = {}

    while True:
        readable, writable, _ = select.select(inputs, outputs, [], None)

        for sock in readable:
            if sock is listen_sock:
                # Accept all queued connections.
                while True:
                    try:
                        conn, addr = listen_sock.accept()
                    except BlockingIOError:
                        break
                    conn.setblocking(False)
                    states[conn] = ConnState(addr)
                    inputs.append(conn)
                    print("Connection from", addr)
                continue

            st = states.get(sock)
            if st is None:
                continue

            try:
                chunk = sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                chunk = b""
            except OSError:
                _close_state(sock, st, inputs, outputs, states)
                continue

            if chunk == b"":
                _close_state(sock, st, inputs, outputs, states)
                continue

            st.recv_buf.extend(chunk)
            req = _try_parse_one_frame(st)
            if req is not None:
                resp_payload = _process_request(req)
                st.send_buf = bytearray(_frame_bytes(resp_payload))
                if sock not in outputs:
                    outputs.append(sock)

        for sock in writable:
            st = states.get(sock)
            if st is None:
                continue

            if not st.send_buf:
                _close_state(sock, st, inputs, outputs, states)
                continue

            try:
                sent = sock.send(st.send_buf)
            except (BlockingIOError, InterruptedError):
                sent = 0
            except OSError:
                _close_state(sock, st, inputs, outputs, states)
                continue

            if sent > 0:
                del st.send_buf[:sent]

            if not st.send_buf:
                _close_state(sock, st, inputs, outputs, states)


if __name__ == '__main__':
    main()
