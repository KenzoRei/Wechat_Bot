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
    "short_delivery": "短途配送：5 分钟车程内，$30/托",
    "delivery":        "配送：5-20 分钟车程内，$45/托",
    "truck_transfer":  "卡车转仓费：20 分钟以上车程，$85/托",
    "self_pickup":     "自提：无需送货服务，不收费",
}
