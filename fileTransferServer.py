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

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))
import params
import framing

# --- Command-line parameters (see lib/params) ---
switchesVarDefaults = (
    (('-l', '--listenPort'), 'listenPort', 50001),
    (('-d', '--directory'), 'directory', '.'),   # base directory for file requests
    (('-?', '--usage'), 'usage', False),
)

progname = "fileTransferServer"
paramMap = params.parseParams(switchesVarDefaults)
listenPort = paramMap['listenPort']
directory = paramMap['directory']
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
    listen_sock.listen(1)
    print("%s: listening on %s:%s (base directory: %s)" % (progname, listenAddr or '0.0.0.0', listenPort, base_dir))

    # Main loop: accept one client, handle it, then accept the next. Each
    # accept() returns a *new* socket (new fd) for the connection; the
    # listening socket stays open for more connections. This is a single-
    # client-at-a-time design; Part 2 is to support many concurrent clients
    # (e.g. fork() per connection or select()/poll with non-blocking I/O).
    while True:
        conn, addr = listen_sock.accept()
        print("Connection from", addr)
        handle_client(conn, addr)


if __name__ == '__main__':
    main()
