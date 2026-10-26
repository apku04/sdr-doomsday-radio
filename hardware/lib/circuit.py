"""Board netlists as plain Python: one Part per component, pins mapped to net names."""
import uuid
from dataclasses import dataclass, field

NC = None  # pin explicitly not connected


@dataclass
class Part:
    ref: str
    symbol: str          # "Lib:Name" from the KiCad symbol libraries
    footprint: str       # "Lib:Name" from the KiCad footprint libraries
    value: str
    pins: dict           # pin number (str) -> net name, or NC
    lcsc: str = ""
    group: str = ""      # schematic grouping / layout hint
    note: str = ""
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))


class Circuit:
    def __init__(self, name):
        self.name = name
        self.parts = []

    def add(self, part):
        if any(p.ref == part.ref for p in self.parts):
            raise ValueError(f"duplicate ref {part.ref}")
        self.parts.append(part)
        return part

    def part(self, ref):
        return next(p for p in self.parts if p.ref == ref)

    def nets(self):
        """net name -> sorted list of (ref, pin); NC pins excluded."""
        out = {}
        for p in self.parts:
            for pin, net in p.pins.items():
                if net is not NC:
                    out.setdefault(net, []).append((p.ref, str(pin)))
        return {n: sorted(v) for n, v in sorted(out.items())}
