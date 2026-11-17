"""BGL (Blue Gene/L, Loghub) parsing and table schema.

One raw line:
    <alert_label> <epoch> <yyyy.mm.dd> <location> <yyyy-mm-dd-hh.mm.ss.ffffff> <location> <type> <component> <level> <message>
`alert_label` is "-" for non-alert lines, otherwise an alert category such as KERNDTLB.

Locations encode the physical hierarchy rack -> midplane -> node card -> card -> chip, e.g.
    R02-M1-N0-C:J12-U11   compute card J12 on node card N0, midplane M1, rack R02
    R02-M1-NC-I:J18-U01   I/O card
    R63-M0-L0-U19-A       link card
    R02-M1-N0             node card itself
"""

from __future__ import annotations

import re
from datetime import datetime

TABLE = "bgl_events"
LAST_DAY = "2006-01-04"  # last event in the log

# Used as table/column comments, so the SQL agent sees them through SQLDatabase.
TABLE_COMMENT = (
    "Event log of the Blue Gene/L supercomputer (LLNL, 2005-06 .. 2006-01). One row per log event. "
    "Rows with a non-null alert_label were tagged as real alerts by the system administrators."
)
COLUMNS: list[tuple[str, str, str]] = [
    ("event_id", "BIGINT", "Line number in the original log, unique."),
    ("alert_label", "STRING", "Alert category assigned by admins (e.g. KERNDTLB, APPSEV, KERNMNTF); NULL for non-alert events."),
    ("is_alert", "BOOLEAN", "True when alert_label is not NULL."),
    ("event_ts", "TIMESTAMP", "Event timestamp (microsecond precision, local time of the machine)."),
    ("event_date", "DATE", "Calendar date of the event."),
    ("location", "STRING", "Full hardware location that reported the event, e.g. R02-M1-N0-C:J12-U11, or NULL/UNKNOWN_LOCATION."),
    ("rack", "STRING", "Rack, e.g. R02. First part of location."),
    ("midplane", "STRING", "Midplane within the rack, e.g. R02-M1."),
    ("node_card", "STRING", "Node card within the midplane, e.g. R02-M1-N0. NULL for link cards and non-hardware locations."),
    ("card_type", "STRING", "COMPUTE (C:), IO (I:), LINK (L), NODE_CARD (location is the node card itself) or OTHER."),
    ("msg_type", "STRING", "Message source type, almost always RAS."),
    ("component", "STRING", "Reporting software component: KERNEL, APP, DISCOVERY, MMCS, HARDWARE, MONITOR, LINKCARD, ..."),
    ("level", "STRING", "Severity: INFO, WARNING, ERROR, SEVERE, FATAL, FAILURE."),
    ("message", "STRING", "Free-text event message."),
    ("event_template", "STRING", "Message with numbers and hex values replaced by <N>; groups events of the same type."),
]

_NUM = re.compile(r"0x[0-9a-fA-F]+|\d+")
_LOC = re.compile(r"^(R[0-9A-F]{2})-(M\d)(?:-(N[0-9A-F]|L\d)(?:-(C|I):J\d+-U\d+|-U\d+-[A-Z])?)?$")


def template_of(message: str) -> str:
    return _NUM.sub("<N>", message)[:300]


def split_location(loc: str) -> tuple[str | None, str | None, str | None, str]:
    m = _LOC.match(loc)
    if not m:
        return None, None, None, "OTHER"
    rack, mid, sub, card = m.groups()
    midplane = f"{rack}-{mid}"
    if sub is None:
        return rack, midplane, None, "OTHER"
    if sub.startswith("L"):
        return rack, midplane, None, "LINK"
    node_card = f"{midplane}-{sub}"
    card_type = {"C": "COMPUTE", "I": "IO"}.get(card or "", "NODE_CARD")
    return rack, midplane, node_card, card_type


def parse_line(line_no: int, line: str) -> tuple | None:
    parts = line.rstrip("\r\n").split(" ", 9)
    if len(parts) < 9:
        return None
    if len(parts) == 9:  # empty message
        parts.append("")
    label, _epoch, _date, loc, ts, _loc2, msg_type, component, level, message = parts
    try:
        event_ts = datetime.strptime(ts, "%Y-%m-%d-%H.%M.%S.%f")
    except ValueError:
        return None
    rack, midplane, node_card, card_type = split_location(loc)
    alert = None if label == "-" else label
    return (
        line_no, alert, alert is not None, event_ts.isoformat(sep=" "), event_ts.date().isoformat(),
        loc, rack, midplane, node_card, card_type, msg_type, component, level, message, template_of(message),
    )
