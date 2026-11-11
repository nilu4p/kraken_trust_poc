"""Vessel watchlist: Combined IUU Vessel List (TMT / RFMOs) + analyst entries.

The Combined IUU list (https://www.iuu-vessels.org/Home/Download) is
distributed as an Excel file behind a short form, so administrators import it.
Accepted formats, detected from the *content* (not the extension):
- XLS (Excel 97-2003 binary, BIFF) — via ``xlrd``;
- XLS that is really an HTML table or SpreadsheetML (XML 2003), as many web
  "Excel" exports are — stdlib parsers;
- XLSX — stdlib zip/XML reader;
- CSV (comma, semicolon or tab).
Columns are mapped by header name.
Matches (MMSI, IMO or normalised name) flag AIS tracks and GFW events, and an
AIS-visible listed vessel inside the EEZ opens an incident on its own.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
import unicodedata
import zipfile
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from defusedxml import ElementTree  # uploaded spreadsheets: no entity expansion / external entities

from app.config import Settings, get_settings
from app.core import audit
from app.db import db, read_db, rows

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

FIELD_HEADERS = {
    "name": ("vessel name", "name", "nom", "current name"),
    "mmsi": ("mmsi",),
    "imo": ("imo",),
    "call_sign": ("call sign", "callsign", "ircs", "indicatif"),
    "flag": ("flag", "pavillon"),
    "reason": ("rfmo", "listing", "reason", "list", "motif"),
}


def name_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def call_sign_key(value: Any) -> str:
    """Radio call sign normalised for matching ('CPA 226' → 'CPA226')."""
    key = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    # Placeholders such as "4SFXXXX" or "UNKNOWN" would create false matches.
    if len(key) < 3 or "XXX" in key or key in ("UNKNOWN", "NONE", "NA"):
        return ""
    return key


def _digits(value: Any, length: Optional[int] = None) -> Optional[str]:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = re.sub(r"\D", "", str(value or ""))
    if not text or (length and len(text) != length):
        return None
    return text


def _xlsx_sheets(data: bytes) -> List[List[List[str]]]:
    """Every worksheet of an XLSX as lists of string rows (stdlib only)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if sum(archive.getinfo(n).file_size for n in names) > 20 * MAX_IMPORT_BYTES:
            raise ValueError("Fichier trop volumineux une fois décompressé")
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t")))
        sheet_names = sorted(
            (n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
            key=lambda n: int(re.sub(r"\D", "", n) or 0),
        )
        if not sheet_names:
            raise ValueError("Aucune feuille dans le classeur")
        roots = [ElementTree.fromstring(archive.read(n)) for n in sheet_names]
    return [_xlsx_sheet_rows(root, shared) for root in roots]


def _xlsx_sheet_rows(root: Any, shared: List[str]) -> List[List[str]]:
    result: List[List[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: Dict[int, str] = {}
        for cell in row.findall("m:c", _NS):
            ref = re.match(r"([A-Z]+)", cell.get("r", "A"))
            col = 0
            for ch in ref.group(1) if ref else "A":
                col = col * 26 + ord(ch) - 64
            value = cell.find("m:v", _NS)
            inline = cell.find("m:is", _NS)
            if cell.get("t") == "s" and value is not None:
                text = shared[int(value.text)] if value.text and int(value.text) < len(shared) else ""
            elif inline is not None:
                text = "".join(t.text or "" for t in inline.iter(f"{{{_NS['m']}}}t"))
            else:
                text = value.text if value is not None and value.text else ""
            cells[col - 1] = text.strip()
        if cells:
            result.append([cells.get(i, "") for i in range(max(cells) + 1)])
        if len(result) > MAX_ROWS:
            break
    return result


OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _cell_text(value: Any) -> str:
    """Excel stores numbers as floats: 9123456.0 → "9123456"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() if value is not None else ""


def _xls_sheets(data: bytes) -> List[List[List[str]]]:
    """Every sheet of a binary Excel 97-2003 file (BIFF)."""
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency listed in requirements
        raise ValueError("Lecture XLS indisponible sur le serveur (module xlrd manquant)") from exc
    book = xlrd.open_workbook(file_contents=data, on_demand=True, logfile=io.StringIO())
    sheets = []
    for index in range(book.nsheets):
        sheet = book.sheet_by_index(index)
        sheets.append([[_cell_text(v) for v in sheet.row_values(i)] for i in range(min(sheet.nrows, MAX_ROWS + 20))])
    book.release_resources()
    return sheets


class _HtmlTable(HTMLParser):
    """Rows of the first <table> of an HTML page saved with an .xls extension."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._tables = 0

    def handle_starttag(self, tag: str, _attrs: Any) -> None:
        if tag == "table":
            self._tables += 1
        elif tag == "tr" and self._tables == 1:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _html_rows(text: str) -> List[List[str]]:
    parser = _HtmlTable()
    parser.feed(text)
    return parser.rows[: MAX_ROWS + 20]


def _spreadsheetml_sheets(data: bytes) -> List[List[List[str]]]:
    """Excel 2003 XML (SpreadsheetML) — every Worksheet."""
    ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
    root = ElementTree.fromstring(data)
    return [_spreadsheetml_table(table, ns) for table in root.findall("ss:Worksheet/ss:Table", ns)]


def _spreadsheetml_table(sheet: Any, ns: Dict[str, str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in sheet.findall("ss:Row", ns):
        cells: List[str] = []
        for cell in row.findall("ss:Cell", ns):
            index = cell.get(f"{{{ns['ss']}}}Index")
            if index and index.isdigit():
                cells.extend([""] * (int(index) - 1 - len(cells)))
            value = cell.find("ss:Data", ns)
            cells.append("".join(value.itertext()).strip() if value is not None else "")
        rows.append(cells)
        if len(rows) > MAX_ROWS + 20:
            break
    return rows


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def detect_format(data: bytes) -> str:
    """Real format from the content: xls | xlsx | html | spreadsheetml | csv."""
    if data[:8] == OLE2_MAGIC:
        return "xls"
    if data[:2] == b"PK":
        return "xlsx"
    head = _decode(data[:4096]).lstrip().lower()
    if head.startswith("<?xml") and "urn:schemas-microsoft-com:office:spreadsheet" in _decode(data[:20000]).lower():
        return "spreadsheetml"
    if head.startswith("<") and ("<table" in _decode(data[:200000]).lower()):
        return "html"
    return "csv"


# Generic files: header → field, exact names first, then prefix / substring.
FIELD_HEADERS = {
    "name": ("vessel name", "name", "nom", "nom du navire", "ship name", "shipname", "current name"),
    "mmsi": ("mmsi",),
    "imo": ("imo", "imo number", "imo no", "numéro imo"),
    "call_sign": ("ircs", "call sign", "callsign", "indicatif", "indicatif radio"),
    "flag": ("flag", "pavillon", "flag state", "current flag"),
    "reason": ("reason", "motif", "listing reason", "raison", "rfmo"),
}
# Headers that contain a hint but mean something else (TMT: OwnerName, RFMOName, CurrentlyListed…).
FALSE_FRIENDS = ("owner", "operator", "rfmoname", "currentlylisted", "builtin", "previous", "former")


def _map_columns(header: List[str]) -> Dict[str, Optional[int]]:
    lowered = [h.lower().strip() for h in header]
    mapping: Dict[str, Optional[int]] = {}
    for field, hints in FIELD_HEADERS.items():
        exact = next((i for i, h in enumerate(lowered) if h in hints), None)
        loose = next(
            (i for i, h in enumerate(lowered)
             if h and not any(f in h.replace(" ", "") for f in FALSE_FRIENDS) and any(h.startswith(x) or x in h for x in hints)),
            None,
        )
        mapping[field] = exact if exact is not None else loose
    return mapping


def _clean_name(raw: str) -> str:
    """'  LU RONG YUAN YU 197, 鲁荣远 渔 197 ' → 'LU RONG YUAN YU 197'."""
    text = " ".join(str(raw or "").split())
    latin = text.split(",")[0].strip()
    return latin or text.strip(" ,")


def _aliases(*names: str) -> List[str]:
    """RFMO names, with bracketed alternatives: 'ABUNDANT 1 (YI HONG 06)' → both."""
    out: List[str] = []
    for name in names:
        text = " ".join(str(name or "").split())
        if not text or text.lower() in ("unknown", "n/a", "-"):
            continue
        for part in re.split(r"[()/;]", text):
            part = _clean_name(part)
            if len(name_key(part)) >= 4:
                out.append(part)
    return out


def _period(text: str) -> Tuple[str, str, str]:
    parts = re.split(r"\s+-(?:\s+|$)", text.strip(), maxsplit=1)
    since = parts[0].strip(" -")
    until = parts[1].strip(" -") if len(parts) > 1 else ""
    return since, "-", until


def _tmt_entries(header: List[str], table: List[List[str]]) -> Tuple[List[Dict[str, Any]], int]:
    """Combined IUU Vessel List (TMT) layout: one row per vessel, RFMO / Reason column pairs."""
    lowered = [h.lower().strip() for h in header]
    col = {name: lowered.index(name) for name in dict.fromkeys(lowered) if name}
    pairs = [
        (header[i].strip(), i, i + 1)
        for i in range(len(header) - 1)
        if lowered[i + 1] == "reason" and lowered[i] not in ("reason", "")
    ]
    entries: List[Dict[str, Any]] = []
    delisted = 0
    for row in table:
        def cell(name: str) -> str:
            i = col.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        if not any(c.strip() for c in row):
            continue
        if cell("currentlylisted").lower() not in ("true", "1", "yes", "oui", "vrai"):
            delisted += 1
            continue
        listings = []
        for rfmo, period_col, reason_col in pairs:
            period = row[period_col].strip() if period_col < len(row) else ""
            if not period:
                continue
            # "2008-11-24 - " (still listed) or "2005-12-26 - 2012-06-21" (dates contain dashes too).
            since, _, until = _period(period)
            listings.append({
                "rfmo": rfmo,
                "since": since or None,
                "until": until or None,
                "reason": (row[reason_col].strip() if reason_col < len(row) else "") or None,
            })
        active = [l for l in listings if not l["until"]] or listings
        original = [l for l in active if l["reason"] and l["reason"].lower() != "cross-listing"]
        cross = [l["rfmo"] for l in active if not l["reason"] or l["reason"].lower() == "cross-listing"]
        reason_parts = [f"{l['rfmo']} ({l['since'] or '?'}) : {l['reason']}" for l in original]
        if cross:
            reason_parts.append("Inscription croisée : " + ", ".join(cross))
        name = _clean_name(cell("name"))
        flag = cell("flag")
        entries.append(
            {
                "name": name,
                "mmsi": cell("mmsi"),
                "imo": cell("imo"),
                "call_sign": cell("ircs"),
                "flag": "" if flag.lower() == "unknown" else flag,
                "reason": " · ".join(reason_parts)[:300],
                "aliases": [a for a in _aliases(cell("rfmoname")) if name_key(a) != name_key(name)],
                "details": {
                    "vessel_type": cell("vesseltype") or None,
                    "gear": cell("geartype") or None,
                    "length_m": cell("length") or None,
                    "gross_tonnage": cell("gt") or None,
                    "owner": cell("ownername") or None,
                    "operator": cell("operatorname") or None,
                    "status": cell("vesselstatus") or None,
                    "national_registration": cell("nnr") or None,
                    "listings": listings,
                    "full_name": " ".join(cell("name").split()) or None,
                },
            }
        )
    return entries, delisted


def _sheets(data: bytes, kind: str) -> List[List[List[str]]]:
    if kind == "xls":
        return _xls_sheets(data)
    if kind == "xlsx":
        return _xlsx_sheets(data)
    if kind == "html":
        return [_html_rows(_decode(data))]
    if kind == "spreadsheetml":
        return _spreadsheetml_sheets(data)
    text = _decode(data)
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
    return [[row for row in csv.reader(io.StringIO(text), dialect)][:MAX_ROWS]]


def parse_report(data: bytes) -> Dict[str, Any]:
    """Parse any supported file; returns entries plus what was recognised (format, sheet, layout)."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Fichier limité à 10 Mo")
    kind = detect_format(data)
    sheets = _sheets(data, kind)
    for sheet_index, table in enumerate(sheets):
        for index, header in enumerate(table[:20]):
            lowered = [h.lower().strip() for h in header]
            if "currentlylisted" in lowered and "name" in lowered:
                entries, delisted = _tmt_entries(header, table[index + 1:])
                return {"entries": entries, "format": kind, "sheet": sheet_index + 1, "layout": "Combined IUU (TMT)",
                        "skipped_delisted": delisted}
            mapping = _map_columns(header)
            if mapping["name"] is not None and any(mapping[f] is not None for f in ("imo", "mmsi", "flag", "call_sign")):
                entries = []
                for row in table[index + 1:]:
                    def cell(field: str) -> str:
                        i = mapping[field]
                        return row[i].strip() if i is not None and i < len(row) else ""

                    entry: Dict[str, Any] = {field: cell(field) for field in FIELD_HEADERS}
                    entry["name"] = _clean_name(entry["name"])
                    if entry["name"] or entry["mmsi"] or entry["imo"]:
                        entries.append(entry)
                return {"entries": entries, "format": kind, "sheet": sheet_index + 1, "layout": "générique",
                        "skipped_delisted": 0}
    raise ValueError(
        f"En-têtes non reconnus dans les {len(sheets)} feuille(s) : attendu une colonne de nom de navire "
        "et au moins une colonne IMO, MMSI, indicatif (IRCS) ou pavillon"
    )


def parse_table(data: bytes) -> List[Dict[str, Any]]:
    """Entries only (see :func:`parse_report`)."""
    return parse_report(data)["entries"]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def import_entries(
    entries: Iterable[Dict[str, str]], *, source: str, actor: str, settings: Optional[Settings] = None
) -> int:
    """Replace every entry of ``source`` with the imported ones; returns the count."""
    now = _now()
    values = [
        (
            source,
            e.get("name", "")[:200],
            name_key(e.get("name", "")),
            _digits(e.get("mmsi"), 9),
            _digits(e.get("imo"), 7),
            call_sign_key(e.get("call_sign")) or None,
            (e.get("flag") or "")[:60] or None,
            (e.get("reason") or "")[:300],
            now,
            actor,
            ",".join(k for k in (name_key(a) for a in e.get("aliases") or []) if len(k) >= 4),
            json.dumps(e.get("details") or {}, ensure_ascii=False),
        )
        for e in entries
    ]
    with db(settings) as conn:
        conn.execute("DELETE FROM watchlist WHERE source = ?", (source,))
        conn.executemany(
            "INSERT INTO watchlist (source, name, name_key, mmsi, imo, call_sign, flag, reason, added_at, added_by, "
            "alias_keys, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    audit.record("watchlist.import", actor=actor, target=f"watchlist:{source}", details={"entries": len(values)}, settings=settings)
    return len(values)


def add_entry(entry: Dict[str, str], *, actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    mmsi, imo = _digits(entry.get("mmsi"), 9), _digits(entry.get("imo"), 7)
    name = (entry.get("name") or "").strip()[:200]
    if not (mmsi or imo or name):
        raise ValueError("Renseignez au moins un MMSI (9 chiffres), un IMO (7 chiffres) ou un nom")
    with db(settings) as conn:
        created = dict(conn.execute(
            "INSERT INTO watchlist (source, name, name_key, mmsi, imo, call_sign, flag, reason, added_at, added_by) "
            "VALUES ('manual', ?, ?, ?, ?, NULL, ?, ?, ?, ?) RETURNING *",
            (name, name_key(name), mmsi, imo, (entry.get("flag") or "")[:60] or None, (entry.get("reason") or "")[:300], _now(), actor),
        ).fetchone())
    audit.record("watchlist.add", actor=actor, target=f"watchlist:{created['id']}", details={"mmsi": mmsi, "imo": imo}, settings=settings)
    return created


def remove_entry(entry_id: int, *, actor: str, settings: Optional[Settings] = None) -> None:
    with db(settings) as conn:
        if not conn.execute("DELETE FROM watchlist WHERE id = ?", (entry_id,)).rowcount:
            raise LookupError("Entrée introuvable")
    audit.record("watchlist.remove", actor=actor, target=f"watchlist:{entry_id}", settings=settings)


def list_entries(search: str = "", limit: int = 500, settings: Optional[Settings] = None) -> Dict[str, Any]:
    with read_db(settings) as conn:
        counts = {r["source"]: r["n"] for r in conn.execute("SELECT source, COUNT(*) AS n FROM watchlist GROUP BY source")}
        if search:
            like = f"%{search[:64]}%"
            result = rows(
                conn.execute(
                    "SELECT * FROM watchlist WHERE name LIKE ? OR mmsi LIKE ? OR imo LIKE ? OR flag LIKE ? ORDER BY name LIMIT ?",
                    (like, like, like, like, limit),
                ).fetchall()
            )
        else:
            result = rows(conn.execute("SELECT * FROM watchlist ORDER BY source, name LIMIT ?", (limit,)).fetchall())
    return {"entries": result, "counts": counts}


_MATCHER_CACHE: Dict[Any, Dict[str, Any]] = {}


class Matcher:
    """In-memory index for fast matching of many vessels.

    The index is rebuilt only when the watchlist changes (row count / highest id:
    every add, import or removal changes it), not on each instantiation — rules,
    map feeds and the graph create one per call.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        with read_db(settings) as conn:
            signature = (str((settings or get_settings()).data_dir), *conn.execute("SELECT COUNT(*), MAX(id) FROM watchlist").fetchone())
            cached = _MATCHER_CACHE.get(signature)
            if cached is not None:
                self.__dict__.update(cached)
                return
            entries = rows(conn.execute("SELECT * FROM watchlist").fetchall())
        self._build(entries)
        _MATCHER_CACHE.clear()  # only the current version is worth keeping
        _MATCHER_CACHE[signature] = dict(self.__dict__)

    def _build(self, entries: List[Dict[str, Any]]) -> None:
        self.by_mmsi = {e["mmsi"]: e for e in entries if e["mmsi"]}
        self.by_imo = {e["imo"]: e for e in entries if e["imo"]}
        self.by_call_sign = {e["call_sign"]: e for e in entries if e.get("call_sign")}
        self.by_name: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            for key in [e["name_key"], *filter(None, (e.get("alias_keys") or "").split(","))]:
                if len(key) >= 4:
                    self.by_name.setdefault(key, e)

    def match(self, *, mmsi: Any = None, imo: Any = None, name: Any = None, call_sign: Any = None) -> Optional[Dict[str, Any]]:
        """Strongest identifier first: MMSI, IMO, radio call sign, then name / RFMO alias."""
        for label, index, key in (
            ("MMSI", self.by_mmsi, _digits(mmsi, 9)),
            ("IMO", self.by_imo, _digits(imo, 7)),
            ("indicatif radio", self.by_call_sign, call_sign_key(call_sign)),
        ):
            if key and key in index:
                return {**index[key], "matched_on": label}
        hit = self.by_name.get(name_key(str(name or "")))
        return {**hit, "matched_on": "nom / alias ORGP"} if hit else None


def summary(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entry:
        return None
    try:
        details = json.loads(entry.get("details") or "{}")
    except ValueError:
        details = {}
    return {
        "watchlist_id": entry["id"],
        "source": entry["source"],
        "name": entry["name"],
        "flag": entry["flag"],
        "reason": entry["reason"],
        "matched_on": entry.get("matched_on"),
        "gear": details.get("gear"),
        "vessel_type": details.get("vessel_type"),
        "rfmos": sorted({l["rfmo"] for l in details.get("listings") or [] if not l.get("until")}),
    }
