from enum import Enum
from typing import Literal

import board
import neopixel

PIN = board.D18
LED_COUNT = 12


class CardPoint(Enum):
    NORTH = (0, LED_COUNT // 4 - 1)
    EAST = (LED_COUNT // 4, LED_COUNT // 2 - 1)
    SOUTH = (LED_COUNT // 2, LED_COUNT // 4 * 3 - 1)
    WEST = (LED_COUNT // 4 * 3, LED_COUNT - 1)


class Enderlights:
    def __init__(self, pin=PIN, led_count: int = LED_COUNT):
        self.pixels = neopixel.NeoPixel(PIN, led_count)
        self.led_count = led_count

    def __getitem__(self, index):
        return self.pixels[index]

    def __setitem__(self, index, value):
        self.pixels[index] = value

    def fill(self, value: list | tuple) -> None:
        self.pixels.fill(value)

    def set_slice(self, start: int, end: int, value: tuple):
        self[start:end] = (end - start) * [value]

    def shutter(self, state: bool, value: list | tuple = (255, 255, 255)) -> None:
        if state is True:
            self.fill(value)
        else:
            self.fill((0, 0, 0))

    def red(self, value):
        for i in range(self.led_count):
            self[i] = (value, self[i][1], self[i][2])

    def green(self, value):
        for i in range(self.led_count):
            self[i] = (self[i][0], value, self[i][2])

    def blue(self, value):
        for i in range(self.led_count):
            self[i] = (self[i][0], self[i][0], value)

    def set_cardinal(
        self,
        card_point: Literal[
            CardPoint.NORTH, CardPoint.SOUTH, CardPoint.EAST, CardPoint.WEST
        ],
        value: tuple,
    ):
        start, end = card_point.value
        self[start : end + 1] = (end - start + 1) * [value]


def cycle(lights, size, step, value=(255, 255, 255)):
    for i in range(0, lights.led_count, step):
        lights.shutter(False)
        start, stop = i, min(i + size, lights.led_count)
        lights[start:stop] = size * [value]
        yield start, stop
