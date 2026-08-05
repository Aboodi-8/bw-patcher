#!/usr/bin/env python3
#! -*- coding: utf-8 -*-
#
# BW Patcher - Mi 6 Module
# Copyright (C) 2024-2026 ScooterTeam
#
# This work is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License.
# To view a copy of this license, visit http://creativecommons.org/licenses/by-nc-sa/4.0/
#
# You are free to:
# - Share — copy and redistribute the material in any medium or format
# - Adapt — remix, transform, and build upon the material
#
# Under the following terms:
# - Attribution — You must give appropriate credit, provide a link to the license, and indicate if changes were made.
# - NonCommercial — You may not use the material for commercial purposes.
# - ShareAlike — If you remix, transform, and build upon the material, you must distribute your contributions under the same license as the original.
#

from typing import List, Tuple

from bwpatcher.modules.mi5elite import Mi5elitePatcher
from bwpatcher.utils import find_pattern, SignatureException


class Mi6Patcher(Mi5elitePatcher):
    """Patcher for Xiaomi electric scooter 6 with the LEQI/N32 controller."""

    # Mi6 signed update got a 0x80 header then the encrypted MCU fw is 0xA800.
    # BU1/EU2 comes after it so dont include them in the MCU patch :D.
    FIRMWARE_SIZE = 0xA800

    # this part converts the speed byte to 0.1 km/h and saves it as active limit
    SIG_SPEED_LIMIT_RETURN = [
        0x10, 0x80, 0x47, 0x4A, 0x9C, 0xF8, 0x06, 0x00,
        0x10, 0x80, 0x46, 0x48, 0x47, 0x4A, 0x00, 0x88,
        0x12, 0x88,
    ]

    # region limit part, the branch right after it is used for region free.
    # custom mode speed code goes after that branch.
    SIG_SPEED_LIMIT_DST = [
        0x7A, 0x4F, 0x03, 0x20, 0x38, 0x80,
        0x79, 0x4F, 0x10, 0x46, 0x63, 0x45,
    ]

    # these are next to eachother in the literal pool used by speed code:
    #   0x20000188 - current mode
    #   0x200002D2 - unrelated state
    #   0x2000027E - unrelated state
    #   0x200001A2 - active speed limit
    SIG_MODE_AND_SPEED_LITERALS = [
        0x88, 0x01, 0x00, 0x20,
        0xD2, 0x02, 0x00, 0x20,
        0x7E, 0x02, 0x00, 0x20,
        0xA2, 0x01, 0x00, 0x20,
    ]

    # unrestricted region fallback, it looks like 35.2 km/h internal value
    SIG_SPEED_LIMIT_MAX = [0x4F, 0xF4, 0xB0, 0x70, 0xEB, 0xE7]

    # motor start is abit different from Elite, first value comes from RAM
    # and the other check is a normal immediate value.
    SIG_MOTOR_START = [
        0x01, 0x80, 0x12, 0x48, 0x00, 0x88, 0x83, 0x42,
        0xED, 0xD3, 0x11, 0x70, 0x70, 0xBD, 0x1E, 0x2B,
        0x07, 0xD2,
    ]

    def _locate_speed_patch_offsets(self) -> None:
        """Find the Mi6 speed code and the needed literal offsets."""
        try:
            sig_offset = find_pattern(self.data, self.SIG_SPEED_LIMIT_RETURN)
            self._ldr_patch_offset = sig_offset - 12
        except SignatureException:
            raise Exception("Could not find Mi6 speed limit signature for patching")

        try:
            dst_offset = find_pattern(self.data, self.SIG_SPEED_LIMIT_DST)
            self._speed_logic_offset = dst_offset + len(self.SIG_SPEED_LIMIT_DST) + 2
        except SignatureException:
            raise Exception("Could not find Mi6 speed logic destination")

        self._default_path_address = self._ldr_patch_offset + 6
        self._patched_path_address = self._ldr_patch_offset + 12

        literals_offset = find_pattern(self.data, self.SIG_MODE_AND_SPEED_LITERALS)
        mode_data_addr = literals_offset
        speed_data_addr = literals_offset + 12

        ldr_r0_pc = (self._ldr_patch_offset + 4) & ~0x3
        self._ldr_r0_offset = mode_data_addr - ldr_r0_pc

        ldr_r2_pc = (self._speed_logic_offset + 4) & ~0x3
        self._ldr_r1_offset = speed_data_addr - ldr_r2_pc

        if self._ldr_r0_offset < 0 or self._ldr_r0_offset > 0x3FC:
            raise Exception("Mi6 mode literal is out of range for the source patch")
        if self._ldr_r0_offset % 4 != 0:
            raise Exception("Mi6 mode literal is not word-aligned")
        if abs(self._ldr_r1_offset) > 0xFFF or self._ldr_r1_offset % 4 != 0:
            raise Exception("Mi6 speed literal is out of range or not word-aligned")

    def _build_speed_logic_asm(self) -> str:
        """Make the Mi6 mode speed code, here it uses r2 and ip packet base."""
        asm_code = f"ldr r2, [pc, #{self._ldr_r1_offset}]\n"

        mode_map = {
            "ped": self.MODE_PEDESTRIAN,
            "drive": self.MODE_DRIVE,
            "sport": self.MODE_SPORT,
        }
        mode_checks = [
            mode for mode in ("ped", "drive", "sport")
            if mode in self.patched_speeds
        ]

        for index, mode in enumerate(mode_checks):
            next_label = (
                f"check_{mode_checks[index + 1]}"
                if index + 1 < len(mode_checks)
                else "default_case"
            )
            asm_code += f"""
            check_{mode}:
            cmp r0, #{mode_map[mode]}
            bne {next_label}
            movw r0, #{self.patched_speeds[mode]}
            b {hex(self._patched_path_address)}
            """

        asm_code += f"""
        default_case:
        ldrb.w r0, [ip, #5]
        b {hex(self._default_path_address)}
        """
        return asm_code

    def _speed_limit_fix(self) -> List[Tuple[str, str, str, str]]:
        """Make Mi6 use the unrestricted region fallback path."""
        try:
            ofs_sig = find_pattern(self.data, self.SIG_SPEED_LIMIT_DST)
            branch_target = find_pattern(
                self.data,
                self.SIG_SPEED_LIMIT_MAX,
                start=ofs_sig + len(self.SIG_SPEED_LIMIT_DST),
            )
        except SignatureException:
            return []

        ofs = ofs_sig + len(self.SIG_SPEED_LIMIT_DST)
        post = self.assembly(f"b {hex(branch_target)}", ofs)
        pre = self.data[ofs:ofs + len(post)]

        if pre == post:
            return []

        self.data[ofs:ofs + len(post)] = post
        return [("speed_limit_fix", hex(ofs), pre.hex(), post.hex())]

    def region_free(self) -> List[Tuple[str, str, str, str]]:
        """Put the region speed bypass under the rfm patch."""
        return self._speed_limit_fix()

    def motor_start_speed(self, kmh: float) -> List[Tuple[str, str, str, str]]:
        """Change both Mi6 motor start checks to the selected kmh."""
        speed = self._calc_speed(kmh, size=0)
        if speed < 0 or speed > 0xFF:
            raise ValueError("Mi6 motor start speed must be between 0 and 25.5 km/h")

        ofs_sig = find_pattern(self.data, self.SIG_MOTOR_START)
        results = []

        # replace the RAM load/cmp with direct cmp and 2 nops. r0 isnt used
        # after the old compare on both paths so it should be fine here.
        ofs = ofs_sig + 2
        pre = self.data[ofs:ofs + 6]
        post = self.assembly(f"cmp r3, #{speed}") + self.assembly("nop") * 2
        if len(pre) != len(post):
            raise Exception("Mi6 motor-start primary patch has the wrong size")
        self.data[ofs:ofs + len(post)] = post
        results.append((
            "motor_start_speed_threshold_1",
            hex(ofs),
            pre.hex(),
            post.hex(),
        ))

        ofs = ofs_sig + 14
        pre = self.data[ofs:ofs + 2]
        post = self.assembly(f"cmp r3, #{speed}")
        if len(pre) != len(post):
            raise Exception("Mi6 motor-start secondary patch has the wrong size")
        self.data[ofs:ofs + len(post)] = post
        results.append((
            "motor_start_speed_threshold_2",
            hex(ofs),
            pre.hex(),
            post.hex(),
        ))

        return results
