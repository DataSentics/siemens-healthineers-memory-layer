"""Generate the mocked org chart (PRD §8): R&D, Customer Support and Service with teams A/B/C, Marketing as one team.
Every team has a manager and at least 3 members. All people are fictional.

    python scripts/make_org.py seed/org.csv
"""

import csv
import sys

DOMAIN = "shs-demo.example"
FIRST = ["anna", "ben", "clara", "david", "elena", "felix", "greta", "hugo", "ines", "jonas", "karin", "leo", "marta",
         "nils", "olga", "pavel", "quinn", "rosa", "simon", "tereza", "uwe", "vera", "wen", "xaver", "yara", "zdenek",
         "alma", "bruno", "cora", "dan", "emil", "fiona", "gustav", "hana", "ivo", "julia", "kai", "lena", "milan", "nora"]
LAST = ["novak", "weber", "svoboda", "meyer", "dvorak", "fischer", "cerny", "wagner", "prochazka", "schulz", "kucera",
        "becker", "vesely", "hoffmann", "horak", "koch", "nemec", "richter", "pokorny", "klein", "marek", "wolf",
        "pospisil", "neumann", "hajek", "schwarz", "jelinek", "zimmer", "ruzicka", "braun", "benes", "krueger", "fiala",
        "hartmann", "sedlacek", "lange", "dolezal", "werner", "zeman", "lehmann"]

# Fixed demo personas: (email local part, name, department, team, is_manager, joined)
PINNED = [
    ("jana.kral", "Jana Kral", "R&D", "R&D / A", True, "2004-03-01"),
    ("eva.horak", "Eva Horak", "R&D", "R&D / A", False, "2004-09-01"),  # pilot user
    ("tomas.vrba", "Tomas Vrba", "R&D", "R&D / A", False, "2005-01-10"),
    ("petra.lind", "Petra Lind", "R&D", "R&D / A", False, "2005-02-14"),
    ("lukas.brandt", "Lukas Brandt", "R&D", "R&D / A", False, "2005-12-15"),  # new joiner
    ("mei.chen", "Mei Chen", "Customer Support", "Customer Support / B", False, "2005-05-01"),
]
TEAMS = [(d, f"{d} / {t}") for d in ("R&D", "Customer Support", "Service") for t in "ABC"] + [("Marketing", "Marketing")]


def main(out: str) -> None:
    rows, names = [], iter(zip(FIRST, LAST))
    for dept, team in TEAMS:
        pinned = [p for p in PINNED if p[3] == team]
        members = [{"local": p[0], "name": p[1], "is_manager": p[4], "joined": p[5]} for p in pinned]
        if not any(m["is_manager"] for m in members):
            f, l = next(names)
            members.insert(0, {"local": f"{f}.{l}", "name": f"{f.title()} {l.title()}", "is_manager": True, "joined": "2003-06-01"})
        while len(members) < 4:
            f, l = next(names)
            joined = "2006-01-03" if len(rows) % 9 == 0 else "2005-03-01"  # a few new joiners
            members.append({"local": f"{f}.{l}", "name": f"{f.title()} {l.title()}", "is_manager": False, "joined": joined})
        manager = next(m for m in members if m["is_manager"])
        for m in members:
            rows.append({"email": f"{m['local']}@{DOMAIN}", "name": m["name"], "department": dept, "team": team,
                         "manager_email": f"{manager['local']}@{DOMAIN}", "is_manager": str(m["is_manager"]).lower(),
                         "joined": m["joined"]})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} people in {len(TEAMS)} teams -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
