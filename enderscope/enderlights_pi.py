from enum import Enum
from typing import Literal, Any
from dataclasses import dataclass

try:
    import board
    from neopixel import NeoPixel

    sim_needed = False
except:
    sim_needed = True

    class board(Enum):
        D18 = "d18"

    import numpy as np


class CardPoint(Enum):
    NORTH = "NORTH"
    EAST = "EAST"
    SOUTH = "SOUTH"
    WEST = "WEST"


@dataclass
class LedData:
    pin: Any
    led_count: int

    def cardinal(
        self,
        cardinal_point: Literal[
            CardPoint.NORTH, CardPoint.SOUTH, CardPoint.EAST, CardPoint.WEST
        ],
    ):
        match cardinal_point:
            case CardPoint.NORTH:
                return self.north
            case CardPoint.EAST:
                return self.east
            case CardPoint.SOUTH:
                return self.south
            case CardPoint.WEST:
                return self.west

    @property
    def north(self):
        return (0, self.led_count // 4 - 1)

    @property
    def east(self):
        return (self.led_count // 4, self.led_count // 2 - 1)

    @property
    def south(self):
        return (self.led_count // 2, self.led_count // 4 * 3 - 1)

    @property
    def west(self):
        return (self.led_count // 4 * 3, self.led_count - 1)


# Grrove LED PIN 18, 16 LEDs
# Hobby LED PIN XX, 12 LEDs


class Enderlights:
    def __init__(self, led_data: LedData = LedData(pin=board.D18, led_count=16)):
        self.led_data = led_data
        if sim_needed is True:
            self.pixels = np.zeros((self.led_data.led_count, 3))
        else:
            self.pixels = NeoPixel(self.led_data.pin, self.led_data.led_count)

    def __getitem__(self, index):
        return self.pixels[index]

    def __setitem__(self, index, value):
        self.pixels[index] = value

    def fill(self, value: list | tuple) -> None:
        if sim_needed is True:
            for i in range(self.pixels.shape[0]):
                self.pixels[i] = [3,3,3]
        else:
            self.pixels.fill(value)

    def set_slice(self, start: int, end: int, value: tuple):
        self[start:end] = (end - start) * [value]

    def shutter(self, state: bool, value: list | tuple = (255, 255, 255)) -> None:
        if state is True:
            self.fill(value)
        else:
            self.fill((0, 0, 0))

    def red(self, value):
        for i in range(self.led_data.led_count):
            self[i] = (value, self[i][1], self[i][2])

    def green(self, value):
        for i in range(self.led_data.led_count):
            self[i] = (self[i][0], value, self[i][2])

    def blue(self, value):
        for i in range(self.led_data.led_count):
            self[i] = (self[i][0], self[i][0], value)

    def set_cardinal(
        self,
        card_point: Literal[
            CardPoint.NORTH, CardPoint.SOUTH, CardPoint.EAST, CardPoint.WEST
        ],
        value: tuple,
    ):
        start, end = self.led_data.cardinal(cardinal_point=card_point)
        self[start : end + 1] = (end - start + 1) * [value]


def cycle(lights: Enderlights, size, step, value=(255, 255, 255)):
    for i in range(0, lights.led_data.led_count, step):
        lights.shutter(False)
        start, stop = i, min(i + size, lights.led_data.led_count)
        lights[start:stop] = size * [value]
        yield start, stop
