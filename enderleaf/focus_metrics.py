# FROM: https://github.com/hoogenboom-group/focus-metrics/tree/main

# -*- coding: utf-8 -*-
"""
@Author:    Ryan Lane
@Date:      05-12-2018
@Updated:   21-01-2022
@Modified:  Felicià Maviane Macia
"""

import numpy as np
import cv2
from scipy.ndimage import convolve
from skimage.filters import sobel_h, sobel_v

FM_BREN = "BREN"
FM_FFTE = "FFTE"
FM_GLVA = "GLVA"
FM_GLVN = "GLVN"
FM_GRAE = "GRAE"
FM_GRAT = "GRAT"
FM_GRAS = "GRAS"
FM_LAPE = "LAPE"
FM_LAPM = "LAPM"
FM_LAPM = "LAPM"
FM_LAPV = "LAPV"
FM_LAPD = "LAPD"
FM_SFRQ = "SFRQ"
FM_TENG = "TENG"
FM_TENV = "TENV"
FM_VOLA = "VOLA"
FM_METHODS = [
    FM_BREN,
    # FM_FFTE,
    # FM_GLVA,
    # FM_GLVN,
    # FM_GRAE,
    FM_GRAS,
    FM_GRAT,
    FM_LAPD,
    FM_LAPE,
    FM_LAPM,
    FM_LAPV,
    FM_SFRQ,
]


def BREN(image):
    """Brenner's focus measure

    Reference
    ---------
    [2] Santos et al. (1997).
    """
    image = image.astype(np.int16)
    M, N = image.shape
    DH = np.zeros((M, N))
    DV = np.zeros((M, N))
    DH[:, : N - 2] = np.clip(image[:, 2:] - image[:, :-2], 0, None)
    DV[: M - 2, :] = np.clip(image[2:, :] - image[:-2, :], 0, None)
    FM = np.max((DH, DV), axis=0) ** 2
    return FM.mean()


def GLVA(image):
    """Gray-level variance

    Reference
    ---------
    [5] Krotkov & Martin (1986).
    """
    FM = np.std(image, ddof=1)
    return FM


def GLVN(image):
    """Normalized gray-level variance

    Reference
    ---------
    [2] Santos et al. (1997).
    """
    FM = np.std(image, ddof=1) ** 2 / image.mean()
    return FM


def GRAE(image):
    """Energy of gradient

    Reference
    ---------
    [7] Subbarao et al. (1992).
    """
    image = image.astype(float)
    Ix = image.copy()
    Iy = image.copy()
    Ix[:, :-1] = np.clip(image[:, 1:] - image[:, :-1], a_min=0, a_max=None)
    Iy[:-1, :] = np.clip(image[1:, :] - image[:-1, :], a_min=0, a_max=None)
    FM = np.clip(Ix**2 + Iy**2, a_min=0, a_max=65535)
    return FM.mean()


def GRAT(image, thresh=0):
    """Thresholded gradient

    Reference
    ---------
    [2] Santos et al. (1997).
    """
    image = image.astype(float)
    Ix = image.copy()
    Iy = image.copy()
    Ix[:, :-1] = np.clip(image[:, 1:] - image[:, :-1], a_min=0, a_max=None)
    Iy[:-1, :] = np.clip(image[1:, :] - image[:-1, :], a_min=0, a_max=None)
    FM = np.max([np.abs(Ix), np.abs(Iy)], axis=0)
    FM[FM < thresh] = 0
    return FM.sum() / (FM != 0).sum()


def GRAS(image):
    """Squared gradient

    Reference
    ---------
    [8] Eskicioglu (1995).
    """
    Ix = np.clip(image[:, 1:] - image[:, :-1], a_min=0, a_max=None)
    FM = np.clip(Ix**2, a_min=0, a_max=65535)
    return FM.mean()


def LAPE(image):
    """Energy of Laplacian

    Reference
    ---------
    [7] Subbarao et al. (1992).
    """
    LAP = np.array(
        [[1 / 6, 2 / 3, 1 / 6], [2 / 3, -10 / 3, 2 / 3], [1 / 6, 2 / 3, 1 / 6]]
    )
    conv = convolve(image.astype(float), LAP, mode="nearest")
    conv = np.clip(conv, a_min=0, a_max=65535)
    FM = np.clip(conv**2, a_min=0, a_max=65535)
    return FM.mean()


def LAPM(image):
    """Modified Laplacian

    Reference
    ---------
    [9] Nayar & Nakagawa (1990).
    """
    M = np.array([[0, 0, 0], [-1, 2, -1], [0, 0, 0]])
    Lx = convolve(image.astype(float), M, mode="nearest")
    Ly = convolve(image.astype(float), M.T, mode="nearest")
    Lx = np.clip(Lx, a_min=0, a_max=65535)
    Ly = np.clip(Ly, a_min=0, a_max=65535)
    FM = np.abs(Lx) + np.abs(Ly)
    return FM.mean()


def LAPV(image):
    """Variance of Laplacian

    Reference
    ---------
    [6] Pech-Pacheco et al. (2000).
    """
    laplacian = cv2.Laplacian(image, cv2.CV_64F)
    variance = laplacian.var()
    return variance


def LAPD(image):
    """Diagonal Laplacian

    Reference
    ---------
    [10] Thelen et al. (2008).
    """
    M1 = np.array([[0, 0, 0], [-1, 2, -1], [0, 0, 0]])
    M2 = np.array([[0, 0, -1], [0, 2, 0], [-1, 0, 0]]) / np.sqrt(2)
    M3 = np.array([[-1, 0, 0], [0, 2, 0], [0, 0, -1]]) / np.sqrt(2)
    F1 = convolve(image.astype(float), M1, mode="nearest")
    F1 = np.clip(F1, a_min=0, a_max=65535)
    F2 = convolve(image.astype(float), M2, mode="nearest")
    F2 = np.clip(F2, a_min=0, a_max=65535)
    F3 = convolve(image.astype(float), M3, mode="nearest")
    F3 = np.clip(F3, a_min=0, a_max=65535)
    F4 = convolve(image.astype(float), M1.T, mode="nearest")
    F4 = np.clip(F4, a_min=0, a_max=65535)
    FM = np.abs(F1) + np.abs(F2) + np.abs(F3) + np.abs(F4)
    return FM.mean()


def SFRQ(image):
    """Spatial frequency

    Reference
    ---------
    [8] Eskicioglu (1995).
    """
    image = image.astype(float)
    Ix = np.zeros_like(image)
    Iy = np.zeros_like(image)
    Ix[:, :-1] = np.clip(image[:, 1:] - image[:, :-1], a_min=0, a_max=None)
    Iy[:-1, :] = np.clip(image[1:, :] - image[:-1, :], a_min=0, a_max=None)
    FM = np.sqrt(Ix**2 + Iy**2).mean()
    return FM


def TENG(image):
    """Tenengrad

    Reference
    ---------
    [5] Krotkov & Martin (1986).
    """
    Gx = sobel_h(image.astype(float))
    Gy = sobel_v(image.astype(float))
    FM = Gx**2 + Gy**2
    return FM.mean()


def TENV(image):
    """Tenengrad variance

    Reference
    ---------
    [6] Pech-Pacheco et al. (2000).
    """
    Gx = sobel_h(image.astype(float))
    Gy = sobel_v(image.astype(float))
    FM = Gx**2 + Gy**2
    return FM.std(ddof=1)


def VOLA(image):
    """Vollath's correlation

    Reference
    ---------
    [2] Santos et al. (1997).
    """
    image = image.astype(float)
    I1 = image.copy()
    I2 = image.copy()
    I1[:-1, :] = image[1:, :]
    I2[:-2, :] = image[2:, :]
    FM = image * (I1 - I2)
    return FM.mean()


def FFTE(image):
    """FFT Energy"""
    return np.abs(np.fft.fft2(image)).var()


def compute_focus_metric(image, method: str):
    return globals()[method](image)
