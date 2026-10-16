"""Writes Mara's inbox as a Maildir: mails.py OUT.

Every message has a fixed date and Message-ID, so the inbox is the same on every build. The VM's clock is
pinned to 2026-03-10 09:30 UTC, a Tuesday, so the newest mails are from this morning.
"""

import os
import sys
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

MARA = "Mara Lindqvist <mara@example.test>"

# (date, sender, subject, body, seen). Jonas's question and the two room lists are what the task needs; the
# rest is the usual traffic of a work inbox.
MAILS = [
    ("2026-02-27 16:05", "Facilities <facilities@example.test>", "Room bookings for week 10",
     "Hi all,\n\nhere are the room bookings for next week (week 10):\n\n"
     "  Mon 09:00  Sprint planning   2.07 (Kepler)\n"
     "  Wed 13:00  Budget review     1.03 (Curie)\n"
     "  Thu 14:00  Design review     3.01 (Noether)\n"
     "  Fri 10:00  Retro             2.07 (Kepler)\n\n"
     "Please free the rooms when you are done.\n\nFacilities", True),
    ("2026-03-02 08:12", "Ops Bot <ops@example.test>", "[ops] Weekly uptime report",
     "Uptime last week: 99.97 %.\nIncidents: 1 (INC-2291, resolved).\n", True),
    ("2026-03-02 10:41", "Priya Raman <priya@example.test>", "Sprint 14 goals",
     "Hi Mara,\n\nattached in spirit: the sprint goals we agreed on.\n"
     "1. Ship the export dialog\n2. Fix the sync conflicts\n3. Start the onboarding redesign\n\nPriya", True),
    ("2026-03-02 14:30", "HR <hr@example.test>", "Reminder: submit your timesheet",
     "Please submit last month's timesheet by Friday.\n\nHR", True),
    ("2026-03-03 09:05", "Lukas Brandt <lukas@example.test>", "Re: export dialog copy",
     "I like the second version better. Shorter labels fit the narrow layout.\n\nLukas", True),
    ("2026-03-03 11:47", "Ops Bot <ops@example.test>", "[ops] Deploy 2026.03.03-1 finished",
     "Deploy 2026.03.03-1 finished in 6m12s. No rollbacks.\n", True),
    ("2026-03-03 16:20", "Nora Weiss <nora@example.test>", "Lunch on Thursday?",
     "There is a new ramen place next to the station. Thursday 12:30?\n\nNora", True),
    ("2026-03-04 08:55", "Newsletter <news@devweekly.test>", "Dev Weekly #212: Faster builds",
     "This week: incremental builds, a look at linkers, and three talks worth your time.\n", True),
    ("2026-03-04 13:02", "Priya Raman <priya@example.test>", "Onboarding redesign kickoff",
     "Kickoff is on Monday next week. I will send the notes afterwards.\n\nPriya", True),
    ("2026-03-04 17:44", "Ops Bot <ops@example.test>", "[ops] Disk usage warning on build-03",
     "build-03: /var at 86 %. Cleanup scheduled tonight.\n", True),
    ("2026-03-05 09:30", "Lukas Brandt <lukas@example.test>", "Sync conflict repro",
     "I can reproduce the conflict with two tabs open. Steps are in the ticket.\n\nLukas", True),
    ("2026-03-05 12:15", "IT Service Desk <it@example.test>", "Planned maintenance Saturday",
     "VPN and mail will be unavailable on Saturday from 06:00 to 08:00.\n\nIT", True),
    ("2026-03-05 15:58", "Nora Weiss <nora@example.test>", "Re: Lunch on Thursday?",
     "Booked a table for four.\n\nNora", True),
    ("2026-03-06 10:22", "Calendar <calendar@example.test>", "Updated invitation: Design review",
     "The design review on Thursday, 12 March, now starts at 14:00 instead of 15:00.\n"
     "The room is listed in the facilities booking mail.\n", True),
    ("2026-03-06 16:10", "Facilities <facilities@example.test>", "Room bookings for week 11",
     "Hi all,\n\nhere are the room bookings for next week (week 11):\n\n"
     "  Mon 09:00  Sprint planning   2.07 (Kepler)\n"
     "  Tue 11:00  Hiring sync       1.03 (Curie)\n"
     "  Thu 14:00  Design review     4.12 (Galilei)\n"
     "  Fri 10:00  Retro             2.07 (Kepler)\n\n"
     "Please free the rooms when you are done.\n\nFacilities", True),
    ("2026-03-06 17:35", "Ops Bot <ops@example.test>", "[ops] Deploy 2026.03.06-2 finished",
     "Deploy 2026.03.06-2 finished in 5m48s. No rollbacks.\n", True),
    ("2026-03-07 09:12", "Newsletter <news@devweekly.test>", "Dev Weekly #213: Debugging stories",
     "This week: five debugging stories from production.\n", True),
    ("2026-03-08 19:40", "Lukas Brandt <lukas@example.test>", "Weekend thought on caching",
     "What if we cached the export preview? It would cut the dialog's open time in half.\n\nLukas", True),
    ("2026-03-09 08:03", "Ops Bot <ops@example.test>", "[ops] Weekly uptime report",
     "Uptime last week: 99.99 %.\nIncidents: 0.\n", True),
    ("2026-03-09 09:47", "Priya Raman <priya@example.test>", "Onboarding redesign notes",
     "Notes from the kickoff:\n- Fewer steps\n- Skip the tour for invited users\n- Measure drop-off\n\nPriya",
     True),
    ("2026-03-09 11:20", "HR <hr@example.test>", "Team event survey",
     "Tell us where you would like to go for the team event. The survey closes on Friday.\n\nHR", True),
    ("2026-03-09 15:05", "Nora Weiss <nora@example.test>", "Slides for Thursday",
     "I will bring the slides for the design review on a USB stick, just in case.\n\nNora", True),
    ("2026-03-09 18:30", "Ops Bot <ops@example.test>", "[ops] Deploy 2026.03.09-1 finished",
     "Deploy 2026.03.09-1 finished in 6m01s. No rollbacks.\n", True),
    ("2026-03-10 07:58", "Newsletter <news@devweekly.test>", "Dev Weekly #214: Release trains",
     "This week: release trains, feature flags and how to retire them.\n", False),
    ("2026-03-10 08:21", "Lukas Brandt <lukas@example.test>", "Export dialog PR is up",
     "PR is up, would be great if you could look at it today.\n\nLukas", False),
    ("2026-03-10 08:58", "Jonas Keller <jonas@example.test>", "Design review room?",
     "Hi Mara,\n\nwhich room is the design review on Thursday in? I could not find the booking.\n\n"
     "Thanks,\nJonas", False),
    ("2026-03-10 09:14", "Ops Bot <ops@example.test>", "[ops] Disk usage back to normal on build-03",
     "build-03: /var at 41 %.\n", False),
]

# The task checks the reply against this Message-ID.
QUESTION_ID = "<design-review-room.20260310@example.test>"


def main(out):
    for sub in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    for n, (date, sender, subject, body, seen) in enumerate(MAILS, 1):
        when = datetime.strptime(date, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = MARA
        msg["Subject"] = subject
        msg["Date"] = format_datetime(when)
        msg["Message-ID"] = QUESTION_ID if subject == "Design review room?" else f"<m{n:03d}.bench@example.test>"
        msg.set_content(body)
        stamp = int(when.timestamp())
        name = f"{stamp}.M{n}P1.bench:2,{'S' if seen else ''}"
        path = os.path.join(out, "cur", name)
        with open(path, "wb") as f:
            f.write(bytes(msg))
        os.utime(path, (stamp, stamp))


if __name__ == "__main__":
    main(sys.argv[1])
