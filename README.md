# nets-tcp-framing

# nets-tcp-file-transfer

For this lab, you must define a file transfer protocol and implement a
client and server.

Part 1:
* File transfer client and server that utilizes the techniques implemented by your archiver you previously constructed.
* To test its framing, this client and server should function correctly when the client's connection is forwarded by the stammering proxy described below.

Part 2:
* Modify the server to support multiple concurrent clients.
* If you are enrolled in the "advanced operating systems" course, your server should be single-threaded and utilize select().

## File transfer client & server (included)

* **fileTransferServer.py** – Listens for TCP connections; clients send a framed `GET <filename>` and receive the file contents (or an error). Uses length-prefixed framing from `lib/framing.py`. Run with `-l` for port, `-d` for base directory; see `-?` for usage.
* **fileTransferClient.py** – Connects to the server (or through the stammer proxy), sends a framed GET request, and saves the response to a file or stdout. Requires `-f <filename>`; optional `-s server:port`, `-o output_path`. See `-?` for usage.

**Example (direct):**
```bash
./fileTransferServer.py -l 50001 &
./fileTransferClient.py -s 127.0.0.1:50001 -f README.md -o out.md
```

**Example (via stammer proxy):** Start server on 50001, then run proxy (forwards 50000 → 50001), then client to proxy:
```bash
./fileTransferServer.py -l 50001 &
./stammer-proxy/stammerProxy.py -l 50000 -s 127.0.0.1:50001 &
./fileTransferClient.py -s localhost:50000 -f README.md -o out.md
```

Make scripts executable if needed: `chmod +x fileTransferServer.py fileTransferClient.py` (see **STUDENT_GUIDE.md**).

## Other code in this repo

* **echo-demo** – Simple TCP echo server and client.
* **fork-demo** – TCP server that forks a child per client.
* **lib** – `params` (command-line parsing) and `framing` (length-prefixed TCP messages).
* **stammer-proxy** – stammerProxy listens on 50000 and forwards to 50001; use it to test that framing works when data is delivered in small chunks.

## Student guide (Git & executable scripts)

See **STUDENT_GUIDE.md** for:
* How to make a script executable in Linux (`chmod +x`).
* How to make a Python script executable (shebang + `chmod +x`).
* Basic Git commands for beginners.

Useful search queries: “how to make a script executable in linux”, “how to make a python script executable”, “basic git commands for beginners”.
