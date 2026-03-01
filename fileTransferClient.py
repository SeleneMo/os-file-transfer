#!/usr/bin/env python3
"""
File transfer client with length-prefixed framing.
Sends framed "GET <filename>", receives framed file (or error). Works through stammer-proxy.
Example via proxy: ./fileTransferClient.py -s localhost:50000 -f README.md -o out.md
"""

import socket
import sys
import re
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))
import params
import framing

switchesVarDefaults = (
    (('-s', '--server'), 'server', '127.0.0.1:50001'),
    (('-f', '--file'), 'file', 'FILE'),   # file to request (required; default placeholder)
    (('-o', '--output'), 'output', '-'),  # local path to write; '-' means stdout
    (('-?', '--usage'), 'usage', False),
)

progname = "fileTransferClient"
paramMap = params.parseParams(switchesVarDefaults)
server = paramMap['server']
filename = paramMap['file']
output_path = paramMap['output']
if paramMap['usage']:
    params.usage()

if not filename or filename == 'FILE':
    print("Usage: -f <filename> required (file to request from server)")
    params.usage()

try:
    serverHost, serverPort = re.split(r':', server, maxsplit=1)
    serverPort = int(serverPort)
except (ValueError, TypeError) as e:
    print("Can't parse server:port from '%s'" % server)
    sys.exit(1)

# getaddrinfo() resolves the host name and port into one or more (address
# family, socket type, protocol, canonical name, sockaddr) tuples. We try
# each in turn so we support both IPv4 and IPv6 (AF_UNSPEC). The kernel
# doesn't care about the human-readable name; it needs a sockaddr (e.g.
# (host, port) for AF_INET) to connect to. This is the same resolution
# step the OS uses when you connect to a host (DNS lookup, etc.).
s = None
for res in socket.getaddrinfo(serverHost, serverPort, socket.AF_UNSPEC, socket.SOCK_STREAM):
    af, socktype, proto, canonname, sa = res
    try:
        s = socket.socket(af, socktype, proto)
        # connect(sa): the kernel allocates a local (addr, port), sends the
        # TCP SYN, does the 3-way handshake, and queues the socket as
        # "connected." Our process blocks until the connection is established
        # (or fails). After this, send/recv on s transfer data over that
        # single connection.
        s.connect(sa)
        break
    except (socket.error, OSError):
        if s:
            s.close()
        s = None
        continue

if s is None:
    print('Could not connect to %s:%s' % (serverHost, serverPort))
    sys.exit(1)

try:
    # Send one framed message: "GET <filename>". The framing layer adds the
    # 4-byte length prefix and uses sendall() so the entire message is
    # copied into the kernel's send buffer (and eventually transmitted,
    # possibly in segments; the proxy may forward them in small chunks).
    framing.send_frame(s, 'GET %s' % filename)
    # Receive the reply: one framed message. recv_frame() loops internally
    # until it has read the length header and then exactly that many payload
    # bytes. All those bytes are assembled in user space (memory) and
    # returned as a single bytes object. For a large file this can be a
    # lot of RAM; the kernel receive buffer and our buffer both hold the
    # data at some point.
    data = framing.recv_frame(s)
    if not data:
        print("Server closed connection without sending data")
        sys.exit(1)
    if data.startswith(b'ERROR:'):
        print(data.decode('utf-8', errors='replace'))
        sys.exit(1)
    if output_path and output_path != '-':
        # Write the received bytes to a file. The OS allocates (or extends)
        # the file and copies our process's buffer (data) to disk (or page
        # cache). So data flows: kernel socket buffer -> our recv buffer
        # (data) -> kernel file cache -> (eventually) disk.
        with open(output_path, 'wb') as f:
            f.write(data)
        print("Wrote %d bytes to %s" % (len(data), output_path))
    else:
        # stdout is normally a text stream (encoding/decoding). For raw
        # bytes we use the underlying binary buffer so we don't corrupt
        # binary file content (e.g. images) by treating them as text.
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
finally:
    # Shutdown and close: same idea as server. We may get OSError if the
    # server already closed the connection (e.g. after sending the file);
    # that's expected, so we ignore and still close() our fd.
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass
