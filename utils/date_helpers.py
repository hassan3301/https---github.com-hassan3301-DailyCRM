import datetime
import dateparser

def parse_date(text):
    settings = {
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": datetime.datetime.now(),
        "PREFER_DAY_OF_MONTH": "first",  # fallback if day is missing
        "RETURN_AS_TIMEZONE_AWARE": False,
        "DATE_ORDER": "MDY",
    }
    parsed = dateparser.parse(text, settings=settings)
    return parsed