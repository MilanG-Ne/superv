#!/usr/bin/env python3
"""Build superv/data/emoji.json from Unicode's emoji-test.txt.

    curl -LO https://unicode.org/Public/emoji/15.1/emoji-test.txt
    python3 tools/gen_emoji.py emoji-test.txt

Keeps fully-qualified emoji, drops skin-tone variants and the "Component"
group. At runtime SuperV also hides emoji the installed fonts can't draw.
"""

import json
import os
import re
import sys

SKIN_TONES = {chr(cp) for cp in range(0x1F3FB, 0x1F400)}
GROUP_ICONS = {
    "Smileys & Emotion": "😀",
    "People & Body": "👋",
    "Animals & Nature": "🐶",
    "Food & Drink": "🍔",
    "Travel & Places": "🚗",
    "Activities": "⚽",
    "Objects": "💡",
    "Symbols": "❤️",
    "Flags": "🏁",
}
LINE = re.compile(r"^([0-9A-F ]+);\s*fully-qualified\s*#\s*(\S+)\s+E(\d+\.\d+)\s+(.+)$")


def main(source, target):
    groups, current = [], None
    header = []
    with open(source, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith("# Version:") or line.startswith("# ©"):
                header.append(line[2:])
            if line.startswith("# group:"):
                name = line.split(":", 1)[1].strip()
                current = None if name == "Component" else {"name": name, "icon": GROUP_ICONS.get(name, ""), "items": []}
                if current:
                    groups.append(current)
                continue
            match = LINE.match(line)
            if not match or current is None:
                continue
            emoji = "".join(chr(int(cp, 16)) for cp in match.group(1).split())
            if SKIN_TONES & set(emoji):
                continue
            current["items"].append([emoji, match.group(4), float(match.group(3))])
    data = {"source": "Unicode emoji-test.txt (%s). Unicode License v3." % "; ".join(header), "groups": groups}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    print("wrote %d emoji in %d groups to %s" % (sum(len(g["items"]) for g in groups), len(groups), target))


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1] if len(sys.argv) > 1 else "emoji-test.txt",
         os.path.join(here, "..", "superv", "data", "emoji.json"))
