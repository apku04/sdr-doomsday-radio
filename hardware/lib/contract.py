"""The one interface contract between HAT, panel board and software (plan: 'Interface contract')."""

# 2x10 IDC ribbon, pin -> net (identical on HAT J2 and panel J1)
PANEL_RIBBON = {
    1: "3V3", 2: "3V3", 3: "GND", 4: "GND", 5: "SDA", 6: "SCL", 7: "GND", 8: "PANEL_INT",
    9: "ENC_VOL_A", 10: "ENC_VOL_B", 11: "ENC_SQL_A", 12: "ENC_SQL_B", 13: "GND", 14: "GND",
    15: "ENC_TUNE_A", 16: "ENC_TUNE_B", 17: "ENC_MENU_A", 18: "ENC_MENU_B", 19: "GND", 20: "GND",
}

# signal -> Raspberry Pi BCM GPIO
PI_BCM = {
    "ENC_VOL_A": 5, "ENC_VOL_B": 6, "ENC_SQL_A": 12, "ENC_SQL_B": 13,
    "ENC_TUNE_A": 22, "ENC_TUNE_B": 23, "ENC_MENU_A": 24, "ENC_MENU_B": 25,
    "PANEL_INT": 17, "LNA_EN": 27, "SDA": 2, "SCL": 3,
}

# BCM GPIO -> 40-pin header pin
BCM_TO_HEADER = {2: 3, 3: 5, 4: 7, 14: 8, 15: 10, 17: 11, 18: 12, 27: 13, 22: 15, 23: 16, 24: 18, 10: 19,
                 9: 21, 25: 22, 11: 23, 8: 24, 7: 26, 0: 27, 1: 28, 5: 29, 6: 31, 12: 32, 13: 33, 19: 35,
                 16: 36, 26: 37, 20: 38, 21: 40}
HEADER_POWER = {1: "3V3", 17: "3V3", 2: "5V", 4: "5V",
                6: "GND", 9: "GND", 14: "GND", 20: "GND", 25: "GND", 30: "GND", 34: "GND", 39: "GND"}

I2C = {"MCP23017": 0x20, "SH1107": 0x3C, "DS3231": 0x68}

# MCP23017 port bit -> function. GPA7/GPB7 are output-only on current silicon.
MCP_PINS = {
    "GPA0": "BTN_SCAN", "GPA1": "BTN_MANUAL", "GPA2": "BTN_Q1", "GPA3": "BTN_Q2", "GPA4": "BTN_BACK",
    "GPA5": "SW_VOL", "GPA6": "SW_SQL", "GPA7": "LED_PWR",
    "GPB0": "SW_TUNE", "GPB1": "SW_MENU", "GPB2": "LED_SCAN", "GPB3": "LED_SIG", "GPB4": "LED_MAN",
    "GPB5": "LED_2M", "GPB6": "LED_70CM", "GPB7": "LED_FM",
}
