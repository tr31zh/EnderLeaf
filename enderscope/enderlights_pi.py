from enum import Enum
from typing import Literal, Any
from dataclasses import dataclass

import numpy as np

try:
    import board
    from neopixel import NeoPixel

    sim_needed = False
except:
    sim_needed = True

    class board(Enum):
        D18 = "d18"
        D23 = "d23"

    import numpy as np


class CardPoint(Enum):
    NORTH = "NORTH"
    EAST = "EAST"
    SOUTH = "SOUTH"
    WEST = "WEST"


LIGHTS_CYCLE = [
    [],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.NORTH],
    [CardPoint.WEST],
    [CardPoint.SOUTH],
    [CardPoint.EAST],
    [CardPoint.NORTH, CardPoint.WEST],
    [CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.SOUTH, CardPoint.EAST],
    [CardPoint.EAST, CardPoint.NORTH],
    [CardPoint.NORTH, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.WEST],
]
LEN_LIGHTS_CYCLE = len(LIGHTS_CYCLE)


@dataclass
class LedData:
    pin: Any
    led_count: int


default_leds = {
    "groov_led": LedData(pin=board.D18, led_count=16),
    "hobby_led": LedData(pin=board.D23, led_count=12),
}


class Enderlights:
    def __init__(
        self,
        led_data: LedData = LedData(pin=board.D18, led_count=16),
        brightness: float = 1.0,
    ):
        self.led_data = led_data
        if sim_needed is True:
            self.pixels = np.zeros((self.led_data.led_count, 3))
        else:
            self.pixels = NeoPixel(self.led_data.pin, self.led_data.led_count)
        self.pixels.brightness = brightness

    def __getitem__(self, index):
        return self.pixels[index]

    def __setitem__(self, index, value):
        self.pixels[index] = value

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in ["enabled", "brightness"]}

    def from_json(self, data: dict) -> None:
        for k, v in data.items():
            try:
                setattr(self, k, v)
            except:
                pass

    def cardinal(
        self,
        cardinal_point: Literal[
            CardPoint.NORTH, CardPoint.SOUTH, CardPoint.EAST, CardPoint.WEST
        ],
    ):
        match cardinal_point:
            case CardPoint.NORTH:
                return (0, self.led_count // 4 - 1)
            case CardPoint.EAST:
                return (self.led_count // 4, self.led_count // 2 - 1)
            case CardPoint.SOUTH:
                return (self.led_count // 2, self.led_count // 4 * 3 - 1)
            case CardPoint.WEST:
                return (self.led_count // 4 * 3, self.led_count - 1)

    def set_cardinal(
        self,
        card_point: Literal[
            CardPoint.NORTH, CardPoint.SOUTH, CardPoint.EAST, CardPoint.WEST
        ],
        value: tuple,
    ):
        start, end = self.cardinal(cardinal_point=card_point)
        self[start : end + 1] = (end - start + 1) * [value]

    def set_cardinals(
        self,
        card_points: list,
        value: tuple = (255, 255, 255),
    ):
        self.fill((0, 0, 0))
        for card_point in card_points:
            self.set_cardinal(card_point=card_point, value=value)

    @property
    def brightness(self):
        return self.pixels.brightness

    @brightness.setter
    def brightness(self, value):
        self.pixels.brightness = value

    @property
    def led_count(self):
        return self.led_data.led_count

    @property
    def north(self):
        start, end = self.cardinal(CardPoint.NORTH)
        return self[start : end + 1]

    @north.setter
    def north(self, value):
        self.set_cardinal(CardPoint.NORTH, value)

    @property
    def east(self):
        start, end = self.cardinal(CardPoint.EAST)
        return self[start : end + 1]

    @east.setter
    def east(self, value):
        self.set_cardinal(CardPoint.EAST, value)

    @property
    def south(self):
        start, end = self.cardinal(CardPoint.SOUTH)
        return self[start : end + 1]

    @south.setter
    def south(self, value):
        self.set_cardinal(CardPoint.SOUTH, value)

    @property
    def west(self):
        start, end = self.cardinal(CardPoint.WEST)
        return self[start : end + 1]

    @west.setter
    def west(self, value):
        self.set_cardinal(CardPoint.WEST, value)

    @property
    def mean(self):
        return np.array(self.pixels).mean(axis=0).mean()

    def fill(self, value: list | tuple) -> None:
        if sim_needed is True:
            for i in range(self.pixels.shape[0]):
                self.pixels[i] = value
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
        for i in range(self.led_count):
            self[i] = (value, self[i][1], self[i][2])

    def green(self, value):
        for i in range(self.led_count):
            self[i] = (self[i][0], value, self[i][2])

    def blue(self, value):
        for i in range(self.led_count):
            self[i] = (self[i][0], self[i][0], value)


def cycle(lights: Enderlights, size, step, value=(255, 255, 255)):
    for i in range(0, lights.led_data.led_count, step):
        lights.shutter(False)
        start, stop = i, min(i + size, lights.led_data.led_count)
        lights[start:stop] = size * [value]
        yield start, stop
