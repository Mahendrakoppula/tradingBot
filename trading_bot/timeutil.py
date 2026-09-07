import datetime as dt

# Fixed UTC+5:30 offset, not zoneinfo("Asia/Kolkata") - IST has no DST so a
# fixed offset is exactly correct, and this avoids depending on the host
# having an IANA tzdata database installed at all.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist() -> dt.datetime:
    """Always use this instead of datetime.now() for anything compared
    against ENTRY_TIME/EXIT_TIME or used for the daily-rollover check.

    Verified live (2026-09-07): the EC2 deployment runs with system clock in
    UTC, not IST. A naive datetime.now() there would have made ENTRY_TIME
    12:30 mean 12:30 UTC = 6:00pm IST - hours after market close - silently
    wrong with no error, since the comparison itself is valid Python, just
    against the wrong clock.
    """
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()
