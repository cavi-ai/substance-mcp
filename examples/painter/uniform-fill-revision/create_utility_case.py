"""Create the original utility-case OBJ used by the material example. MIT."""

import math
from pathlib import Path

parts = []


def box(name, center, size, bevel, material):
    h = [v / 2 for v in size]
    r = min(bevel, min(h) * 0.8)
    faces = []
    for a in range(3):
        b, c = [i for i in range(3) if i != a]
        for sign in [-1, 1]:
            f = []
            for sb, sc in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
                v = [0.0, 0.0, 0.0]
                v[a] = sign * h[a]
                v[b] = sb * (h[b] - r)
                v[c] = sc * (h[c] - r)
                f.append(v)
            faces.append(f)
    for a, b in [(0, 1), (0, 2), (1, 2)]:
        c = 3 - a - b
        for sa in [-1, 1]:
            for sb in [-1, 1]:
                f = []
                for axis, sc in [(a, -1), (b, -1), (b, 1), (a, 1)]:
                    v = [0.0, 0.0, 0.0]
                    v[a] = sa * (h[a] if axis == a else h[a] - r)
                    v[b] = sb * (h[b] if axis == b else h[b] - r)
                    v[c] = sc * (h[c] - r)
                    f.append(v)
                faces.append(f)
    for sx in [-1, 1]:
        for sy in [-1, 1]:
            for sz in [-1, 1]:
                signs = [sx, sy, sz]
                faces.append(
                    [
                        [signs[i] * (h[i] if i == axis else h[i] - r) for i in range(3)]
                        for axis in range(3)
                    ]
                )
    out = []
    for face in faces:
        a, b, c = face[:3]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
        if sum(n[i] * sum(q[i] for q in face) for i in range(3)) < 0:
            face = list(reversed(face))
        out.append([[q[i] + center[i] for i in range(3)] for q in face])
    parts.append((name, material, out))


box("CaseBody", (0, 0.19, 0), (0.72, 0.36, 0.3), 0.024, "PaintedMetal")
box("CaseLid", (0, 0.402, 0), (0.73, 0.065, 0.31), 0.015, "PaintedMetal")
box("HandleGrip", (0, 0.515, 0), (0.29, 0.045, 0.055), 0.012, "Hardware")
for x in [-0.145, 0.145]:
    box("HandleSupport", (x, 0.467, 0), (0.035, 0.085, 0.055), 0.008, "Hardware")
for x in [-0.235, 0.235]:
    box("Latch", (x, 0.355, 0.163), (0.062, 0.1, 0.022), 0.008, "Hardware")
    box("Foot", (x, -0.006, 0), (0.1, 0.03, 0.23), 0.008, "Hardware")
counts = {m: sum((len(f) for _, mat, f in parts if mat == m)) for m in ["PaintedMetal", "Hardware"]}
indices = {m: 0 for m in counts}
lines = ["# Original utility-case geometry; MIT.", "mtllib utility_case.mtl"]
idx = 1
for name, mat, faces in parts:
    lines += ["o " + name, "usemtl " + mat]
    grid = math.ceil(math.sqrt(counts[mat]))
    for face in faces:
        cell = indices[mat]
        indices[mat] += 1
        col = cell % grid
        row = cell // grid
        pad = 0.08
        uv = [
            ((col + pad) / grid, (row + pad) / grid),
            ((col + 1 - pad) / grid, (row + pad) / grid),
            ((col + 1 - pad) / grid, (row + 1 - pad) / grid),
            ((col + pad) / grid, (row + 1 - pad) / grid),
        ]
        if len(face) == 3:
            uv = uv[:3]
        for v in face:
            lines.append("v " + " ".join(f"{x:.7f}" for x in v))
        for u, v in uv:
            lines.append(f"vt {u:.7f} {v:.7f}")
        lines.append("f " + " ".join(f"{idx + j}/{idx + j}" for j in range(len(face))))
        idx += len(face)
out = Path(__file__).parent
(out / "utility_case.obj").write_text("\n".join(lines) + "\n")
(out / "utility_case.mtl").write_text(
    "newmtl PaintedMetal\nKd 0.12 0.42 0.35\nnewmtl Hardware\nKd 0.2 0.2 0.2\n"
)
print({"faces": sum(counts.values()), "vertices": idx - 1, "materials": counts})
