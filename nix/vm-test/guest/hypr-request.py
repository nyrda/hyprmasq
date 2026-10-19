import os, socket, sys
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket.sock")
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(path)
connection.sendall(sys.argv[1].encode())
reply = b""
while chunk := connection.recv(65536):
    reply += chunk
with open(sys.argv[2] + ".part", "wb") as output:
    output.write(reply)
os.rename(sys.argv[2] + ".part", sys.argv[2])
