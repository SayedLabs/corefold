"""Temporary: can a normal user read per-connection byte counts (TCP EStats)? Removed before merge."""
import ctypes
import socket
import ssl
import struct
import sys

ip = ctypes.windll.iphlpapi
print("admin:", ctypes.windll.shell32.IsUserAnAdmin())
raw = socket.create_connection(("speed.cloudflare.com", 443))
s = ssl.create_default_context().wrap_socket(raw, server_hostname="speed.cloudflare.com")
la, lp = s.getsockname()[:2]
ra, rp = s.getpeername()[:2]
row = struct.pack("<IIIII", 5, struct.unpack("<I", socket.inet_aton(la))[0], socket.htons(lp),
                  struct.unpack("<I", socket.inet_aton(ra))[0], socket.htons(rp))
rowbuf = ctypes.create_string_buffer(row)
rw = ctypes.create_string_buffer(b"\x01")
r = ip.SetPerTcpConnectionEStats(rowbuf, 1, rw, 0, 1, 0)
print("Set EStats data:", r)
s.sendall(b"GET /__down?bytes=2000000 HTTP/1.1\r\nHost: speed.cloudflare.com\r\nConnection: close\r\n\r\n")
got = 0
while True:
    b = s.recv(65536)
    if not b:
        break
    got += len(b)
print("got", got)
rwo = ctypes.create_string_buffer(1)
rod = ctypes.create_string_buffer(96)
r = ip.GetPerTcpConnectionEStats(rowbuf, 1, rwo, 0, 1, None, 0, 0, rod, 0, 96)
print("Get EStats data:", r, struct.unpack_from("<QQQQ", rod.raw, 0) if r == 0 else "")
sys.stdout.flush()
