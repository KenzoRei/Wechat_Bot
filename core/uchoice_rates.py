"""Hardcoded U-Choice rate constants — per explicit design choice, not DB tables."""

SHORT_DELIVERY = 30
DELIVERY = 45
TRUCK_TRANSFER = 85
SELF_PICKUP = 0
PALLETIZATION_PER_PALLET = 15
UNPACKING_FLAT = 300
STORAGE_PER_PALLET_PER_DAY = 1

CHARGE_TYPE_RATES = {
    "short_delivery": SHORT_DELIVERY,
    "delivery": DELIVERY,
    "truck_transfer": TRUCK_TRANSFER,
    "self_pickup": SELF_PICKUP,
}

# Explanatory context surfaced to the AI (see ai/prompt_builder.py) so it can
# answer "这几种计费类型有什么区别" without guessing — not just the label + rate.
CHARGE_TYPE_DESCRIPTIONS = {
    "short_delivery": "短途配送：JFK 仓库范围内，$30/托",
    "delivery":        "配送：JFK 附近 10 分钟车程内，$45/托",
    "truck_transfer":  "卡车转仓费：超过 JFK 附近 10 分钟车程（含跨仓库转运），$85/托",
    "self_pickup":     "自提：客户或仓库自行到仓库取货，不产生运输费，$0/托",
}
