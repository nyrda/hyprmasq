import socket, sys

connection = socket.socket(socket.AF_UNIX)
connection.connect(f"/tmp/hm-m-{sys.argv[1]}.sock")
connection.sendall(sys.argv[2].encode() + b"\n")
reply = b""
while chunk := connection.recv(65536):
    reply += chunk
sys.stdout.write(reply.decode())
