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
    """Dummy JPEG encoder for simulation purposes"""
    def __init__(self):
        """Initializes a no-op JPEG encoder stub"""
        pass


class FileOutput:
    """Dummy file output for simulation purposes"""
    def __init__(self, dummy):
        """Initializes a no-op file output stub

        Args:
            dummy: Unused placeholder matching the real ``FileOutput`` signature.
        """
        pass


class Picamera2(io.BufferedIOBase):
    """Simulates a Raspberry Pi camera (Picamera2) and its streaming buffer
    
    This class mimics the behavior of the actual camera hardware, providing
    simulated images and a producer-consumer buffer for streaming.
    """
    def __init__(self, max_frames: int = 10):
        """Initializes the simulated camera, its buffer, and control state

        Args:
            max_frames (int, optional): Maximum number of frames the producer-consumer
                buffer can hold. Defaults to 10.
        """
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
        """Initializes the simulated camera"""
        pass

    def autofocus_cycle(self):
        """Simulates an autofocus cycle"""
        pass

    def write(self, data: bytes) -> int:
        """Producer calls this to add a frame to the buffer, blocking if full

        Args:
            data (bytes): Frame bytes to add to the buffer.

        Returns:
            int: Number of bytes written, or 0 if the stream is closed.
        """
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
        """Consumer calls this to retrieve a frame from the buffer, blocking if empty

        Args:
            size (int, optional): Ignored; the whole next frame is returned.
                Defaults to -1.

        Returns:
            bytes: The next frame, or b"" when the stream is closed.
        """
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
        """Copies a frame from the buffer into the provided bytearray

        Args:
            b (bytearray): Buffer to fill with the next frame.

        Returns:
            int: Number of bytes copied, or 0 when no frame is available.
        """
        data = self.read(len(b))
        if not data:
            return 0
        b[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET):
        """Unsupported operation for the streaming buffer

        Args:
            offset (int): Seek offset (ignored).
            whence (int, optional): Seek reference. Defaults to io.SEEK_SET.
        """
        raise io.UnsupportedOperation("Seek not supported on streaming buffer")

    def tell(self) -> int:
        """Unsupported operation for streaming buffer"""
        raise io.UnsupportedOperation("Tell not supported on streaming buffer")

    def flush(self):
        """No-op for in-memory buffer"""
        pass  # In-memory buffer, no flushing needed

    def close(self):
        """Signals EOF and unblocks all waiting threads in the producer-consumer cycle"""
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def get_image_size(self, target):
        """Returns the dimensions of the requested image target ('main' or 'raw')

        Args:
            target (str): Image target key ('main' or 'raw').

        Returns:
            tuple: The (width, height) for the target.
        """
        return self.camera_config[target]["size"]

    def get_color_from_index(self, index):
        """Returns an RGB color based on an index, cycling through the HSV spectrum

        Args:
            index (int): Color index; wraps around the color cycle.

        Returns:
            np.ndarray: The RGB color as a uint8 array.
        """
        return (
            mpl_colors.hsv_to_rgb(
                np.array([self.colors[index % len(self.colors)], 200, 200]) / 255
            )
            * 255
        ).astype(np.uint8)

    def generate_image(self, color: tuple, target) -> bytes:
        """Creates a solid color image of the requested size

        Args:
            color (tuple): RGB fill color.
            target (str): Image target key ('main' or 'raw').

        Returns:
            np.ndarray: The generated solid-color image.
        """
        width, height = self.get_image_size(target)
        return np.full(shape=(height, width, 3), dtype=np.uint8, fill_value=color)

    def generate_frame(self, color: tuple) -> bytes:
        """Generates a solid color image and returns it as JPEG bytes for streaming

        Args:
            color (tuple): RGB fill color.

        Returns:
            bytes: The generated image encoded as JPEG.
        """
        img_bytes = io.BytesIO()
        Image.fromarray(self.generate_image(color, "main"), "RGB").save(
            img_bytes, format="JPEG", quality=85
        )
        return img_bytes.getvalue()

    def stream(self):
        """Background thread loop that continuously generates and buffers frames"""
        while True:
            if self.stop_event.is_set() is True:
                break
            try:
                frame_bytes = self.generate_frame(
                    color=self.get_color_from_index(index=self.color_index)
                )
                self.color_index += 1
                self.write(frame_bytes)
                time.sleep(1 / 24) # Simulate 24 FPS
            except ValueError:
                break

    def create_video_configuration(self, raw: dict = {}, main: dict = {}):
        """Dummy video configuration

        Args:
            raw (dict, optional): Raw stream configuration. Defaults to {}.
            main (dict, optional): Main stream configuration. Defaults to {}.
        """
        pass

    def create_still_configuration(self, raw: dict = {}, main: dict = {}):
        """Dummy still configuration

        Args:
            raw (dict, optional): Raw stream configuration. Defaults to {}.
            main (dict, optional): Main stream configuration. Defaults to {}.
        """
        pass

    def capture_array(self, target):
        """Generates a single image array for the specified target ('main' or 'raw')

        Args:
            target (str): Image target key ('main' or 'raw').

        Returns:
            np.ndarray: The generated image array.
        """
        image = self.generate_image(
            color=self.get_color_from_index(self.color_index), target=target
        )
        self.color_index += 1
        return image

    def capture_metadata(self):
        """Returns dummy camera metadata"""
        return {"LensPosition": 15}

    def stop(self):
        """Stops the simulated camera"""
        pass

    def switch_mode(self, dummy):
        """Dummy mode switch

        Args:
            dummy: Unused placeholder matching the real ``switch_mode`` signature.
        """
        pass

    def configure(self, conf):
        """Dummy configuration update

        Args:
            conf: Configuration to apply (ignored).
        """
        pass

    def set_controls(self, conf):
        """Dummy control update

        Args:
            conf: Controls to apply (ignored).
        """
        pass

    def start_recording(self, encoder, output):
        """Starts the simulated frame production thread

        Args:
            encoder: Encoder to use (unused in simulation).
            output: Output to use (unused in simulation).
        """
        self.stop_event.clear()
        self.prod_thread = Thread(target=self.stream, name="CameraThread")
        self.prod_thread.start()

    def stop_recording(self):
        """Stops the simulated frame production thread"""
        self.stop_event.set()
        self.prod_thread.join()
