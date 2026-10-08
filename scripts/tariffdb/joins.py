"""How a rate joins the TOU windows that say when it applies; shared by validate.py and scripts/billcalc.py.

A rate prices one charge group; a window names the group it applies to (tou_window.applies_to):

  rate                                         group            windows used
  usage, register general                      usage            applies_to usage or all
  usage, register controlled_load              controlled_load  applies_to controlled_load (priced periods), else as usage
  demand, capacity                             demand           applies_to demand or all
  export                                       export           applies_to export, else as usage

Within the group a rate takes the windows with its tou_period and, when either side names one, its season. A rate whose
tou_period is NULL or anytime applies at all times (its season, if any, still limits it to that season's months).
controlled_load_supply windows price nothing: they state when a controlled-load circuit is switched on.
"""

SUPPLY = "controlled_load_supply"
ALL_TIMES = (None, "anytime")


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
    """The windows of one tariff-period that say when a rate applies: [] when it applies at all times."""
    if rate["tou_period"] in ALL_TIMES:
        return []
    return [w for w in group_windows(group_of(rate), windows)
            if w["tou_period"] == rate["tou_period"] and same_season(rate["season"], w["season"])]


def season_months(season, windows):
    """Months of a season: the union of the months of every window of the tariff-period stated for that season;
    None when no window states them."""
    months = set()
    for w in windows:
        if w["season"] == season and w["months"]:
            months |= {int(m) for m in str(w["months"]).split(",")}
    return months or None
