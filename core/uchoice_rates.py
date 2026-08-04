"""Hardcoded U-Choice rate constants — per explicit design choice, not DB tables."""

SHORT_DELIVERY = 30
DELIVERY = 45
TRUCK_TRANSFER = 85
PALLETIZATION_PER_PALLET = 15
UNPACKING_FLAT = 300
STORAGE_PER_PALLET_PER_DAY = 1

CHARGE_TYPE_RATES = {
    "short_delivery": SHORT_DELIVERY,
    "delivery": DELIVERY,
    "truck_transfer": TRUCK_TRANSFER,
}
