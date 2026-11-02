"""SH1107 128x128 OLED over /dev/i2c-N: 5x7 font, frame buffer, background writer.

Raw I2C writes, no PIL / luma needed. Frame layout = SH1107 pages: byte (y // 8) * 128 + x,
bit y % 8. The writer thread sends only pages that changed and reopens the bus after errors.
"""
import logging
import os
import threading

log = logging.getLogger("oled")

_F = {
    " ": "00000 00000 00000 00000 00000 00000 00000",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    ":": "00000 01100 01100 00000 01100 01100 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    "-": "00000 00000 00000 11111 00000 00000 00000", "+": "00000 00100 00100 11111 00100 00100 00000",
    ">": "01000 00100 00010 00001 00010 00100 01000", "<": "00010 00100 01000 10000 01000 00100 00010",
    "/": "00000 00001 00010 00100 01000 10000 00000", "*": "00000 00100 10101 01110 10101 00100 00000",
    "=": "00000 00000 11111 00000 11111 00000 00000", "!": "00100 00100 00100 00100 00100 00000 00100",
    "?": "01110 10001 00001 00010 00100 00000 00100", "%": "11000 11001 00010 00100 01000 10011 00011",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    ",": "00000 00000 00000 00000 01100 00100 01000", "'": "01100 00100 01000 00000 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111", "#": "01010 01010 11111 01010 11111 01010 01010",
}
# Each glyph as 5 column bytes (bit r = row r), for fast unscaled drawing.
_COLS = {k: [sum(1 << r for r, row in enumerate(v.split()) if row[c] == "1") for c in range(5)]
         for k, v in _F.items()}
CHAR_W = 6


class Frame:
    W = H = 128

    def __init__(self):
        self.buf = bytearray(2048)

    def pixel(self, x, y, on=True):
        if 0 <= x < 128 and 0 <= y < 128:
            i, m = (y >> 3) * 128 + x, 1 << (y & 7)
            if on:
                self.buf[i] |= m
            else:
                self.buf[i] &= ~m & 0xFF

    def rect(self, x, y, w, h, on=True):
        for yy in range(max(0, y), min(128, y + h)):
            m, base = 1 << (yy & 7), (yy >> 3) * 128
            for xx in range(max(0, x), min(128, x + w)):
                if on:
                    self.buf[base + xx] |= m
                else:
                    self.buf[base + xx] &= ~m & 0xFF

    def hline(self, x, y, w, on=True):
        self.rect(x, y, w, 1, on)

    def frame_rect(self, x, y, w, h):
        self.rect(x, y, w, 1)
        self.rect(x, y + h - 1, w, 1)
        self.rect(x, y, 1, h)
        self.rect(x + w - 1, y, 1, h)

    def text(self, x, y, s, scale=1, on=True):
        """Draw upper-case text; returns the x after it. Unknown characters draw as '?'."""
        for ch in str(s).upper():
            cols = _COLS.get(ch, _COLS["?"])
            for c, bits in enumerate(cols):
                r = 0
                while bits:
                    if bits & 1:
                        if scale == 1:
                            self.pixel(x + c, y + r, on)
                        else:
                            self.rect(x + c * scale, y + r * scale, scale, scale, on)
                    bits >>= 1
                    r += 1
            x += CHAR_W * scale
        return x


def text_width(s, scale=1):
    return len(str(s)) * CHAR_W * scale


def rotate(buf, rot):
    """Rotate a frame by 0/90/180/270 degrees (to match how the module is mounted)."""
    if rot == 0:
        return bytes(buf)
    out = bytearray(2048)
    for y in range(128):
        base, m = (y >> 3) * 128, 1 << (y & 7)
        for x in range(128):
            if buf[base + x] & m:
                nx, ny = {90: (127 - y, x), 180: (127 - x, 127 - y), 270: (y, 127 - x)}[rot]
                out[(ny >> 3) * 128 + nx] |= 1 << (ny & 7)
    return bytes(out)


# SH1107 128x128 init, as luma.oled sh1107(width=128, height=128)
SH1107_INIT = bytes([0xAE, 0x20, 0xA6, 0xA8, 0x7F, 0xA4, 0xD3, 0x00, 0xD5, 0x80, 0xD9, 0x22, 0xDA, 0x12,
                     0xDB, 0x35, 0x81, 0x7F])


class Oled:
    """Background writer: submit() never blocks on I2C."""

    def __init__(self, bus=1, addr=0x3C, rot=0, contrast=8):
        self.bus, self.addr, self.rot = bus, addr, rot
        self.ok = False
        self._fd, self._sent, self._pending = None, [None] * 16, None
        self._cmds = [self._contrast_cmd(contrast)]
        self._cv = threading.Condition()
        self._stop = False
        threading.Thread(target=self._run, name="oled", daemon=True).start()

    @staticmethod
    def _contrast_cmd(level):
        return bytes([0x00, 0x81, max(0, min(255, int(level) * 17))])

    def submit(self, frame):
        with self._cv:
            self._pending = bytes(frame.buf)
            self._cv.notify()

    def contrast(self, level):
        """level 1..15"""
        with self._cv:
            self._cmds.append(self._contrast_cmd(level))
            self._cv.notify()

    def close(self, blank=True):
        with self._cv:
            if blank:
                self._cmds.append(b"\x00\xAE")   # display off
            self._stop = True
            self._cv.notify()

    def _run(self):
        import fcntl
        while True:
            with self._cv:
                while self._pending is None and not self._cmds and not self._stop:
                    self._cv.wait(5)
                fb, cmds, stop = self._pending, self._cmds, self._stop
                self._pending, self._cmds = None, []
            try:
                if self._fd is None:
                    self._fd = os.open(f"/dev/i2c-{self.bus}", os.O_RDWR)
                    fcntl.ioctl(self._fd, 0x0703, self.addr)   # I2C_SLAVE
                    os.write(self._fd, b"\x00" + SH1107_INIT + b"\xAF")
                    self._sent = [None] * 16
                for c in cmds:
                    os.write(self._fd, c)
                if fb is not None:
                    phys = rotate(fb, self.rot)
                    for page in range(16):
                        row = phys[page * 128:(page + 1) * 128]
                        if self._sent[page] == row:
                            continue
                        os.write(self._fd, bytes([0x00, 0x10, 0x00, 0xB0 | page]))
                        for i in range(0, 128, 32):
                            os.write(self._fd, b"\x40" + row[i:i + 32])
                        self._sent[page] = row
                if not self.ok:
                    log.info("display OK at 0x%02X", self.addr)
                self.ok, self._warned = True, False
            except OSError as e:
                if not getattr(self, "_warned", False):
                    log.warning("display not answering at 0x%02X: %s (retrying every second)", self.addr, e)
                self.ok, self._warned = False, True
                self._close()
                if fb is not None:
                    with self._cv:          # retry the frame on the next round
                        if self._pending is None:
                            self._pending = fb
                    if not stop:
                        threading.Event().wait(1.0)
            if stop:
                self._close()
                return

    def _close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
