"""How a rate joins the TOU windows that say when it applies; shared by validate.py and scripts/billcalc.py.

A rate prices one charge group; a window names the group it applies to (tariff_window_set.applies_to):

  rate                                         group            windows used
  usage, register general                      usage            applies_to usage or all
  usage, register controlled_load              controlled_load  applies_to controlled_load (priced periods), else as usage
  demand, capacity                             demand           applies_to demand or all
  export                                       export           applies_to export, else as usage

Within the group a rate takes the windows with its tou_period and, when either side names one, its season. A rate whose
tou_period is NULL or anytime applies at all times (its season, if any, still limits it to that season's months),
except a usage rate beside usage rates of the same register priced in periods: it prices the rest of the time
(Endeavour N72 'Block 1' beside 'Solar Soak Period'). A demand, capacity or export rate with no period beside windows
stated for its own group that name a period is ambiguous (which window measures it is not stated); validate.py fails it.
A window prices its group when a rate of the group takes it, or a rate with no period (the rest of the time, or all
of it) shares its season. A demand window no rate prices (Endeavour's off-peak demand window) and a window of a
period no rate of its group prices (the price list leaves it unpriced: Energex 92000 lists off-peak and shoulder at 0,
which the build stores as no rate) are information only; a window of a period its group prices, but in no season the
window belongs to, fails validate.py.
controlled_load_supply windows price nothing: they state when a controlled-load circuit is switched on.
"""

SUPPLY = "controlled_load_supply"
ALL_TIMES = (None, "anytime")
# periods the distributor announces as events (dates and hours notified in advance): a rate in one needs windows
# only when the document fixes its hours; otherwise the event times are an input
EVENT_PERIODS = ("critical_peak", "critical_minimum", "dynamic_maximum", "dynamic_minimum")
# bands of one capacity charge (the first kVA up to a minimum, then the rest), not times of day: no window
BANDS = ("capacity_minimum", "capacity_remaining")


def group_of(rate):
    """The charge group of a rate (a dict with charge_type and register), or None for daily, metering and other."""
    ct = rate["charge_type"]
    if ct == "usage":
        return "controlled_load" if rate["register"] == "controlled_load" else "usage"
    if ct in ("demand", "capacity"):
        return "demand"
    if ct == "export":
        return "export"
    return None


def group_windows(group, windows):
    """The windows (dicts with applies_to, tou_period) a charge group of one tariff-period is priced by."""
    def pick(*applies):
        return [w for w in windows if w["applies_to"] in applies and w["tou_period"] != SUPPLY]
    if group == "controlled_load":
        return pick("controlled_load") or pick("usage", "all")
    if group == "export":
        return pick("export") or pick("usage", "all")
    return pick(group, "all")


def same_season(rate_season, window_season):
    return rate_season is None or window_season is None or rate_season == window_season


def rate_windows(rate, windows):
    """The windows of one tariff-period that say when a rate applies: [] when it applies at all times. A seasonal rate
    takes the windows stated for its season when there are any, else the windows stated for no season."""
    if rate["tou_period"] in ALL_TIMES:
        return []
    ws = [w for w in group_windows(group_of(rate), windows)
          if w["tou_period"] == rate["tou_period"] and same_season(rate["season"], w["season"])]
    return [w for w in ws if w["season"] == rate["season"]] or ws


def priced_windows(rate, windows):
    """The windows of one tariff-period a rate prices: its own windows, or for a rate with no period every window of
    its group in its season."""
    if rate["tou_period"] in ALL_TIMES:
        return [w for w in group_windows(group_of(rate), windows) if same_season(rate["season"], w["season"])]
    return rate_windows(rate, windows)


def is_rest(rate, rates):
    """True when a usage rate with no period prices the time its register's period-priced usage rates leave."""
    return rate["charge_type"] == "usage" and rate["tou_period"] in ALL_TIMES and any(
        r["charge_type"] == "usage" and r["register"] == rate["register"] and r["tou_period"] not in ALL_TIMES
        and same_season(rate["season"], r["season"]) for r in rates)


def ambiguous(rate, windows):
    """The periods a demand, capacity or export rate with no period (NULL; anytime is a stated period) could be
    measured in: windows stated for its own group (applies_to demand or export) name them, so 'at all times' is not
    safe to assume. [] when there is no doubt."""
    if rate["charge_type"] not in ("demand", "capacity", "export") or rate["tou_period"] is not None:
        return []
    return sorted({w["tou_period"] for w in windows if w["applies_to"] == group_of(rate)
                   and w["tou_period"] not in ALL_TIMES + (SUPPLY,) and same_season(rate["season"], w["season"])})


def season_named(season, windows):
    """True when a window of the tariff-period belongs to the season, whether or not it lists the months."""
    return any(w["season"] == season for w in windows)


def season_months(season, windows):
    """Months of a season: the union of the months of every window of the tariff-period stated for that season; for
    non_summer with none stated, the months summer leaves (the name defines it); None when no window states them."""
    months = set()
    for w in windows:
        if w["season"] == season and w["months"]:
            months |= {int(m) for m in str(w["months"]).split(",")}
    if not months and season == "non_summer":
        summer = season_months("summer", windows)
        return set(range(1, 13)) - summer if summer else None
    return months or None


# ----------------------------------------------------------------------------------------------- windows of a tariff
def season_dates(parts):
    """(months, dst) of a season from its season_part rows: months = comma-separated month numbers when every part
    spans whole months, dst = 'in' / 'out' for the daylight-saving period or the rest of the year; (None, None) when
    the document names the season without its dates."""
    if not parts:
        return None, None
    if any(p["start_anchor"] for p in parts):
        return None, "in" if parts[0]["start_anchor"] == "dst_start" else "out"
    months = []
    for p in sorted(parts, key=lambda p: int(p["part_no"])):
        m, end = int(p["start_month"]), int(p["end_month"])
        months.append(m)
        while m != end:
            m = m % 12 + 1
            months.append(m)
    return ",".join(str(m) for m in months), None


def tariff_windows(window_sets, seasons, season_parts, time_windows, tariff_window_sets):
    """{(distributor_id, tariff_code, effective_from): [window]} from the window tables (iterables of dicts): each
    window a tariff's charges use, with its set's clock and holiday rule and its season's months, applies_to from the
    link. A window two linked sets both state (a summary and a schedule) is kept once."""
    sets = {s["window_set_id"]: s for s in window_sets}
    parts = {}
    for p in season_parts:
        parts.setdefault(p["season_id"], []).append(p)
    season = {s["season_id"]: (s, *season_dates(parts.get(s["season_id"]))) for s in seasons}
    by_set = {}
    for w in sorted(time_windows, key=lambda w: w["window_id"]):
        by_set.setdefault(w["window_set_id"], []).append(w)
    out, seen = {}, set()
    for link in sorted(tariff_window_sets, key=lambda r: (r["distributor_id"], r["tariff_code"], r["effective_from"],
                                                         r["window_set_id"], r["applies_to"])):
        key = (link["distributor_id"], link["tariff_code"], link["effective_from"])
        ws = sets[link["window_set_id"]]
        for w in by_set.get(link["window_set_id"], []):
            s, months, dst = season[w["season_id"]]
            fact = (key, link["applies_to"], w["tou_period"], w["day_type"], w["start_time"], w["end_time"], months,
                    dst, s["season"])
            if fact in seen:
                continue
            seen.add(fact)
            out.setdefault(key, []).append({
                "window_id": f"{w['window_id']}@{link['applies_to']}", "window_set_id": ws["window_set_id"],
                "applies_to": link["applies_to"], "tou_period": w["tou_period"], "period_label": w["period_label"],
                "day_type": w["day_type"], "start_time": w["start_time"], "end_time": w["end_time"], "months": months,
                "dst": dst, "season": s["season"], "season_label": s["season_label"],
                "time_basis": ws["time_basis"], "public_holidays": ws["public_holidays"],
                "document_id": ws["document_id"], "locator": w["locator"] or ws["locator"]})
    return out
