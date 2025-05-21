import datetime

def get_month_ranges(year=None):
    if year is None:
        year = datetime.date.today().year

    month_ranges = []
    for month in range(1, 13):
        start = datetime.date(year, month, 1)
        if month == 12:
            end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        month_ranges.append((start.isoformat(), end.isoformat()))
    return month_ranges