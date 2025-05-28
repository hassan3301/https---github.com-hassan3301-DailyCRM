import datetime
import dateparser

def parse_date(text):
    if not text:
        return None
    parsed = dateparser.parse(
        text,
        settings={
            "RELATIVE_BASE": datetime.datetime.now(),
            "PREFER_DATES_FROM": "future"
        }
    )
    return parsed