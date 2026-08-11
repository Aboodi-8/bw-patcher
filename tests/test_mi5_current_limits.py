import unittest

from bwpatcher.modules.mi5 import Mi5Patcher
from bwpatcher.utils import SignatureException


class Mi5CurrentLimitTests(unittest.TestCase):
    ECO = bytes([
        0x48, 0x78, 0xff, 0x25, 0x13, 0x35,
        0x42, 0x4a, 0x67, 0x24, 0x01, 0x28,
    ])
    DRIVE = bytes([
        0x08, 0xe0, 0xff, 0x20, 0x49, 0x30,
        0x10, 0x80, 0x18, 0x88, 0x02, 0xe0,
    ])
    SPORT = bytes([
        0x13, 0xe0, 0xff, 0x20, 0xee, 0x30,
        0x10, 0x80, 0x58, 0x88, 0xf3, 0xe7,
    ])

    def make_patcher(self):
        return Mi5Patcher(b"\x00" + self.ECO + self.DRIVE + self.SPORT + b"\x00")

    def test_stock_values_encode_exactly(self):
        patcher = self.make_patcher()
        self.assertEqual(
            patcher.current_limit_eco(10.0)[0][2:],
            ("ff251335", "ff251335"),
        )
        self.assertEqual(
            patcher.current_limit_drive(12.0)[0][2:],
            ("ff204930", "ff204930"),
        )
        self.assertEqual(
            patcher.current_limit_sport(18.0)[0][2:],
            ("ff20ee30", "ff20ee30"),
        )

    def test_drive_can_use_stock_sport_ceiling(self):
        patcher = self.make_patcher()
        result = patcher.current_limit_drive(18.0)[0]
        self.assertEqual(result[2], "ff204930")
        self.assertEqual(result[3], "ff20ee30")

    def test_twenty_amp_shift_encoding(self):
        patcher = self.make_patcher()
        self.assertEqual(
            patcher.current_limit_eco(20.0)[0][3],
            "8925ad00",
        )
        self.assertEqual(
            patcher.current_limit_sport(20.0)[0][3],
            "89208000",
        )

    def test_limits_outside_test_envelope_are_rejected(self):
        for amps in (4.9, 20.1):
            with self.subTest(amps=amps):
                with self.assertRaises(ValueError):
                    self.make_patcher().current_limit_sport(amps)

    def test_unknown_firmware_is_rejected(self):
        with self.assertRaises(SignatureException):
            Mi5Patcher(bytes(128)).current_limit_drive(12.0)


if __name__ == "__main__":
    unittest.main()
