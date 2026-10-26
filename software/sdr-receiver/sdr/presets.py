"""Frequency presets kept in config/presets.json."""
import json
import os
import re
import threading

from .config import CONFIG_DIR, mhz_to_hz

VERIFICATION = ("unverified", "listed", "heard")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")


class PresetError(ValueError):
    pass


class PresetStore:
    def __init__(self, settings, path=CONFIG_DIR / "presets.json"):
        self.bands = settings["bands"]
        self.band_order = settings["band_order"]
        self.path = path
        self._lock = threading.Lock()
        self._header = {}
        self._presets = []
        self.problems = []
        self.load()

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self._header = {k: v for k, v in data.items() if k != "presets"}
        presets, problems, seen = [], [], set()
        for raw in data.get("presets", []):
            try:
                p = self._normalise(raw)
                if p["id"] in seen:
                    raise PresetError(f"duplicate id {p['id']}")
                seen.add(p["id"])
                presets.append(p)
            except (PresetError, KeyError, TypeError, ValueError) as e:
                problems.append(f"preset {raw.get('id', '?')}: {e}")
        with self._lock:
            self._presets = presets
            self.problems = problems

    def _normalise(self, raw):
        pid = str(raw["id"])
        if not ID_RE.match(pid):
            raise PresetError("id must be 1-40 chars of a-z, 0-9, '-'")
        band = raw["band"]
        if band not in self.bands:
            raise PresetError(f"unknown band {band}")
        hz = mhz_to_hz(raw["freq_mhz"])
        b = self.bands[band]
        if not b["min_hz"] <= hz <= b["max_hz"]:
            raise PresetError(f"{hz / 1e6:.4f} MHz is outside band {band}")
        digital = raw.get("digital") or None
        verification = raw.get("verification", "unverified")
        if verification not in VERIFICATION:
            raise PresetError(f"verification must be one of {VERIFICATION}")
        return {
            "id": pid,
            "name": str(raw.get("name") or f"{hz / 1e6:.4f} MHz")[:80],
            "band": band,
            "freq_mhz": hz / 1e6,
            "digital": str(digital)[:20] if digital else None,
            "verification": verification,
            # Digital voice cannot be demodulated as FM speech, so it is never scanned.
            "enabled": bool(raw.get("enabled", False)) and not digital,
            "source": str(raw.get("source", ""))[:200],
            "notes": str(raw.get("notes", ""))[:300],
        }

    def _save(self):
        data = dict(self._header)
        data["presets"] = self._presets
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, self.path)

    def all(self):
        with self._lock:
            return [dict(p) for p in self._presets]

    def get(self, pid):
        with self._lock:
            for p in self._presets:
                if p["id"] == pid:
                    return dict(p)
        raise PresetError(f"no preset {pid!r}")

    def scan_list(self):
        """Enabled analogue presets, in band order then frequency."""
        order = {b: i for i, b in enumerate(self.band_order)}
        with self._lock:
            items = [dict(p) for p in self._presets if p["enabled"] and not p["digital"]]
        return sorted(items, key=lambda p: (order[p["band"]], p["freq_mhz"]))

    def add(self, band, freq_mhz, name=None):
        with self._lock:
            hz = mhz_to_hz(freq_mhz)
            base = f"{band[0]}-{hz // 1000}"
            pid, n = base, 2
            while any(p["id"] == pid for p in self._presets):
                pid, n = f"{base}-{n}", n + 1
            p = self._normalise({"id": pid, "band": band, "freq_mhz": freq_mhz, "name": name,
                                 "verification": "unverified", "enabled": band != "fm",
                                 "source": "added from web UI"})
            self._presets.append(p)
            self._save()
            return dict(p)

    def remove(self, pid):
        with self._lock:
            before = len(self._presets)
            self._presets = [p for p in self._presets if p["id"] != pid]
            if len(self._presets) == before:
                raise PresetError(f"no preset {pid!r}")
            self._save()

    def update(self, pid, enabled=None, verification=None):
        with self._lock:
            for p in self._presets:
                if p["id"] != pid:
                    continue
                if enabled is not None:
                    if enabled and p["digital"]:
                        raise PresetError(f"{p['digital']} is digital voice; it cannot be scanned as analogue FM")
                    p["enabled"] = bool(enabled)
                if verification is not None:
                    if verification not in VERIFICATION:
                        raise PresetError(f"verification must be one of {VERIFICATION}")
                    p["verification"] = verification
                self._save()
                return dict(p)
        raise PresetError(f"no preset {pid!r}")
