"""IT6524D supply envelope for the user's nominal 6.4-ohm load, not a coil rating.

Manufacturer: https://www.itechate.com/uploadfiles/2019/08/201908020916331633.pdf
"""
import math

SUPPLY_MAX_VOLTAGE_V = 360.0
SUPPLY_MAX_CURRENT_A = 30.0
SUPPLY_MAX_POWER_W = 3000.0
LOAD_REFERENCE_RESISTANCE_OHM = 6.4
MAX_CURRENT_A = math.floor(min(SUPPLY_MAX_CURRENT_A,
    SUPPLY_MAX_VOLTAGE_V / LOAD_REFERENCE_RESISTANCE_OHM,
    math.sqrt(SUPPLY_MAX_POWER_W / LOAD_REFERENCE_RESISTANCE_OHM)) * 100) / 100
