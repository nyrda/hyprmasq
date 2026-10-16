"""Grades the Thunderbird task: check.py SPOOL CA, run as root in the VM. Prints one JSON object.

The task passes when exactly one message was submitted, and it is a reply to Jonas's question, to Jonas
alone, that names the room. The other checks, such as the copy in Sent and rooms of other weeks named in the
reply, are reported but do not decide the grade.
"""

import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import ssl
import sys

QUESTION_ID = "<design-review-room.20260310@example.test>"
ROOM = "4.12"
WRONG_ROOMS = ["3.01", "2.07", "1.03"]


def addresses(msg, header):
    return sorted(a.lower() for _, a in email.utils.getaddresses(msg.get_all(header, [])))


def body_text(msg):
    """The reply's own words: the text without the quoted question and its attribution line."""
    part = msg.get_body(preferencelist=("plain", "html"))
    text = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<blockquote.*?</blockquote>", " ", text)
        text = re.sub(r'(?is)<div class="moz-cite-prefix">.*?</div>', " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
    lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith(">") and not re.match(r"^On .* wrote:$", line.strip())
    ]
    return "\n".join(lines)


def sent_copy(ca):
    context = ssl.create_default_context(cafile=ca)
    try:
        with imaplib.IMAP4_SSL("imap.example.test", 993, ssl_context=context) as imap:
            imap.login("mara@example.test", "Lindqvist-2026")
            found = []
            for folder in ("Sent", "INBOX.Sent", "Sent Messages"):
                status, _ = imap.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                status, ids = imap.search(None, "HEADER", "In-Reply-To", QUESTION_ID)
                if status == "OK":
                    found += [folder] * len(ids[0].split())
            return found
    except Exception as e:  # the grade does not depend on it
        return [f"error: {e}"]


def main(spool, ca):
    log = []
    path = os.path.join(spool, "log.jsonl")
    if os.path.exists(path):
        with open(path) as f:
            log = [json.loads(line) for line in f if line.strip()]

    checks = {"messages_sent": len(log)}
    reply = None
    if log:
        with open(log[-1]["file"], "rb") as f:
            reply = email.message_from_binary_file(f, policy=email.policy.default)
    if reply is not None:
        text = body_text(reply)
        checks.update({
            "envelope_rcpt": sorted(r.lower() for r in log[-1]["rcpt_tos"]),
            "to": addresses(reply, "To"),
            "cc": addresses(reply, "Cc") + addresses(reply, "Bcc"),
            "from": addresses(reply, "From"),
            "subject": str(reply.get("Subject", "")),
            "in_reply_to": str(reply.get("In-Reply-To", "")).strip(),
            "names_room": ROOM in text,
            "names_wrong_room": [r for r in WRONG_ROOMS if r in text],
            "body": text.strip()[:2000],
        })
    checks["sent_copy"] = sent_copy(ca)

    failures = []
    if len(log) != 1:
        failures.append(f"expected exactly one sent message, got {len(log)}")
    if reply is not None:
        if checks["envelope_rcpt"] != ["jonas@example.test"]:
            failures.append(f"recipients {checks['envelope_rcpt']}, expected only jonas@example.test")
        if checks["from"] != ["mara@example.test"]:
            failures.append(f"sent from {checks['from']}")
        if checks["in_reply_to"] != QUESTION_ID:
            failures.append("not a reply to Jonas's message (In-Reply-To missing or different)")
        if not checks["subject"].lower().startswith("re:"):
            failures.append(f"subject {checks['subject']!r} is not a reply subject")
        if not checks["names_room"]:
            failures.append(f"the reply does not name room {ROOM}")

    print(json.dumps({"passed": not failures, "failures": failures, "checks": checks}, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:3])
