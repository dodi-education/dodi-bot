#!/usr/bin/env python3
"""
DODI Case Generator - Spherical AI Assistant Case
Generates mesh-grid sphere with diamond wireframe pattern,
display cutout with snap-fit, and Pi 4 mounting standoffs.
"""
import math, os, time

# ===================== PARAMETERS =====================
SPHERE_RADIUS = 87.5
WALL_THICKNESS = 3.0
INNER_RADIUS = SPHERE_RADIUS - WALL_THICKNESS

BOTTOM_CUT_HEIGHT = 25.0
BOTTOM_CUT_Y = -SPHERE_RADIUS + BOTTOM_CUT_HEIGHT

DISPLAY_VISIBLE_DIAM = 87.6    # active screen area
DISPLAY_OUTER_DIAM = 115.0     # full module diameter incl. black bezel
DISPLAY_HOLE_DIAM = 113.0      # hole 1mm overlap per side
DISPLAY_HOLE_RADIUS = DISPLAY_HOLE_DIAM / 2.0
DISPLAY_ANG_RADIUS = math.asin(min(DISPLAY_HOLE_RADIUS / SPHERE_RADIUS, 0.999))

# Display gaze tilt: raise the face so dodi looks slightly UP at a desk
# viewer instead of straight ahead. The display cutout is a circular patch
# centered on the sphere at (theta, phi); its outward normal at that center
# IS the gaze direction. theta is measured from the top pole (+Y), so
# decreasing theta below 90deg slides the patch toward the top and tilts the
# gaze up by exactly that many degrees. Bottom stays flat (cut is along Y),
# so the case still stands level while the face looks upward.
DISPLAY_TILT_UP_DEG = 15.0
DISPLAY_CENTER_THETA = math.radians(90.0 - DISPLAY_TILT_UP_DEG)
DISPLAY_CENTER_PHI = 0.0

# Diamond wireframe: 3 families of latitude lines at 60deg rotations
GRID_SPACING = 12.0     # degrees between grid lines per family
GRID_LINE_WIDTH = 2.8   # degrees width of each line
GRID_ANGLES = [0, 60, -60]  # rotation angles around X-axis (degrees)

BEZEL_RING_WIDTH = 8.0
BOTTOM_RIM_WIDTH = 8.0
TOP_CAP_ANGLE = 10.0

N_THETA = 760
N_PHI = 1520

BOTTOM_CUT_THETA = math.acos(max(-1, min(1, BOTTOM_CUT_Y / SPHERE_RADIUS)))

# Pi 4 mounting standoffs — on back wall, opposite display (+Z front, -Z back)
# Pi board perpendicular to Z, screws go straight in along Z axis
# Board: 85x56mm. Holes from corner: (3.5,3.5),(61.5,3.5),(3.5,52.5),(61.5,52.5)
# Board CENTERED at origin (not hole pattern), so right edge doesn't hit wall
# Board center = (42.5, 28) from corner → holes offset by (-39, -24.5) to (19, 24.5)
PI_HOLES_XY = [(-39.0, -24.5), (19.0, -24.5), (-39.0, 24.5), (19.0, 24.5)]
PI_BOARD_Z = -55.0   # Z position of Pi board surface (back of sphere)
# Board edges: x=±42.5, y=±28. Worst corner clearance at z=-55:
# sqrt(42.5²+28²+55²)=74.9mm vs inner_r=84.5mm → 9.6mm clearance ✓
POST_OD = 6.0
TAP_DRILL_D = 2.05   # M2.5 tap drill
POST_SEGMENTS = 16
BOSS_FILL_ANGLE = math.radians(4.0)  # solid fill radius around each boss on sphere wall

# Speaker mounting — at inner top of sphere, Y-axis aligned (screws go up)
# Speaker: 44mm diagonal incl. mounting ears, 31mm cone, 2 diagonal screw holes
# Mounting holes ~38mm apart (diagonal corners), using M2.5 screws
SPEAKER_HOLES_XZ = [(-19.0, 0.0), (19.0, 0.0)]  # 2 holes, 38mm apart along X
SPEAKER_MOUNT_Y = 65.0  # Y position of speaker mounting face (bottom of boss)
# At x=±19, z=0: inner wall Y = sqrt(84.5²-19²) ≈ 82.3mm → boss length ~17mm
# At Y=65: XZ radius = sqrt(84.5²-65²) ≈ 54mm, speaker fits (22mm from center) ✓

# Pre-compute spherical coords of Pi boss attachment points on sphere wall
# so is_cell_solid can fill the shell behind each boss
PI_BOSS_SPHERICAL = []
for _hx, _hy in PI_HOLES_XY:
    _r2 = INNER_RADIUS**2 - _hx**2 - _hy**2
    if _r2 > 0:
        _zw = -math.sqrt(_r2)  # back wall is -Z
        _bt = math.acos(max(-1, min(1, _hy / INNER_RADIUS)))
        _bp = math.atan2(_hx, _zw)
        PI_BOSS_SPHERICAL.append((_bt, _bp))

# ===================== HELPERS =====================
def sph2cart(r, theta, phi):
    st = math.sin(theta)
    return (r * st * math.sin(phi), r * math.cos(theta), r * st * math.cos(phi))

def ang_dist(t1, p1, t2, p2):
    c = math.cos(t1)*math.cos(t2) + math.sin(t1)*math.sin(t2)*math.cos(p1-p2)
    return math.acos(max(-1, min(1, c)))

def is_cell_solid(theta_deg, phi_deg, theta_rad, phi_rad):
    if theta_rad > BOTTOM_CUT_THETA:
        return False
    d = ang_dist(theta_rad, phi_rad, DISPLAY_CENTER_THETA, DISPLAY_CENTER_PHI)
    if d < DISPLAY_ANG_RADIUS:
        return False
    if d < DISPLAY_ANG_RADIUS + math.radians(BEZEL_RING_WIDTH):
        return True
    if theta_rad > BOTTOM_CUT_THETA - math.radians(BOTTOM_RIM_WIDTH):
        return True
    if theta_deg < TOP_CAP_ANGLE:
        return True

    # Solid fill behind Pi mounting bosses
    for (bt, bp) in PI_BOSS_SPHERICAL:
        if ang_dist(theta_rad, phi_rad, bt, bp) < BOSS_FILL_ANGLE:
            return True

    # Diamond wireframe: 3 rotated families of latitude lines
    x, y, z = sph2cart(1.0, theta_rad, phi_rad)
    for ang_deg in GRID_ANGLES:
        a = math.radians(ang_deg)
        ca, sa = math.cos(a), math.sin(a)
        ry = y * ca - z * sa  # rotated Y component
        lat = math.degrees(math.acos(max(-1, min(1, ry))))
        if (lat % GRID_SPACING) < GRID_LINE_WIDTH:
            return True
    return False

# ===================== MESH BUILDER =====================
class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.faces = []
        self._vmap = {}

    def vtx(self, x, y, z):
        k = (round(x, 3), round(y, 3), round(z, 3))
        if k not in self._vmap:
            self.verts.append(k)
            self._vmap[k] = len(self.verts)
        return self._vmap[k]

    def quad(self, a, b, c, d):
        u = [a, b, c, d]
        # collapse degenerate
        u2 = []
        for i, v in enumerate(u):
            if v != u[(i+1) % 4]:
                u2.append(v)
        if len(u2) >= 3:
            self.faces.append(tuple(u2))

    def tri(self, a, b, c):
        if a != b and b != c and a != c:
            self.faces.append((a, b, c))

    def cylinder(self, cx, cy_bot, cz, cy_top, od, id_inner, segs=16):
        """Vertical cylinder with optional center hole. Returns nothing."""
        ro = od / 2.0
        ri = id_inner / 2.0
        ot, ob, it, ib = [], [], [], []
        for i in range(segs):
            a = 2 * math.pi * i / segs
            ca, sa = math.cos(a), math.sin(a)
            ot.append(self.vtx(cx + ro*ca, cy_top, cz + ro*sa))
            ob.append(self.vtx(cx + ro*ca, cy_bot, cz + ro*sa))
            if ri > 0:
                it.append(self.vtx(cx + ri*ca, cy_top, cz + ri*sa))
                ib.append(self.vtx(cx + ri*ca, cy_bot, cz + ri*sa))
        for i in range(segs):
            j = (i + 1) % segs
            # outer wall
            self.quad(ob[i], ob[j], ot[j], ot[i])
            if ri > 0:
                # inner wall (reversed)
                self.quad(it[i], it[j], ib[j], ib[i])
                # top annular face
                self.quad(ot[i], ot[j], it[j], it[i])
                # bottom annular face
                self.quad(ib[i], ib[j], ob[j], ob[i])
            else:
                # solid top
                center_t = self.vtx(cx, cy_top, cz)
                self.tri(center_t, ot[i], ot[j])
                center_b = self.vtx(cx, cy_bot, cz)
                self.tri(center_b, ob[j], ob[i])

    def z_cylinder(self, cx, cy, z_back, z_front, od, id_inner, segs=16):
        """Cylinder along Z-axis with optional center hole (tap drill).
        z_back = sphere wall side, z_front = Pi board side."""
        ro = od / 2.0
        ri = id_inner / 2.0
        ft, bk = [], []  # front (Pi side) and back (wall side) outer rings
        fi, bi = [], []  # front and back inner rings
        for i in range(segs):
            a = 2 * math.pi * i / segs
            ca, sa = math.cos(a), math.sin(a)
            ft.append(self.vtx(cx + ro*ca, cy + ro*sa, z_front))
            bk.append(self.vtx(cx + ro*ca, cy + ro*sa, z_back))
            if ri > 0:
                fi.append(self.vtx(cx + ri*ca, cy + ri*sa, z_front))
                bi.append(self.vtx(cx + ri*ca, cy + ri*sa, z_back))
        for i in range(segs):
            j = (i + 1) % segs
            # outer wall
            self.quad(bk[i], bk[j], ft[j], ft[i])
            if ri > 0:
                # inner wall (hole, reversed normal)
                self.quad(fi[i], fi[j], bi[j], bi[i])
                # front annular face (Pi board side - screw goes in here)
                self.quad(ft[i], ft[j], fi[j], fi[i])
                # back annular face (sphere wall side)
                self.quad(bi[i], bi[j], bk[j], bk[i])
            else:
                cf = self.vtx(cx, cy, z_front)
                self.tri(cf, ft[i], ft[j])
                cb = self.vtx(cx, cy, z_back)
                self.tri(cb, bk[j], bk[i])

# ===================== MAIN GENERATION =====================
def generate_case():
    m = MeshBuilder()
    t0 = time.time()
    print(f"Generating DODI case (R={SPHERE_RADIUS}, diamond wireframe)...")

    dtheta = math.pi / N_THETA
    dphi = 2.0 * math.pi / N_PHI

    # Vertex grid
    ov = []
    iv = []
    for i in range(N_THETA + 1):
        theta = i * dtheta
        ro, ri2 = [], []
        for j in range(N_PHI + 1):
            phi = (j % N_PHI) * dphi
            ox, oy, oz = sph2cart(SPHERE_RADIUS, theta, phi)
            ix, iy, iz = sph2cart(INNER_RADIUS, theta, phi)
            ro.append(m.vtx(ox, oy, oz))
            ri2.append(m.vtx(ix, iy, iz))
        ov.append(ro)
        iv.append(ri2)
    print(f"  Vertices: {len(m.verts)} ({time.time()-t0:.1f}s)")

    # Solid map
    sol = []
    sc = 0
    for i in range(N_THETA):
        row = []
        tc = (i + 0.5) * dtheta
        td = math.degrees(tc)
        for j in range(N_PHI):
            pc = (j + 0.5) * dphi
            pd = math.degrees(pc) % 360
            s = is_cell_solid(td, pd, tc, pc)
            row.append(s)
            sc += s
        sol.append(row)
    print(f"  Solid: {sc}/{N_THETA*N_PHI} ({100*sc/(N_THETA*N_PHI):.1f}%)")

    # Shell faces
    for i in range(N_THETA):
        for j in range(N_PHI):
            if not sol[i][j]:
                continue
            jn = j + 1
            m.quad(ov[i][j], ov[i][jn], ov[i+1][jn], ov[i+1][j])
            m.quad(iv[i+1][j], iv[i+1][jn], iv[i][jn], iv[i][j])
            if i == 0 or not sol[i-1][j]:
                m.quad(ov[i][j], ov[i][jn], iv[i][jn], iv[i][j])
            if i == N_THETA-1 or not sol[i+1][j]:
                m.quad(ov[i+1][jn], ov[i+1][j], iv[i+1][j], iv[i+1][jn])
            jp = (j-1) % N_PHI
            if not sol[i][jp]:
                m.quad(ov[i][j], iv[i][j], iv[i+1][j], ov[i+1][j])
            jnc = (j+1) % N_PHI
            if not sol[i][jnc]:
                m.quad(ov[i+1][jn], iv[i+1][jn], iv[i][jn], ov[i][jn])
    print(f"  Shell faces: {len(m.faces)} ({time.time()-t0:.1f}s)")

    # --- Pi 4 mounting standoffs with M2.5 tap holes ---
    # On back wall (opposite display), Z-aligned so screws go straight in
    for (hx, hy) in PI_HOLES_XY:
        # Z of inner sphere wall at this (x,y) position
        r2 = INNER_RADIUS**2 - hx**2 - hy**2
        if r2 <= 0:
            continue
        z_wall = -math.sqrt(r2)  # back wall is -Z
        m.z_cylinder(hx, hy, z_wall, PI_BOARD_Z, POST_OD, TAP_DRILL_D, POST_SEGMENTS)
    print(f"  + Pi mounts: {len(m.faces)} faces ({time.time()-t0:.1f}s)")

    # --- Speaker mounting bosses with M2.5 tap holes ---
    # At inner top of sphere, Y-axis aligned, screws go upward into bosses
    for (sx, sz) in SPEAKER_HOLES_XZ:
        # Y of inner sphere wall at this (x,z) position
        r2 = INNER_RADIUS**2 - sx**2 - sz**2
        if r2 <= 0:
            continue
        y_wall = math.sqrt(r2)  # top wall is +Y
        m.cylinder(sx, SPEAKER_MOUNT_Y, sz, y_wall, POST_OD, TAP_DRILL_D, POST_SEGMENTS)
    print(f"  + Speaker mounts: {len(m.faces)} faces ({time.time()-t0:.1f}s)")

    return m

# ===================== OUTPUT =====================
def write_obj(mesh, path):
    print(f"Writing {path}...")
    # Compact the vertex list: the spherical vertex grid is built in full, but
    # faces are only emitted for solid cells, so the diamond openings and the
    # region below the base cut leave ~half the vertices unreferenced. Keep
    # only vertices that a face actually uses and renumber faces to match.
    used = set()
    for face in mesh.faces:
        used.update(face)
    remap = {}
    verts = []
    for old in sorted(used):
        remap[old] = len(verts) + 1
        verts.append(mesh.verts[old - 1])
    faces = [tuple(remap[i] for i in face) for face in mesh.faces]
    print(f"  Compacted: {len(verts)}/{len(mesh.verts)} verts referenced "
          f"({100*len(verts)/len(mesh.verts):.0f}%)")
    with open(path, 'w') as f:
        f.write("# DODI AI Assistant Spherical Case\n")
        f.write(f"# Vertices: {len(verts)}, Faces: {len(faces)}\n")
        f.write(f"# R={SPHERE_RADIUS}mm, wall={WALL_THICKNESS}mm\n")
        f.write(f"# Diamond wireframe: {len(GRID_ANGLES)} families, "
                f"{GRID_SPACING}deg spacing, {GRID_LINE_WIDTH}deg width\n")
        f.write(f"# Display hole: {DISPLAY_HOLE_DIAM:.1f}mm\n")
        f.write(f"# Display tilt: {DISPLAY_TILT_UP_DEG:.0f}deg upward gaze "
                f"(center at theta={math.degrees(DISPLAY_CENTER_THETA):.0f}deg)\n")
        bo = 2*math.sqrt(max(0, SPHERE_RADIUS**2 - BOTTOM_CUT_Y**2))
        f.write(f"# Bottom opening: ~{bo:.0f}mm, Height: {SPHERE_RADIUS-BOTTOM_CUT_Y:.0f}mm\n")
        f.write(f"# Pi 4 mounts: 4x M2.5 standoffs at z={PI_BOARD_Z}mm (back wall)\n")
        f.write(f"# Speaker mounts: 2x M2.5 standoffs at y={SPEAKER_MOUNT_Y}mm (top)\n\n")
        f.write("o DODI_Case\n\n")

        for v in verts:
            f.write(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
        f.write("\n")
        for face in faces:
            f.write("f " + " ".join(str(i) for i in face) + "\n")
    sz = os.path.getsize(path)
    print(f"  {len(verts)} verts, {len(faces)} faces, {sz/1024/1024:.1f}MB")

if __name__ == '__main__':
    t = time.time()
    mesh = generate_case()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "case.obj")
    write_obj(mesh, out)
    print(f"\nDone in {time.time()-t:.1f}s")
