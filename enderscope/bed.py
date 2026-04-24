from dataclasses import dataclass

@dataclass
class Bed:
    x_lim: tuple = (0, 235)
    y_lim: tuple = (0, 235)
    z_lim: tuple = (0, 235)

    rest_height: int = 100
    individual_height: int = 36

    qr_start_x = 30
    qr_start_y = 30

    row_count: int = 9
    col_count: int = 9

    @property
    def x_min(self):
        return self.x_lim[0]

    @property
    def x_max(self):
        return self.x_lim[1]

    @property
    def y_min(self):
        return self.y_lim[0]

    @property
    def y_max(self):
        return self.y_lim[1]

    @property
    def z_min(self):
        return self.z_lim[0]

    @property
    def z_max(self):
        return self.z_lim[1]


bed = Bed()