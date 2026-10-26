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
    pad_nets: dict = field(default_factory=dict)   # footprint-only pads (e.g. mounting lugs) -> net
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))


class Circuit:
    def __init__(self, name):
        self.name = name
        self.parts = []

    def add(self, part):
        if any(p.ref == part.ref for p in self.parts):
            raise ValueError(f"duplicate ref {part.ref}")
        part.uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sdr-hat/{self.name}/{part.ref}"))  # stable builds
        self.parts.append(part)
        return part

    def part(self, ref):
        return next(p for p in self.parts if p.ref == ref)

    def nets(self, include_pads=False):
        """net name -> sorted list of (ref, pin); NC pins excluded. include_pads adds pad_nets."""
        out = {}
        for p in self.parts:
            items = list(p.pins.items()) + (list(p.pad_nets.items()) if include_pads else [])
            for pin, net in items:
                if net is not NC:
                    out.setdefault(net, []).append((p.ref, str(pin)))
        return {n: sorted(v) for n, v in sorted(out.items())}
