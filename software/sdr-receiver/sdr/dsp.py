"""NFM / WFM demodulators in numpy, processing fixed-size I/Q blocks with filter state.

Signal chain (per block of uint8 I/Q from the dongle):
  to complex -> mix the wanted channel from the tuning offset down to 0 Hz
  -> low-pass + decimate (channel filter) -> channel power in dBFS (signal meter / squelch)
  -> FM discriminator -> [WFM: 50 us de-emphasis] -> audio low-pass + decimate -> int16 PCM

The dongle is tuned `offset_hz` away from the wanted frequency so the RTL2832's DC
spike falls outside the channel filter.
"""
import numpy as np


def lowpass(ntaps, cutoff, fs):
    """Windowed-sinc FIR (Blackman), unity DC gain."""
    n = np.arange(ntaps) - (ntaps - 1) / 2
    h = np.sinc(2 * cutoff / fs * n) * np.blackman(ntaps)
    return (h / h.sum()).astype(np.float32)


class FirDecimator:
    """FIR low-pass + decimation that carries its history across blocks."""

    def __init__(self, taps, decim, dtype):
        self.taps = taps
        self.decim = decim
        self.hist = np.zeros(len(taps) - 1, dtype=dtype)

    def __call__(self, x):
        buf = np.concatenate((self.hist, x))
        self.hist = buf[len(buf) - (len(self.taps) - 1):]
        if np.iscomplexobj(buf):
            y = (np.convolve(buf.real, self.taps, "valid") + 1j * np.convolve(buf.imag, self.taps, "valid"))
        else:
            y = np.convolve(buf, self.taps, "valid")
        return y[::self.decim]


# Mode parameters. Block lengths keep every decimation exact, are multiples of 512 bytes
# (librtlsdr requirement), and contain a whole number of mixer cycles (no phase drift).
MODES = {
    "nfm": dict(fs=240_000, offset_hz=50_000, block=30_720, decim1=5, chan_cut=6_000, taps1=63,
                decim2=3, audio_cut=3_500, taps2=41, deemph_s=None, deviation=5_000, audio_gain=16_000),
    "wfm": dict(fs=1_024_000, offset_hz=250_000, block=131_072, decim1=4, chan_cut=100_000, taps1=31,
                decim2=8, audio_cut=15_000, taps2=41, deemph_s=50e-6, deviation=75_000, audio_gain=32_000),
}


class Demodulator:
    def __init__(self, mode):
        p = MODES[mode]
        self.mode = mode
        self.fs = p["fs"]
        self.offset_hz = p["offset_hz"]
        self.block = p["block"]
        self.block_bytes = 2 * p["block"]
        fs1 = self.fs / p["decim1"]
        self.audio_rate = int(fs1 / p["decim2"])
        n = np.arange(self.block)
        # tuned at f + offset, so the wanted signal sits at -offset: multiply by e^{+j..}
        self.mixer = np.exp(2j * np.pi * self.offset_hz / self.fs * n).astype(np.complex64)
        self.chan = FirDecimator(lowpass(p["taps1"], p["chan_cut"], self.fs), p["decim1"], np.complex64)
        self.audio = FirDecimator(lowpass(p["taps2"], p["audio_cut"], fs1), p["decim2"], np.float32)
        self.fm_gain = fs1 / (2 * np.pi * p["deviation"])  # rad/sample -> +/-1.0 at full deviation
        self.prev = np.complex64(0)
        self.audio_gain = p["audio_gain"]
        if p["deemph_s"]:
            # one-pole de-emphasis approximated by a short FIR (exponential decay), at fs1
            a = np.exp(-1 / (fs1 * p["deemph_s"]))
            k = np.arange(int(8 * fs1 * p["deemph_s"]) + 1)
            h = (1 - a) * a ** k
            self.deemph = FirDecimator((h / h.sum()).astype(np.float32), 1, np.float32)
        else:
            self.deemph = None

    def process(self, raw):
        """raw: uint8 buffer of block_bytes. Returns (int16 PCM bytes, channel power dBFS)."""
        u = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        u -= 127.5
        u *= 1 / 127.5
        x = u.view(np.complex64) * self.mixer
        y = self.chan(x)
        power = float(np.mean(y.real * y.real + y.imag * y.imag))
        dbfs = 10 * np.log10(power + 1e-12)
        d = np.empty(len(y), dtype=np.float32)
        d[0] = np.angle(y[0] * np.conj(self.prev))
        d[1:] = np.angle(y[1:] * np.conj(y[:-1]))
        self.prev = y[-1]
        d *= self.fm_gain
        if self.deemph is not None:
            d = self.deemph(d)
        a = self.audio(d)
        pcm = np.clip(a * self.audio_gain, -32767, 32767).astype("<i2")
        return pcm.tobytes(), dbfs
