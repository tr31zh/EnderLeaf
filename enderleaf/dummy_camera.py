import io
from threading import Thread, Condition, Event, Lock
import collections
import time
import numpy as np
from PIL import Image
from matplotlib import colors as mpl_colors

IMG_WIDTH = 4608
IMG_HEIGHT = 2592


class JpegEncoder:
    def __init__(self):
        pass


class FileOutput:
    def __init__(self, dummy):
        pass


class Picamera2(io.BufferedIOBase):
    def __init__(self, max_frames: int = 10):
        super().__init__()
        # Buffer
        self._buffer = collections.deque(maxlen=max_frames)
        self._lock = Lock()
        self._not_empty = Condition(self._lock)
        self._not_full = Condition(self._lock)
        self._closed = False

        # Camera simulation
        self.sensor_modes = [
            {
                "bit_depth": 10,
                "crop_limits": (696, 528, 2664, 1980),
                "exposure_limits": (31, 66512892),
                "format": "SRGGB10_CSI2P",
                "fps": 120.05,
                "size": (1332, 990),
                "unpacked": "SRGGB10",
            },
            {
                "bit_depth": 12,
                "crop_limits": (0, 440, 4056, 2160),
                "exposure_limits": (60, 127156999),
                "format": "SRGGB12_CSI2P",
                "fps": 50.03,
                "size": (2028, 1080),
                "unpacked": "SRGGB12",
            },
            {
                "bit_depth": 12,
                "crop_limits": (0, 0, 4056, 3040),
                "exposure_limits": (60, 127156999),
                "format": "SRGGB12_CSI2P",
                "fps": 40.01,
                "size": (2028, 1520),
                "unpacked": "SRGGB12",
            },
            {
                "bit_depth": 12,
                "crop_limits": (0, 0, 4056, 3040),
                "exposure_limits": (114, 239542228),
                "format": "SRGGB12_CSI2P",
                "fps": 10.0,
                "size": (4056, 3040),
                "unpacked": "SRGGB12",
            },
        ]
        self.camera_config = {
            "main": {"size": (1280, 720)},
            "raw": {"size": (4608, 2592)},
        }
        self.colors = list(range(0, 255, 1))
        self.color_index = 0
        self.prod_thread = None
        self.stop_event = Event()
        self.camera_controls = {"LensPosition": (0, 15, 1)}

    def start(self):
        pass

    def autofocus_cycle(self):
        pass

    def write(self, data: bytes) -> int:
        """Producer calls this. Blocks if buffer is full."""
        if self._closed:
            raise ValueError("I/O operation on closed stream")
        with self._not_full:
            while len(self._buffer) >= self._buffer.maxlen:
                if self._closed:
                    return 0
                self._not_full.wait()  # Block producer until space is available
            self._buffer.append(data)
            self._not_empty.notify()  # Wake up consumer
        return len(data)

    def read(self, size: int = -1) -> bytes:
        """Consumer calls this. Blocks if buffer is empty."""
        if self._closed and not self._buffer:
            return b""
        with self._not_empty:
            while not self._buffer:
                if self._closed:
                    return b""
                self._not_empty.wait()  # Block consumer until frame is available
            data = self._buffer.popleft()
            self._not_full.notify()  # Wake up producer
        return data

    def readinto(self, b: bytearray) -> int:
        """Required by io.BufferedIOBase. Copies frame into provided buffer."""
        data = self.read(len(b))
        if not data:
            return 0
        b[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET):
        raise io.UnsupportedOperation("Seek not supported on streaming buffer")

    def tell(self) -> int:
        raise io.UnsupportedOperation("Tell not supported on streaming buffer")

    def flush(self):
        pass  # In-memory buffer, no flushing needed

    def close(self):
        """Signals EOF. Unblocks all waiting threads gracefully."""
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def get_image_size(self, target):
        return self.camera_config[target]["size"]

    def get_color_from_index(self, index):
        return (
            mpl_colors.hsv_to_rgb(
                np.array([self.colors[index % len(self.colors)], 200, 200]) / 255
            )
            * 255
        ).astype(np.uint8)

    def generate_image(self, color: tuple, target) -> bytes:
        width, height = self.get_image_size(target)
        return np.full(shape=(height, width, 3), dtype=np.uint8, fill_value=color)

    def generate_frame(self, color: tuple) -> bytes:
        """Generates a random RGB image and returns it as JPEG bytes."""
        img_bytes = io.BytesIO()
        Image.fromarray(self.generate_image(color, "main"), "RGB").save(
            img_bytes, format="JPEG", quality=85
        )
        return img_bytes.getvalue()

    def stream(self):
        """Simulates PiCamera capturing and buffering generated frames."""
        while True:
            if self.stop_event.is_set() is True:
                break
            try:
                frame_bytes = self.generate_frame(
                    color=self.get_color_from_index(index=self.color_index)
                )
                self.color_index += 1
                self.write(frame_bytes)
                time.sleep(1 / 24)
            except ValueError:
                break

    def create_video_configuration(self, raw: dict = {}, main: dict = {}):
        pass

    def create_still_configuration(self, raw: dict = {}, main: dict = {}):
        pass

    def capture_array(self, target):
        image = self.generate_image(
            color=self.get_color_from_index(self.color_index), target=target
        )
        self.color_index += 1
        return image

    def capture_metadata(self):
        return {"LensPosition": 15}

    def stop(self):
        pass

    def switch_mode(self, dummy):
        pass

    def configure(self, conf):
        pass

    def set_controls(self, conf):
        pass

    def start_recording(self, encoder, output):
        self.stop_event.clear()
        self.prod_thread = Thread(target=self.stream, name="CameraThread")
        self.prod_thread.start()

    def stop_recording(self):
        self.stop_event.set()
        self.prod_thread.join()
