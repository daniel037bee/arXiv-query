"""ORCID work matching and tracker import, using only the Python standard library."""

import json
import os
from pathlib import Path
import posixpath
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


PRIORITY_WEIGHTS = {"Highest": 3, "High": 2, "Medium": 1, "Skip (sim/theory)": 0}
DEFAULT_CONFIG = Path(__file__).with_name("tracked_pis.json")

# Only a leading or corresponding author promotes a paper; a tracked PI buried in a
# long collaboration list does not. arXiv publishes no corresponding-author field, so
# "corresponding" means the arXivRaw submitter or a contact address printed in the
# record. Add "last" here to also promote senior (final) authors.
PROMOTED_ROLES = ("first", "second", "corresponding")
ROLE_ORDER = {"first": 0, "second": 1, "corresponding": 2, "last": 3, "co-author": 4, "unknown": 5}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize_orcid(value):
    value = re.sub(r"^https?://(?:www\.)?orcid\.org/", "", str(value or "").strip(), flags=re.I).rstrip("/").upper()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        return ""
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    return value if digits[-1] == ("X" if check == 10 else str(check)) else ""


def normalize_arxiv_id(value):
    value = urllib.parse.unquote(str(value or "").strip())
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value, flags=re.I)
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    value = re.sub(r"v\d+$", "", value)
    return value.lower() if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})", value) else ""


def normalize_doi(value):
    value = urllib.parse.unquote(str(value or "").strip()).lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def _fold(value):
    """Accent-free lowercase text, so diacritics cannot break a name or email comparison."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def _name_parts(value):
    tokens = [token for token in re.split(r"[^a-z]+", _fold(value)) if token]
    while len(tokens) > 1 and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    # A lone surname constrains nothing further: do not read it as a first initial.
    return (tokens[-1], tokens[0][:1] if len(tokens) > 1 else "") if tokens else ("", "")


def names_match(person_name, author_name):
    """Surname plus first initial. Only ever used to place a PI already identified by ORCID."""
    surname, initial = _name_parts(person_name)
    other_surname, other_initial = _name_parts(author_name)
    if not surname or surname != other_surname:
        return False
    return not initial or not other_initial or initial == other_initial


def author_names(paper):
    """Ordered author names, or [] when the stored string cannot be split back reliably."""
    names = [name.strip() for name in str(paper.get("authors") or "").split(",") if name.strip()]
    count = paper.get("author_count")
    return names if not count or len(names) == count else []


def contact_emails(paper):
    """Addresses arXiv actually carries: the submitter line, the comments field, the abstract."""
    text = " ".join(str(paper.get(field) or "") for field in ("submitter", "comments", "abstract"))
    return {match.group(0).casefold() for match in EMAIL_PATTERN.finditer(text)}


def _email_matches(person, emails):
    known = _fold(person.get("email")).strip()
    surname = _name_parts(person.get("name"))[0]
    for email in emails:
        if known and email == known:
            return True
        local = re.sub(r"[^a-z]", "", _fold(email.split("@", 1)[0]))
        if len(surname) >= 3 and surname in local:
            return True
    return False


def is_corresponding(paper, person):
    """arXiv exposes no corresponding author, so accept the submitter or a printed contact address."""
    submitter = paper.get("submitter")
    if submitter and names_match(person.get("name"), re.sub(r"<[^>]*>", " ", str(submitter))):
        return True
    return _email_matches(person, contact_emails(paper))


def author_role(paper, person, positions=None):
    """Place a matched PI: first, second, corresponding, last, co-author, or unknown."""
    position = min(positions) if positions else None
    if position is None:
        # ORCID public works can link a PI to a paper whose author ORCIDs arXiv never received.
        position = next((i for i, name in enumerate(author_names(paper))
                         if names_match(person.get("name"), name)), None)
    if position == 0:
        return "first", position
    if position == 1:
        return "second", position
    if is_corresponding(paper, person):
        return "corresponding", position
    count = paper.get("author_count") or len(author_names(paper))
    if position is not None and count and position == count - 1:
        return "last", position
    return ("co-author" if position is not None else "unknown"), position


def _xlsx_rows(path):
    """Read values and hyperlink targets without executing spreadsheet formulas."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(e.itertext()) for e in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = {r.get("Id"): r.get("Target") for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        for sheet in book.findall("s:sheets/s:sheet", ns):
            target = relationships[sheet.get(rel_id)]
            sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            root = ET.fromstring(archive.read(sheet_path))
            links = {}
            rel_path = posixpath.join(posixpath.dirname(sheet_path), "_rels", posixpath.basename(sheet_path) + ".rels")
            if rel_path in archive.namelist():
                targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(archive.read(rel_path))}
                links = {e.get("ref"): targets.get(e.get(rel_id), "") for e in root.findall("s:hyperlinks/s:hyperlink", ns)}
            rows = []
            for row in root.findall("s:sheetData/s:row", ns):
                values = {}
                for cell in row.findall("s:c", ns):
                    col = re.sub(r"\d", "", cell.get("r", ""))
                    kind = cell.get("t")
                    value = cell.findtext("s:v", "", ns)
                    if kind == "s":
                        value = strings[int(value)] if value else ""
                    elif kind == "inlineStr":
                        value = "".join(cell.find("s:is", ns).itertext())
                    if normalize_orcid(links.get(cell.get("r"))):
                        value = links[cell.get("r")]
                    values[col] = value
                rows.append((int(row.get("r")), values))
            yield sheet.get("name"), rows


def load_watchlist(config_path=None, tracker_path=None):
    with open(config_path or DEFAULT_CONFIG, encoding="utf-8") as source:
        config = json.load(source)
    people = config["people"]
    if tracker_path:
        known = {p["name"].casefold(): p for p in people}
        people = []
        for sheet, rows in _xlsx_rows(tracker_path):
            headers = None
            for row_number, values in rows:
                if "PI (contact)" in values.values() and "Priority" in values.values():
                    headers = {value: col for col, value in values.items()}
                    continue
                if not headers or not values.get(headers["PI (contact)"]):
                    continue
                raw = values[headers["PI (contact)"]]
                raw = re.sub(r"\s*\(CITA\)\s*\+ dept faculty", "", raw)
                names = raw.split(" / ")
                orcid_col = next((col for label, col in headers.items() if label.casefold() in ("orcid", "orcid id", "orcid iD".casefold())), None)
                email_col = next((col for label, col in headers.items() if label.casefold() in ("email", "e-mail", "contact email")), None)
                for name in names:
                    person = dict(known.get(name.strip().casefold(), {}))
                    person.update(name=name.strip(), institution=values.get(headers.get("Institution"), ""),
                                  priority=values.get(headers["Priority"], ""), source_row=row_number, source_sheet=sheet)
                    if orcid_col and values.get(orcid_col):
                        if len(names) != 1:
                            raise ValueError(f"Row {row_number}: put each PI and ORCID on a separate row.")
                        person["orcid"] = values[orcid_col]
                    if email_col and values.get(email_col) and len(names) == 1:
                        person["email"] = values[email_col]
                    people.append(person)
        if not people:
            raise ValueError("No PI (contact) and Priority columns found in the tracker.")
    unique = {}
    for original in people:
        person = dict(original)
        priority = person.get("priority")
        if priority not in PRIORITY_WEIGHTS:
            raise ValueError(f"Unknown priority {priority!r} for {person.get('name')}. Use {', '.join(PRIORITY_WEIGHTS)}.")
        raw_orcid = person.get("orcid")
        orcid = normalize_orcid(raw_orcid)
        if raw_orcid and not orcid:
            raise ValueError(f"Invalid ORCID for {person['name']}: {raw_orcid}")
        person.update(orcid=orcid or None, weight=PRIORITY_WEIGHTS[priority])
        # An email is optional evidence for corresponding authorship; drop anything unusable.
        email = _fold(person.get("email")).strip()
        if EMAIL_PATTERN.fullmatch(email):
            person["email"] = email
        else:
            person.pop("email", None)
        key = orcid or person["name"].casefold()
        if key not in unique or person["weight"] > unique[key]["weight"]:
            unique[key] = person
    return list(unique.values())


def work_identifiers(payload):
    """Only identifiers for the work itself or its version, never its container."""
    keys = set()
    for group in payload.get("group", []):
        # The group combines equivalent versions/sources of the same work.
        for entry in [group] + group.get("work-summary", []):
            external = entry.get("external-ids") or {}
            for identifier in external.get("external-id", []):
                if identifier.get("external-id-relationship") not in ("self", "version-of"):
                    continue
                kind = identifier.get("external-id-type", "").lower()
                value = identifier.get("external-id-value")
                if kind == "arxiv":
                    normalized = normalize_arxiv_id(value)
                    if normalized:
                        keys.add("arxiv:" + normalized)
                elif kind == "doi":
                    normalized = normalize_doi(value)
                    if normalized:
                        keys.add("doi:" + normalized)
                        if normalized.startswith("10.48550/arxiv."):
                            arxiv = normalize_arxiv_id(normalized[len("10.48550/arxiv."):])
                            if arxiv:
                                keys.add("arxiv:" + arxiv)
            url = entry.get("url") or {}
            arxiv = normalize_arxiv_id(url.get("value"))
            if arxiv:
                keys.add("arxiv:" + arxiv)
    return sorted(keys)


def build_orcid_index(people, cache_path, refresh=False, offline=False):
    """Cache public works for 24h; preserve old evidence if a refresh fails."""
    cache_path = Path(cache_path)
    saved = {}
    if cache_path.exists():
        try:
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("Could not read ORCID works cache; rebuilding it.")
    index, statuses = {}, {}
    token = os.getenv("ORCID_ACCESS_TOKEN")
    network_unavailable = False
    for person in people:
        orcid = person.get("orcid")
        if not orcid or not person["weight"]:
            continue
        record = saved.get(orcid, {})
        fresh = bool(record) and time.time() - record.get("fetched_at", 0) < 86400
        status = "cached" if fresh else ("stale" if record else "unavailable")
        if not offline and not network_unavailable and (refresh or not fresh):
            headers = {"Accept": "application/json", "User-Agent": "arXiv-Dashboard/1.0 (ORCID public works)"}
            if token:
                headers["Authorization"] = "Bearer " + token
            try:
                req = urllib.request.Request(f"https://pub.orcid.org/v3.0/{orcid}/works", headers=headers)
                with urllib.request.urlopen(req, timeout=20) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or "group" not in payload:
                    raise ValueError("Unexpected ORCID works response")
                record = {"fetched_at": time.time(), "keys": work_identifiers(payload)}
                saved[orcid] = record
                status = "current"
                print(f"ORCID: {person['name']} ({len(record['keys'])} work identifiers)")
                time.sleep(0.15)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                print(f"ORCID unavailable for {person['name']}: {error}; using cached evidence if available.")
                # Avoid repeating a failed service request for the whole watchlist.
                if not isinstance(error, urllib.error.HTTPError) or error.code in (401, 403, 429, 500, 502, 503, 504):
                    network_unavailable = True
        statuses[orcid] = {"status": status, "fetched_at": record.get("fetched_at"),
                           "identifier_count": len(record.get("keys", [])) if record else None}
        for key in record.get("keys", []):
            index.setdefault(key, []).append(orcid)
    if not offline:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        temporary.replace(cache_path)
    return index, statuses


def match_paper(paper, people, index, promoted_roles=PROMOTED_ROLES):
    """Link tracked PIs to a paper, then mark which of them their author position promotes."""
    positions = {}
    for orcid, places in (paper.get("orcid_positions") or {}).items():
        normalized = normalize_orcid(orcid)
        if normalized:
            positions[normalized] = sorted(int(place) for place in places)
    ids = set(positions)
    ids.update(filter(None, (normalize_orcid(value) for value in paper.get("author_orcids", []))))
    arxiv = normalize_arxiv_id(paper.get("id"))
    if arxiv:
        ids.update(index.get("arxiv:" + arxiv, []))
    doi = normalize_doi(paper.get("doi"))
    if doi:
        ids.update(index.get("doi:" + doi, []))
    matches = []
    for person in people:
        if not person["weight"] or not person.get("orcid") or person["orcid"] not in ids:
            continue
        role, position = author_role(paper, person, positions.get(person["orcid"]))
        matches.append({"name": person["name"], "orcid": person["orcid"], "priority": person["priority"],
                        "weight": person["weight"], "role": role, "position": position,
                        "promoted": role in promoted_roles})
    matches.sort(key=lambda p: (not p["promoted"], -p["weight"], ROLE_ORDER.get(p["role"], 9), p["name"]))
    return matches


def pi_score(matches):
    """Only promoted matches move a paper up the ranking."""
    return sum(match["weight"] for match in matches if match["promoted"])


def needs_submitter(paper):
    """A submitter lookup can only change the ranking while some match is still unpromoted."""
    return "submitter" not in paper and any(not match["promoted"] for match in paper.get("orcid_matches", []))
