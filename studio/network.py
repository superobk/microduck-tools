"""Explicit Studio HTTP/HTTPS endpoints; never broaden the Host/Origin whitelist."""
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

DEFAULT_PORT = 8790
RESERVED_PORTS = {8443, 8765, 8766}


def settings(server_ip, port=DEFAULT_PORT, transport="lan-http"):
    address = ipaddress.ip_address(server_ip)
    if (address.version != 4 or address.is_unspecified or address.is_multicast
            or str(address) == "255.255.255.255"):
        raise ValueError("需要实际IPv4；0.0.0.0仅是监听地址，不能作为访问地址")
    if transport not in ("lan-http", "ssh-loopback-http", "https"):
        raise ValueError("未知Studio传输方式")
    host = "127.0.0.1" if transport == "ssh-loopback-http" else str(address)
    cfg = dict(listen="127.0.0.1" if transport == "ssh-loopback-http" else "0.0.0.0",
               port=port, transport=transport,
               public_origin=f"{'https' if transport == 'https' else 'http'}://{host}:{port}")
    if port in RESERVED_PORTS:
        raise ValueError("端口保留给mediad/Tools/USB工作台，请使用8790或其他空闲端口")
    validate(cfg)
    return cfg


def validate(cfg):
    port = cfg.get("port")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Studio端口须为1024..65535")
    # Older installed HTTPS configurations have no transport field.
    transport = cfg.get("transport", "https" if cfg.get("public_origin", "").startswith("https://") else None)
    if transport not in ("lan-http", "ssh-loopback-http", "https"):
        raise ValueError("HTTP必须明确指定lan-http或ssh-loopback-http")
    origin = urlsplit(cfg.get("public_origin", ""))
    try:
        address = ipaddress.ip_address(origin.hostname or "")
        expected_port = origin.port
    except ValueError as error:
        raise ValueError("访问地址须为实际IPv4及匹配端口") from error
    if (address.version != 4 or address.is_unspecified or address.is_multicast
            or str(address) == "255.255.255.255"):
        raise ValueError("访问地址不能是监听通配/组播/广播地址")
    scheme = "https" if transport == "https" else "http"
    if (origin.scheme != scheme or expected_port != port
            or cfg["public_origin"] != f"{scheme}://{address}:{port}"):
        raise ValueError("public_origin须精确匹配协议、IPv4和端口")
    bind = cfg.get("listen")
    if bind not in ("0.0.0.0", "127.0.0.1", str(address)):
        raise ValueError("监听地址与访问地址不匹配")
    if transport == "ssh-loopback-http" and (bind != "127.0.0.1" or str(address) != "127.0.0.1"):
        raise ValueError("SSH模式必须仅监听并访问127.0.0.1")
    return transport


def ensure_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("0.0.0.0", port))
        except OSError as error:
            raise RuntimeError(f"端口{port}已占用或不可绑定；不停止其他duck服务，请选择其他端口") from error


def tls_context(cfg):
    if validate(cfg) != "https":
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cfg["cert"], cfg["key"])
    return context
