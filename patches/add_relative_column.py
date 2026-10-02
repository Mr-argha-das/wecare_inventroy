#!/usr/bin/env python3
"""Add a 'Relative Name' column to templates/admin/equipment_assign.html.

Run from the fcm_hms project root:

    python3 add_relative_column.py

Safe to re-run: it stops if the column is already there, and it keeps a
backup at templates/admin/equipment_assign.html.before-relative
"""
import re
import shutil
import sys

PATH = "templates/admin/equipment_assign.html"

src = open(PATH, encoding="utf-8").read()

if "relative_name" in src:
    sys.exit("Already added - nothing to do.")

shutil.copy(PATH, PATH + ".before-relative")

lines = src.split("\n")
out = []
done_th = done_td = done_search = False

for ln in lines:
    out.append(ln)
    stripped = ln.strip()

    # 1) header cell right after <th>Patient</th>
    if not done_th and re.fullmatch(r"<th[^>]*>\s*Patient\s*</th>", stripped, re.I):
        indent = ln[: len(ln) - len(ln.lstrip())]
        out.append(indent + "<th>Relative Name</th>")
        done_th = True

    # 2) body cell right after the patient-name <td>
    if not done_td and "a.patient_name" in ln and "<td" in ln:
        indent = ln[: len(ln) - len(ln.lstrip())]
        out.append(indent + '<td>${a.relative_name || "\u2014"}</td>')
        done_td = True

    # 3) let the search box match the relative name too
    if not done_search and "a.patient_name" in ln and "includes(q)" in ln:
        indent = ln[: len(ln) - len(ln.lstrip())]
        out.append(indent + '(a.relative_name || "").toLowerCase().includes(q) ||')
        done_search = True

res = "\n".join(out)

# 4) every colspan in this table grows by one
res = re.sub(r'colspan="(\d+)"', lambda m: 'colspan="%d"' % (int(m.group(1)) + 1), res)

open(PATH, "w", encoding="utf-8").write(res)

print("header cell :", "OK" if done_th else "NOT FOUND - add <th>Relative Name</th> by hand")
print("body cell   :", "OK" if done_td else "NOT FOUND - add the <td> by hand")
print("search      :", "OK" if done_search else "skipped (no search filter found)")
print("backup      :", PATH + ".before-relative")
