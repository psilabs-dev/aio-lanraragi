import socket

def is_port_available(port: int):
    """
    Checks to see if the port on localhost is available.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
