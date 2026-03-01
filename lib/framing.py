# Framing helpers for TCP: length-prefixed messages (4-byte big-endian).
# Ensures full send/recv so protocols work correctly through the stammering proxy.
#
# === WHY FRAMING? (OS / network context) ===
# TCP gives you a byte *stream*, not a sequence of messages. There are no
# built-in message boundaries: if the sender does three send() calls, the
# receiver might get the data in one recv(), two recvs(), or many small
# recvs() depending on kernel buffer sizes, network segments, and timing.
# The "stammering" proxy deliberately forwards bytes in small, random-sized
# chunks. So we must define our own message boundaries. We do that by
# sending a fixed-length header (4 bytes) that says "the next N bytes are
# one logical message." The receiver then reads exactly 4 bytes (the length),
# then exactly N bytes, and can reassemble one complete message regardless
# of how the OS or proxy delivered the bytes.

import struct


def send_frame(sock, data):
    """Send one framed message: 4-byte length (big-endian) then payload."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    length = len(data)
    # struct.pack('>I', length) produces exactly 4 bytes in memory:
    #   '>' = big-endian (network byte order; same order used in IP/TCP headers)
    #   'I' = unsigned int (4 bytes). Those 4 bytes are written into a small
    # buffer and then sent. We send the length first so the receiver knows
    # how many bytes to read for the payload.
    sock.sendall(struct.pack('>I', length))
    # sendall() is critical: send() may transmit only *part* of the buffer
    # (kernel send buffer full, or partial copy). sendall() loops inside the
    # OS/glibc until all bytes are copied into the kernel's socket send
    # buffer (or the connection fails). Without it, we might send "4" bytes
    # of length then only part of the payload, and the receiver would get
    # inconsistent state.
    sock.sendall(data)


def recv_frame(sock):
    """Receive one framed message. Returns bytes, or b'' on EOF."""
    # First read exactly 4 bytes (the length field). TCP can deliver these
    # 4 bytes in 1, 2, 3, or 4 separate recv() calls; _recv_exact hides
    # that and returns only when we have all 4 (or EOF).
    len_buf = _recv_exact(sock, 4)
    if not len_buf:
        return b''
    # struct.unpack interprets the 4 bytes as one big-endian unsigned int.
    # That number is the length of the payload that follows in the stream.
    (length,) = struct.unpack('>I', len_buf)
    return _recv_exact(sock, length)


def _recv_exact(sock, n):
    """
    Read exactly n bytes from sock, or return b'' on EOF.

    OS/network reality: recv(n) is allowed to return *fewer* than n bytes.
    It returns "what's available now" in the kernel's receive buffer (or
    one TCP segment's worth), not "wait until n bytes exist." So we must
    loop: accumulate bytes into a buffer until we have n (or the stream
    ends). The buffer is a bytearray so we can extend it in place without
    allocating a new str/bytes each time. When we have n bytes, we return
    an immutable bytes() copy for the caller.
    """
    buf = bytearray()
    while len(buf) < n:
        # Request up to (n - len(buf)) bytes. The kernel may return less:
        # e.g. only one segment (often ~1460 bytes), or less if that's
        # all that's buffered. Zero bytes means the other side closed the
        # connection (EOF).
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return b''
        buf.extend(chunk)
    return bytes(buf)
