"""Minimal ctypes binding to the system librtlsdr (Debian package librtlsdr0)."""
import ctypes
import ctypes.util

_lib = ctypes.CDLL(ctypes.util.find_library("rtlsdr") or "librtlsdr.so.0")
_p = ctypes.c_void_p

READ_CB = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32, ctypes.c_void_p)

_lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
_lib.rtlsdr_open.argtypes = [ctypes.POINTER(_p), ctypes.c_uint32]
for name, args in {
    "rtlsdr_close": [_p],
    "rtlsdr_set_center_freq": [_p, ctypes.c_uint32],
    "rtlsdr_set_sample_rate": [_p, ctypes.c_uint32],
    "rtlsdr_set_freq_correction": [_p, ctypes.c_int],
    "rtlsdr_set_tuner_gain_mode": [_p, ctypes.c_int],
    "rtlsdr_set_tuner_gain": [_p, ctypes.c_int],
    "rtlsdr_set_agc_mode": [_p, ctypes.c_int],
    "rtlsdr_reset_buffer": [_p],
    "rtlsdr_read_sync": [_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
    "rtlsdr_read_async": [_p, READ_CB, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32],
    "rtlsdr_cancel_async": [_p],
}.items():
    getattr(_lib, name).argtypes = args
    getattr(_lib, name).restype = ctypes.c_int


class RtlSdrError(OSError):
    pass


def _check(r, what):
    if r < 0:
        raise RtlSdrError(f"{what} failed ({r})")
    return r


def device_count():
    return _lib.rtlsdr_get_device_count()


class RtlSdr:
    """One open dongle. Settings may be changed from another thread while read_async runs
    (as rtl_tcp does); everything else belongs to one thread."""

    def __init__(self, index=0):
        self._dev = _p()
        if device_count() <= index:
            raise RtlSdrError("RTL-SDR not found: check the USB connection")
        _check(_lib.rtlsdr_open(ctypes.byref(self._dev), index), "rtlsdr_open")
        self._buf = None
        self._cb = None

    def close(self):
        if self._dev:
            _lib.rtlsdr_close(self._dev)
            self._dev = _p()

    def set_sample_rate(self, rate):
        _check(_lib.rtlsdr_set_sample_rate(self._dev, int(rate)), "set_sample_rate")

    def set_center_freq(self, hz):
        _check(_lib.rtlsdr_set_center_freq(self._dev, int(hz)), "set_center_freq")

    def set_ppm(self, ppm):
        r = _lib.rtlsdr_set_freq_correction(self._dev, int(ppm))
        if r < 0 and r != -2:  # -2: value unchanged
            raise RtlSdrError(f"set_freq_correction failed ({r})")

    def set_gain(self, gain_db):
        """gain_db: float dB, or 'auto' for the tuner's AGC."""
        _check(_lib.rtlsdr_set_agc_mode(self._dev, 0), "set_agc_mode")
        if gain_db == "auto":
            _check(_lib.rtlsdr_set_tuner_gain_mode(self._dev, 0), "set_tuner_gain_mode")
        else:
            _check(_lib.rtlsdr_set_tuner_gain_mode(self._dev, 1), "set_tuner_gain_mode")
            _check(_lib.rtlsdr_set_tuner_gain(self._dev, int(round(float(gain_db) * 10))), "set_tuner_gain")

    def reset_buffer(self):
        _check(_lib.rtlsdr_reset_buffer(self._dev), "reset_buffer")

    def read(self, nbytes):
        """Blocking read of interleaved uint8 I/Q, exactly nbytes."""
        if self._buf is None or len(self._buf) != nbytes:
            self._buf = (ctypes.c_uint8 * nbytes)()
        n = ctypes.c_int(0)
        _check(_lib.rtlsdr_read_sync(self._dev, self._buf, nbytes, ctypes.byref(n)), "read_sync")
        if n.value != nbytes:
            raise RtlSdrError(f"short read {n.value}/{nbytes}")
        return self._buf

    def read_async(self, callback, buf_len, buf_num=8):
        """Blocks until cancel_async(); callback(bytes) is called for every filled buffer."""
        def cb(buf, n, _ctx):
            callback(ctypes.string_at(buf, n))
        self._cb = READ_CB(cb)  # keep a reference while librtlsdr may call it
        return _lib.rtlsdr_read_async(self._dev, self._cb, None, buf_num, buf_len)

    def cancel_async(self):
        if self._dev:
            _lib.rtlsdr_cancel_async(self._dev)
