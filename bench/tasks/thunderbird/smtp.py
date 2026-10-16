"""A fake SMTP submission server: smtp.py CERT KEY SPOOL.

Listens on 465 (TLS) and 587 (STARTTLS), takes AUTH PLAIN or LOGIN with the accounts below, and delivers
nothing: it writes each accepted message to SPOOL/<n>.eml and a line to SPOOL/log.jsonl, which the checker
reads.
"""

import json
import os
import ssl
import sys
import threading
import time

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword, SMTP

ACCOUNTS = {"mara@example.test": "Lindqvist-2026", "mara": "Lindqvist-2026"}


def authenticator(server, session, envelope, mechanism, auth_data):
    ok = (
        isinstance(auth_data, LoginPassword)
        and ACCOUNTS.get(auth_data.login.decode()) == auth_data.password.decode()
    )
    return AuthResult(success=ok, handled=False)


class Spool:
    def __init__(self, path):
        self.path = path
        self.count = 0

    async def handle_DATA(self, server, session, envelope):
        self.count += 1
        name = os.path.join(self.path, f"{self.count}.eml")
        with open(name, "wb") as f:
            f.write(envelope.original_content)
        with open(os.path.join(self.path, "log.jsonl"), "a") as f:
            f.write(json.dumps({
                "time": time.time(),
                "file": name,
                "mail_from": envelope.mail_from,
                "rcpt_tos": envelope.rcpt_tos,
                "login": session.auth_data.login.decode() if isinstance(session.auth_data, LoginPassword) else None,
            }) + "\n")
        return "250 OK"


class Submission(Controller):
    def __init__(self, handler, port, tls_context, implicit):
        self.tls_context = tls_context
        self.implicit = implicit
        super().__init__(
            handler,
            hostname="0.0.0.0",
            port=port,
            ssl_context=tls_context if implicit else None,
        )

    def factory(self):
        return SMTP(
            self.handler,
            hostname="smtp.example.test",
            tls_context=None if self.implicit else self.tls_context,
            require_starttls=not self.implicit,
            # aiosmtpd does not count implicit TLS as TLS, and would not offer AUTH on 465.
            auth_require_tls=not self.implicit,
            auth_required=True,
            authenticator=authenticator,
        )


def main(cert, key, spool):
    os.makedirs(spool, exist_ok=True)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    handler = Spool(spool)
    servers = [Submission(handler, 465, context, True), Submission(handler, 587, context, False)]
    for server in servers:
        server.start()
    print("fake smtp listening on 465 and 587", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main(*sys.argv[1:4])
