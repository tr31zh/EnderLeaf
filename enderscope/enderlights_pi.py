from enum import Enum
from typing import Any
from collections.abc import Generator
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

from enderleaf.enums import CardPoint


@dataclass
class LedData:
    """Holds the pin and LED count configuration for a ring

    Attributes:
        pin (Any): GPIO pin the ring is wired to.
        led_count (int): Number of LEDs in the ring.
    """

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
        """Initialize the LED ring controller

        Args:
            led_data (LedData): LED pin and count configuration.
            brightness (float, optional): Initial brightness. Defaults to 1.0.
        """
        self.led_data = led_data
        if sim_needed is True:
            self.pixels = np.zeros((self.led_data.led_count, 3))
            self._brightness = brightness
        else:
            self.pixels = NeoPixel(self.led_data.pin, self.led_data.led_count)
            self.pixels.brightness = brightness

    def __getitem__(self, index: int | slice):
        """Return the pixel value at the given index

        Args:
            index (int | slice): Pixel index or slice.

        Returns:
            tuple: RGB pixel value.
        """
        return self.pixels[index]

    def __setitem__(self, index: int | slice, value: tuple | list) -> None:
        """Set the pixel value at the given index

        Args:
            index (int | slice): Pixel index or slice.
            value (tuple | list): RGB pixel value.
        """
        self.pixels[index] = value

    def cardinal(self, cardinal_point: CardPoint) -> tuple[int, int]:
        """Return the start and end pixel indices for a cardinal quarter

        Args:
            cardinal_point (CardPoint): Cardinal quarter of the ring.

        Returns:
            tuple[int, int]: (start, end) pixel indices of the quarter.
        """
        match cardinal_point:
            case CardPoint.NORTH:
                return (0, self.led_count // 4 - 1)
            case CardPoint.EAST:
                return (self.led_count // 4, self.led_count // 2 - 1)
            case CardPoint.SOUTH:
                return (self.led_count // 2, self.led_count // 4 * 3 - 1)
            case CardPoint.WEST:
                return (self.led_count // 4 * 3, self.led_count - 1)

    def set_cardinal(self, card_point: CardPoint, value: tuple) -> None:
        """Set all pixels of a cardinal quarter to a value

        Args:
            card_point (CardPoint): Cardinal quarter of the ring.
            value (tuple): RGB value to set.
        """
        start, end = self.cardinal(cardinal_point=card_point)
        self[start : end + 1] = (end - start + 1) * [value]

    def set_cardinals(
        self,
        card_points: list[CardPoint],
        value: tuple = (255, 255, 255),
    ) -> None:
        """Set all pixels of several cardinal quarters to a value

        Args:
            card_points (list[CardPoint]): Cardinal quarters to set.
            value (tuple, optional): RGB value to set. Defaults to (255, 255, 255).
        """
        self.fill((0, 0, 0))
        for card_point in card_points:
            self.set_cardinal(card_point=card_point, value=value)

    @property
    def brightness(self) -> float:
        """Get the ring brightness

        Returns:
            float: Current brightness.
        """
        return self._brightness if sim_needed else self.pixels.brightness

    @brightness.setter
    def brightness(self, value: float) -> None:
        """Set the ring brightness

        Args:
            value (float): Brightness value.
        """
        if sim_needed:
            self._brightness = value
        else:
            self.pixels.brightness = value

    @property
    def led_count(self) -> int:
        """Get the number of LEDs in the ring

        Returns:
            int: Number of LEDs.
        """
        return self.led_data.led_count

    @property
    def north(self) -> np.ndarray:
        """Get the pixels of the north quarter

        Returns:
            np.ndarray: Pixels of the north quarter.
        """
        start, end = self.cardinal(CardPoint.NORTH)
        return self[start : end + 1]

    @north.setter
    def north(self, value: tuple) -> None:
        """Set the pixels of the north quarter

        Args:
            value (tuple): RGB value to set.
        """
        self.set_cardinal(CardPoint.NORTH, value)

    @property
    def east(self) -> np.ndarray:
        """Get the pixels of the east quarter

        Returns:
            np.ndarray: Pixels of the east quarter.
        """
        start, end = self.cardinal(CardPoint.EAST)
        return self[start : end + 1]

    @east.setter
    def east(self, value: tuple) -> None:
        """Set the pixels of the east quarter

        Args:
            value (tuple): RGB value to set.
        """
        self.set_cardinal(CardPoint.EAST, value)

    @property
    def south(self) -> np.ndarray:
        """Get the pixels of the south quarter

        Returns:
            np.ndarray: Pixels of the south quarter.
        """
        start, end = self.cardinal(CardPoint.SOUTH)
        return self[start : end + 1]

    @south.setter
    def south(self, value: tuple) -> None:
        """Set the pixels of the south quarter

        Args:
            value (tuple): RGB value to set.
        """
        self.set_cardinal(CardPoint.SOUTH, value)

    @property
    def west(self) -> np.ndarray:
        """Get the pixels of the west quarter

        Returns:
            np.ndarray: Pixels of the west quarter.
        """
        start, end = self.cardinal(CardPoint.WEST)
        return self[start : end + 1]

    @west.setter
    def west(self, value: tuple) -> None:
        """Set the pixels of the west quarter

        Args:
            value (tuple): RGB value to set.
        """
        self.set_cardinal(CardPoint.WEST, value)

    @property
    def mean(self) -> float:
        """Compute the mean pixel value across all LEDs

        Returns:
            float: Mean pixel value.
        """
        return np.array(self.pixels).mean(axis=0).mean()

    def fill(self, value: list | tuple) -> None:
        """Set every pixel of the ring to a value

        Args:
            value (list | tuple): RGB value to fill the ring with.
        """
        if sim_needed is True:
            for i in range(self.pixels.shape[0]):
                self.pixels[i] = value
        else:
            self.pixels.fill(value)

    def set_slice(self, start: int, end: int, value: tuple) -> None:
        """Set a range of pixels to a value

        Args:
            start (int): Start index (inclusive).
            end (int): End index (exclusive).
            value (tuple): RGB value to set.
        """
        self[start:end] = (end - start) * [value]

    def shutter(self, state: bool, value: list | tuple = (255, 255, 255)) -> None:
        """Open or close the shutter, lighting or blanking the whole ring

        Args:
            state (bool): True to light the ring with value, False to blank it.
            value (list | tuple, optional): RGB value when open. Defaults to (255, 255, 255).
        """
        if state:
            self.fill(value)
        else:
            self.fill((0, 0, 0))

    def red(self, value: int) -> None:
        """Set the red channel of every pixel

        Args:
            value (int): Red channel value.
        """
        for i in range(self.led_count):
            self[i] = (value, self[i][1], self[i][2])

    def green(self, value: int) -> None:
        """Set the green channel of every pixel

        Args:
            value (int): Green channel value.
        """
        for i in range(self.led_count):
            self[i] = (self[i][0], value, self[i][2])

    def blue(self, value: int) -> None:
        """Set the blue channel of every pixel

        Args:
            value (int): Blue channel value.
        """
        for i in range(self.led_count):
            self[i] = (self[i][0], self[i][0], value)

    def rgb(self, color: tuple) -> None:
        """Set every pixel to the given color

        Args:
            color (tuple): RGB value.
        """
        for i in range(self.led_count):
            self[i] = color


def cycle(
    lights: Enderlights,
    size: int,
    step: int,
    value: tuple = (255, 255, 255),
) -> Generator[tuple[int, int], None, None]:
    """Yield lit slices of the ring one at a time, for animation

    Args:
        lights (Enderlights): Ring controller to animate.
        size (int): Length of each lit slice.
        step (int): Step between successive slices.
        value (tuple, optional): RGB value of the lit slice. Defaults to (255, 255, 255).

    Yields:
        tuple[int, int]: (start, stop) indices of the currently lit slice.
    """
    for i in range(0, lights.led_data.led_count, step):
        lights.shutter(False)
        start, stop = i, min(i + size, lights.led_data.led_count)
        lights[start:stop] = size * [value]
        yield start, stop
