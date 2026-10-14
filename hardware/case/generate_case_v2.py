#!/usr/bin/env python3
"""
DODI Case Generator v2 - Spherical case with a glue-in display notch.

Changes from v1 (generate_case.py, kept as the record of prototype 1):

  * Display notch: the round display is pressed in from the FRONT into a
    stepped pocket — a cylindrical wall (with a radial gap for the textile
    cover + hot glue) and a ledge at the back that carries an adhesive strip
    under the display's back plate. The textile wraps over a rounded lip and
    down the wall; the display then press-fits against the fabric.
  * No Pi standoffs: the Pi is stacked on the display's own M2.5 studs, so
    display + driver board + Pi + HAT form one unit that drops in through the
    ledge opening.
  * 30deg display tilt: the flat base is cut 15deg further back ("base roll"),
    so the standing case shows the display 30deg above horizontal (v1: 15deg)
    -- easier to read and tap on a desk or nightstand.
  * Speaker on the upper back: v1's two straight M2.5 posts, moved onto the
    filled upper-back disc (perpendicular to it). The top is open lattice, a
    chimney for warm air.
  * Exact CSG geometry (manifold3d) instead of v1's stair-stepped cell grid:
    smooth lattice edges, hairline slits closed, and a single watertight,
    2-manifold mesh ready for SLS/MJF print services.

Display dimensions come from Waveshare's 3D model of the 3.4inch DSI LCD (C)
(3_4inch-DSI-LCD_C.stp, wiki "3.4inch_DSI_LCD_(C)"): Ø115.0 rim, 4.5 mm tall
plus a 0.5 mm front chamfer; Ø113.0 back plate, 1.0 mm; glass face to back
plate = 6.0 mm. Driver PCB outline reaches r = 50.1 mm, the Pi (with
USB/Ethernet overhang) ~52 mm from the display axis.

Coordinates: the shell is modelled as in v1 (+Y up, display faces +Z tilted up
by DISPLAY_TILT_UP_DEG) with the base plane rolled back by BASE_ROLL_DEG; the
exported mesh is rotated to stand on its base (flat base at y = BOTTOM_CUT_Y,
+Y up, display facing +Z), in mm.

Requires: pip install manifold3d numpy scipy
Usage:
  python generate_case_v2.py                    # case_v2.obj + case_v2.stl
  python generate_case_v2.py --fit-test 0.6 1.0 1.4
      # small notch rings (one per fabric gap) to test-fit display + textile
      # before ordering the full sphere
"""
import argparse
import math
import os
import time

import numpy as np
from manifold3d import CrossSection, Manifold, OpType

# ===================== PARAMETERS =====================
# --- Sphere (unchanged from v1) ---
SPHERE_RADIUS = 87.5
WALL_THICKNESS = 3.0
INNER_RADIUS = SPHERE_RADIUS - WALL_THICKNESS
BOTTOM_CUT_HEIGHT = 25.0
BOTTOM_CUT_Y = -SPHERE_RADIUS + BOTTOM_CUT_HEIGHT
DISPLAY_TILT_UP_DEG = 15.0   # display axis above the shell's equator
BASE_ROLL_DEG = 15.0         # base plane rolled back -> display view angle on a
                             # desk = DISPLAY_TILT_UP_DEG + BASE_ROLL_DEG = 30deg

# Diamond wireframe: 3 families of latitude bands at 0/±60deg rotations
GRID_SPACING = 12.0     # degrees between bands per family
GRID_LINE_WIDTH = 2.8   # degrees width of each band
GRID_ANGLES = [0, 60, -60]
# Per-family band widening (degrees added to BOTH edges of every band). The
# family axes are 60deg apart -- a multiple of GRID_SPACING -- so band edges of
# different families meet exactly in the x=0 symmetry plane, where their cone
# surfaces are tangent along a whole ruling; that degenerate contact leaves
# non-manifold edges in the exported mesh. Widening turns every such meeting
# into a small overlap (never a gap), and the per-family difference keeps the
# edges from coinciding. (An earlier fix *shifted* the families instead, which
# opened 0.2-0.5 mm hairline slits wherever two bands used to meet.)
GRID_OVERLAP = [0.05, 0.10, 0.15]
# Openings narrower than this (everywhere along them) are filled: three
# families of circles always cross at grazing angles somewhere, leaving hairline
# slivers that read as air gaps and print poorly. 0 disables.
SLIT_FILL_MM = 1.5
SLIT_MAP_RES = 1400     # cube-map resolution per face for the slit search
BEZEL_RING_WIDTH = 8.0  # degrees of solid shell around the display notch
BOTTOM_RIM_WIDTH = 8.0  # degrees of solid shell above the base cut
# Lattice poles filled solid out to the outer edge of their first ring, as
# (family angle, +1 = the family axis / -1 = its antipode). A bare pole is a
# small disc inside a ring with thin crescent gaps around it; filling it reads
# cleaner and is stiffer. (60, +1) is the disc on the upper back that carries
# the speaker posts; the top pole (0, +1) stays open lattice as a chimney.
FILLED_POLES = [(60, +1)]

# --- Display module (Waveshare 3.4inch DSI LCD (C), from the STEP model) ---
DISP_RIM_D = 115.0          # glass/frame rim diameter
DISP_FRONT_TO_BACK = 6.0    # glass face -> back plate face (rests on the glue)
DISP_VISIBLE_D = 88.2       # active area (for reference / checks only)

# --- Notch (all tunable; see fit-test rings) ---
FABRIC_GAP = 1.0      # radial room per side for textile + hot glue (press fit)
GLUE_T = 0.5          # adhesive strip thickness on the ledge
DISPLAY_RECESS = 0.0  # glass face below the (bare plastic) lip; 0 = flush
NOTCH_WALL_T = 2.0    # radial thickness of the cylindrical notch wall
LEDGE_T = 2.0         # axial thickness of the ledge
LEDGE_INNER_R = 53.5  # ledge opening radius; the display stack (r <= ~52)
                      # must pass through it, so don't go below ~53
LIP_FILLET = 1.0      # rounds the lip the fabric wraps over

# --- Speaker mounting ---
# Boxed 31 x 28 x 15 mm speaker, two diagonal M2.5 ears (44 mm ear-to-ear
# diagonal, holes 38 mm apart), ears flush with the box's back face.
SPEAKER_HOLES_XZ = [(-19.0, 0.0), (19.0, 0.0)]  # 2 holes, 38 mm apart along X
POST_OD = 6.0
TAP_DRILL_D = 2.05      # M2.5 tap drill
BOSS_PAD_ANGLE = 4.5    # degrees, solid shell pad behind each boss
# v1's top posts (Y-aligned, mounting face 65 mm from the centre), rotated from
# the top pole onto the filled upper-back disc, perpendicular to it.
SPEAKER_MOUNT_Y = 65.0
SPEAKER_POLE = (60, +1)         # lattice pole (filled disc) the posts stand on

SEGMENTS = 512          # circular resolution (0.7deg facets, <0.002 mm sag)
FAR = SPHERE_RADIUS + 10.0  # "beyond the shell" for revolve profiles
_ADD = OpType.Add


# ===================== DERIVED NOTCH GEOMETRY =====================
def notch_geometry(fabric_gap=FABRIC_GAP, glue_t=GLUE_T):
    """Axial (h) / radial (r) dimensions of the notch in the display frame."""
    g = {}
    g["wall_in_r"] = DISP_RIM_D / 2.0 + fabric_gap
    g["wall_out_r"] = g["wall_in_r"] + NOTCH_WALL_T
    # The sphere meets the wall at an acute (~48deg) edge, so the lip fillet
    # (tangent to wall and sphere) sits well below the sharp corner. Its centre
    # is at r = wall_in_r + f, distance R - f from the sphere centre; the lip's
    # highest point is the top of that arc, and the glass is seated against it.
    f = LIP_FILLET
    g["fillet_rc"] = g["wall_in_r"] + f
    g["fillet_hc"] = math.sqrt((SPHERE_RADIUS - f) ** 2 - g["fillet_rc"] ** 2)
    g["h_lip"] = g["fillet_hc"] + f
    g["h_front"] = g["h_lip"] - DISPLAY_RECESS
    g["h_ledge_top"] = g["h_front"] - DISP_FRONT_TO_BACK - glue_t
    g["h_ledge_bot"] = g["h_ledge_top"] - LEDGE_T
    return g


# ===================== CSG HELPERS =====================
def revolve(profile, segments=SEGMENTS):
    """Revolve a closed (r, h) polygon around the Z axis (r >= 0)."""
    pts = [(float(r), float(h)) for r, h in profile]
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    if area < 0:
        pts.reverse()
    return CrossSection([pts]).revolve(circular_segments=segments)


def cone_band(theta1_deg, theta2_deg, r0=50.0, r1=FAR):
    """Solid between two cones (half-angles theta1..theta2 from +Z), r0..r1.

    Only the part between r0 and r1 matters (the shell lives at 84.5..87.5),
    which keeps the profile away from the degenerate apex.
    """
    t1, t2 = math.radians(theta1_deg), math.radians(min(theta2_deg, 180.0))
    return revolve([(r0 * math.sin(t1), r0 * math.cos(t1)),
                    (r1 * math.sin(t1), r1 * math.cos(t1)),
                    (r1 * math.sin(t2), r1 * math.cos(t2)),
                    (r0 * math.sin(t2), r0 * math.cos(t2))])


def z_to_axis_x_rotation(dir_y, dir_z):
    """X-rotation (deg) taking +Z onto the unit vector (0, dir_y, dir_z)."""
    # Rx(t) * (0,0,1) = (0, -sin t, cos t)
    return math.degrees(math.atan2(-dir_y, dir_z))


def display_axis():
    t = math.radians(DISPLAY_TILT_UP_DEG)
    return np.array([0.0, math.sin(t), math.cos(t)])


def to_display_frame(m):
    """Orient a body built around +Z onto the (tilted) display axis."""
    n = display_axis()
    return m.rotate([z_to_axis_x_rotation(n[1], n[2]), 0.0, 0.0])


def cone_around(direction, half_angle_deg):
    """Solid cone (r 50..FAR) around an arbitrary unit direction."""
    d = np.asarray(direction, dtype=float)
    d /= np.linalg.norm(d)
    return orient(cone_band(0.0, half_angle_deg), d)


# ===================== PARTS =====================
def orient(m, direction):
    """Rotate a body built around +Z so its axis points along `direction`."""
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    elev = math.degrees(math.asin(max(-1.0, min(1.0, d[1]))))  # angle above XZ plane
    yaw = math.degrees(math.atan2(d[0], d[2]))
    return m.rotate([-elev, 0.0, 0.0]).rotate([0.0, yaw, 0.0])


def keep_spec(g, layout):
    """The shell's solid regions as cone bands: [(axis, [(theta1, theta2), ...]), ...].

    One description drives both the CSG (lattice_shell) and the analytic slit
    map (slit_patches), so the two always agree.
    """
    spec = []
    # Lattice: band n of a family spans [12n, 12n + 2.8] deg (+ overlap) from its
    # axis (0, cos a, -sin a) -- the same pattern as v1's is_cell_solid().
    n_bands = int(math.ceil(180.0 / GRID_SPACING))
    for a, ov in zip(GRID_ANGLES, GRID_OVERLAP):
        spec.append((pole_axis(a, +1),          # band 0 is the solid disc at the axis
                     [(max(k * GRID_SPACING - ov, 0.0), k * GRID_SPACING + GRID_LINE_WIDTH + ov)
                      for k in range(n_bands + 1) if k * GRID_SPACING < 180.0]))
    # Solid bezel around the notch: from the notch wall outward by BEZEL_RING_WIDTH.
    bezel = math.degrees(math.asin(min(g["wall_out_r"] / SPHERE_RADIUS, 0.999))) + BEZEL_RING_WIDTH
    spec.append((display_axis(), [(0.0, bezel)]))
    # Filled poles (the speaker disc): solid out to the first ring's outer edge,
    # ending 0.2deg inside it so the ring's own edge stays the boundary
    # (identical cone surfaces would make a degenerate union).
    for a, sign in FILLED_POLES:
        ov = GRID_OVERLAP[GRID_ANGLES.index(a)]
        spec.append((pole_axis(a, sign), [(0.0, GRID_SPACING + GRID_LINE_WIDTH + ov - 0.2)]))
    # Bottom rim (around the rolled base's down direction).
    bottom_cut_theta = math.degrees(math.acos(BOTTOM_CUT_Y / SPHERE_RADIUS))
    spec.append((-base_up(BASE_ROLL_DEG), [(0.0, 180.0 - bottom_cut_theta + BOTTOM_RIM_WIDTH)]))
    # Solid pads behind the speaker bosses.
    for direction, angle in layout["pads"]:
        d = np.asarray(direction, dtype=float)
        spec.append((d / np.linalg.norm(d), [(0.0, angle)]))
    return spec


def spec_occupied(dirs, spec):
    """Analytic version of the keep set: is each unit direction (N, 3) solid?"""
    solid = np.zeros(len(dirs), dtype=bool)
    for axis, bands in spec:
        ang = np.degrees(np.arccos(np.clip(dirs @ axis, -1.0, 1.0)))
        for t1, t2 in bands:
            solid |= (ang >= t1) & (ang <= t2)
    return solid


def _cube_faces(n, extent):
    """Gnomonic cube-map grids: [(face axis, e1, e2, u, v)], u/v in [-extent, extent]."""
    t = np.linspace(-extent, extent, n)
    u, v = np.meshgrid(t, t)
    faces = []
    for k in range(3):
        for sgn in (1.0, -1.0):
            ax = np.zeros(3); ax[k] = sgn
            e1 = np.zeros(3); e1[(k + 1) % 3] = 1.0
            e2 = np.cross(ax, e1)
            faces.append((ax, e1, e2, u, v))
    return faces


def slit_patches(spec):
    """Small solid patches closing every opening narrower than SLIT_FILL_MM.

    Three families of overlapping circles inevitably cross at grazing angles
    somewhere, leaving hairline slivers (0.3-1 mm) that read as "air gaps" and
    print poorly. The keep set is rasterised on a cube map at mid-shell radius;
    every empty region that is thin everywhere is covered by convex patches
    (built in ~1.5 mm chunks along the sliver, so a curved sliver never spills
    into a neighbouring real opening).
    """
    if SLIT_FILL_MM <= 0:
        return [], 0
    from scipy import ndimage
    from scipy.spatial import ConvexHull
    r_mid = (SPHERE_RADIUS + INNER_RADIUS) / 2
    n, ext = SLIT_MAP_RES, 1.15                  # faces overlap a little at the edges
    patches, count = [], 0
    # Two cube maps (the second turned 30deg about Y then X) so a speck that
    # falls between one grid's pixels is caught by the other.
    t = math.radians(30.0)
    ry = np.array([[math.cos(t), 0, math.sin(t)], [0, 1, 0], [-math.sin(t), 0, math.cos(t)]])
    rx = np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])
    grids = [(f[0], f[1], f[2], f[3], f[4]) for f in _cube_faces(n, ext)]
    grids += [(rx @ ry @ f[0], rx @ ry @ f[1], rx @ ry @ f[2], f[3], f[4]) for f in _cube_faces(n, ext)]
    for ax, e1, e2, u, v in grids:
        P = ax + u[..., None] * e1 + v[..., None] * e2
        r2 = u * u + v * v
        dirs = P / np.sqrt(1.0 + r2)[..., None]
        empty = ~spec_occupied(dirs.reshape(-1, 3), spec).reshape(n, n)
        mm_px = (2.0 * ext / (n - 1)) * r_mid / (1.0 + r2) ** 0.75   # local mm per pixel
        edt = ndimage.distance_transform_edt(empty) * mm_px
        lab, k = ndimage.label(empty)
        if not k:
            continue
        idx = np.arange(1, k + 1)
        widest = 2.0 * ndimage.maximum(edt, lab, idx)
        for i in np.nonzero(widest < SLIT_FILL_MM)[0]:
            rows, cols = np.nonzero(lab == i + 1)
            if rows.min() == 0 or cols.min() == 0 or rows.max() == n - 1 or cols.max() == n - 1:
                continue                          # cut by this face's edge: a neighbour owns it
            uu, vv = u[rows, cols], v[rows, cols]
            if max(abs(uu.mean()), abs(vv.mean())) > 1.0:
                continue                          # centre lies on a neighbouring face
            count += 1
            pts = np.column_stack([uu, vv])
            # chunk along the principal axis (~1.5 mm) so curved slivers stay tight
            margin = 0.5 / r_mid + 1.5 * (2.0 * ext / (n - 1))              # ~0.5 mm + 1.5 px
            if len(pts) >= 3:
                c0 = pts.mean(axis=0)
                _, vec = np.linalg.eigh(np.cov((pts - c0).T) + 1e-12 * np.eye(2))
                along = (pts - c0) @ vec[:, -1]
                step = 1.5 / (mm_px[rows, cols].mean() / (2.0 * ext / (n - 1)))  # 1.5 mm in uv
                bins = np.floor((along - along.min()) / step).astype(int)
            else:                                 # a speck: one patch
                bins = np.zeros(len(pts), dtype=int)
            for b in np.unique(bins):
                chunk = pts[(bins == b) | (bins == b + 1)]   # neighbours overlap -> no seams
                ring = np.array([[math.cos(t), math.sin(t)] for t in np.linspace(0, 2 * math.pi, 8, endpoint=False)])
                grown = (chunk[:, None, :] + margin * ring[None, :, :]).reshape(-1, 2)
                if len(chunk) >= 3:
                    try:
                        grown = grown[ConvexHull(grown).vertices]
                    except Exception:
                        pass
                d3 = ax + grown[:, :1] * e1 + grown[:, 1:] * e2
                d3 /= np.linalg.norm(d3, axis=1, keepdims=True)
                patches.append(Manifold.hull_points(np.vstack([d3 * (INNER_RADIUS - 1.0),
                                                                d3 * (SPHERE_RADIUS + 1.0)])))
    return patches, count


def lattice_shell(g, layout):
    """Spherical shell: diamond lattice + solid bezel / poles / rim / boss pads."""
    shell = (Manifold.sphere(SPHERE_RADIUS, SEGMENTS)
             - Manifold.sphere(INNER_RADIUS, SEGMENTS))
    spec = keep_spec(g, layout)
    keep = [orient(Manifold.batch_boolean([cone_band(t1, t2) for t1, t2 in bands], _ADD), axis)
            for axis, bands in spec]
    patches, n_slits = slit_patches(spec)
    print(f"  closed hairline slits < {SLIT_FILL_MM} mm: {n_slits} found over two scans, "
          f"{len(patches)} patch(es)")
    return shell ^ Manifold.batch_boolean(keep + patches, _ADD)


def notch_add(g):
    """Wall + ledge ring (before the pocket is cut), clipped to the sphere."""
    ring = revolve([(LEDGE_INNER_R, g["h_ledge_bot"]), (g["wall_out_r"], g["h_ledge_bot"]),
                    (g["wall_out_r"], FAR), (LEDGE_INNER_R, FAR)])
    return to_display_frame(ring) ^ Manifold.sphere(SPHERE_RADIUS, SEGMENTS)


def notch_cut(g):
    """Display pocket + stack opening + rounded lip, in the display frame."""
    R, f, rw = SPHERE_RADIUS, LIP_FILLET, g["wall_in_r"]
    pocket = revolve([(0.0, g["h_ledge_top"]), (rw, g["h_ledge_top"]),
                      (rw, FAR), (0.0, FAR)])
    opening = revolve([(0.0, g["h_ledge_bot"] - 1.0), (LEDGE_INNER_R, g["h_ledge_bot"] - 1.0),
                       (LEDGE_INNER_R, FAR), (0.0, FAR)])
    # Lip fillet: arc of radius f tangent to the wall (r = rw) and the outer sphere.
    rc, hc = g["fillet_rc"], g["fillet_hc"]
    s = R / (R - f)
    t2 = (rc * s, hc * s)                       # tangent point on the sphere
    t2_out = (rc * (s + 5.0 / R), hc * (s + 5.0 / R))
    a2 = math.atan2(t2[1] - hc, t2[0] - rc)     # arc from the sphere tangent...
    arc = [(rc + f * math.cos(a2 + (math.pi - a2) * i / 32),
            hc + f * math.sin(a2 + (math.pi - a2) * i / 32)) for i in range(33)]
    # ...to the wall tangent (rw, hc); close through the pocket (r < rw) so the
    # cutter overlaps the pocket instead of sharing its wall surface exactly.
    fillet = revolve([(rw - 0.5, FAR), t2_out] + arc + [(rw - 0.5, hc)])
    return to_display_frame(Manifold.batch_boolean([pocket, opening, fillet], _ADD))


def pole_axis(family_angle, sign):
    ar = math.radians(family_angle)
    return np.array([0.0, sign * math.cos(ar), -sign * math.sin(ar)])


def speaker_layout():
    """Speaker post rotation (about X, from the top pole) + shell pads (model frame)."""
    c = pole_axis(*SPEAKER_POLE)
    rot = math.degrees(math.atan2(c[2], c[1]))      # Rx(rot) takes +Y onto c
    t = math.radians(rot)
    pads = []
    for sx, sz in SPEAKER_HOLES_XZ:
        y = math.sqrt(INNER_RADIUS**2 - sx**2 - sz**2)
        pads.append(([sx, y * math.cos(t) - sz * math.sin(t), y * math.sin(t) + sz * math.cos(t)],
                     BOSS_PAD_ANGLE))
    summary = f"2x M2.5 posts on the upper-back disc, mounting face {SPEAKER_MOUNT_Y:.0f}mm from centre"
    return {"rot": rot, "pads": pads, "summary": summary}


def speaker_bosses(layout):
    add, cut = [], []
    for sx, sz in SPEAKER_HOLES_XZ:
        y_wall = math.sqrt(INNER_RADIUS**2 - sx**2 - sz**2)
        h = y_wall + 1.0 - SPEAKER_MOUNT_Y          # 1 mm into the shell, never through
        post = Manifold.cylinder(h, POST_OD / 2.0, circular_segments=48)
        hole = Manifold.cylinder(y_wall - SPEAKER_MOUNT_Y + 1.0, TAP_DRILL_D / 2.0,
                                 circular_segments=24)
        # cylinders are built along +Z from z=0; stand them up along +Y
        add.append(post.rotate([-90.0, 0.0, 0.0]).translate([sx, SPEAKER_MOUNT_Y, sz]))
        cut.append(hole.rotate([-90.0, 0.0, 0.0]).translate([sx, SPEAKER_MOUNT_Y - 1.0, sz]))
    add = [m.rotate([layout["rot"], 0.0, 0.0]) for m in add]
    cut = [m.rotate([layout["rot"], 0.0, 0.0]) for m in cut]
    return add, cut


def base_up(base_roll):
    """'Up' of the standing case in the model frame: +Y rolled back by base_roll
    (toward +Z, i.e. away from the display), which tilts the display upward."""
    r = math.radians(base_roll)
    return np.array([0.0, math.cos(r), math.sin(r)])


def bottom_cut(m, base_roll=0.0):
    """Remove everything below the flat base (along base_up, below BOTTOM_CUT_Y)."""
    big = 4 * SPHERE_RADIUS
    box = Manifold.cube([big, big, big]).translate([-big / 2, BOTTOM_CUT_Y - big, -big / 2])
    return m - box.rotate([base_roll, 0.0, 0.0])   # Rx(roll) takes +Y onto base_up


def stand_upright(m, base_roll=0.0):
    """Rotate the model so it stands on its base (base_up -> +Y) for export."""
    return m.rotate([-base_roll, 0.0, 0.0])


def generate_case(fabric_gap=FABRIC_GAP, glue_t=GLUE_T):
    """Case in the model frame (see stand_upright() for the export orientation)."""
    t0 = time.time()
    g = notch_geometry(fabric_gap, glue_t)
    layout = speaker_layout()
    g["speaker"] = layout
    print(f"Generating DODI case v2 (R={SPHERE_RADIUS}, fabric gap {fabric_gap} mm, "
          f"glue {glue_t} mm, display {DISPLAY_TILT_UP_DEG + BASE_ROLL_DEG:.0f}deg up)...")
    print(f"  speaker: {layout['summary']}")
    body = lattice_shell(g, layout)
    print(f"  lattice shell ({time.time()-t0:.1f}s)")
    body = body + notch_add(g)
    post_add, post_cut = speaker_bosses(layout)
    body = Manifold.batch_boolean([body] + post_add, _ADD)
    body = body - notch_cut(g) - Manifold.batch_boolean(post_cut, _ADD)
    body = bottom_cut(body, BASE_ROLL_DEG)
    # Lattice nodes where band edges graze each other leave zero-volume shards;
    # keep only the real body so the exported mesh is one clean solid.
    parts = body.decompose()
    body = max(parts, key=lambda p: p.volume())
    dropped = sum(p.volume() for p in parts) - body.volume()
    print(f"  notch + bosses + base ({time.time()-t0:.1f}s); dropped {len(parts)-1} "
          f"degenerate shard(s), {dropped:.4f} mm^3")
    return body, g


def fit_test_ring(case, g):
    """Just the notch region: lip, wall, ledge and a strip of bezel shell."""
    margin = 4.0   # enough outer shell to glue the fabric onto
    r_out = g["wall_out_r"] + margin
    keep = revolve([(0.0, g["h_ledge_bot"] - 1.0), (r_out, g["h_ledge_bot"] - 1.0),
                    (r_out, FAR), (0.0, FAR)])
    return case ^ to_display_frame(keep)


# ===================== OUTPUT =====================
def mesh_arrays(m):
    mesh = m.to_mesh()
    return (np.asarray(mesh.vert_properties)[:, :3].astype(np.float64),
            np.asarray(mesh.tri_verts).astype(np.int64))


def write_obj(m, path, header_lines, name="DODI_Case"):
    v, f = mesh_arrays(m)
    with open(path, "w") as fh:
        for line in header_lines:
            fh.write(f"# {line}\n")
        fh.write(f"# Vertices: {len(v)}, Faces: {len(f)}\n\no {name}\n\n")
        fh.write("\n".join(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in v))
        fh.write("\n\n")
        fh.write("\n".join(f"f {a} {b} {c}" for a, b, c in f + 1))
        fh.write("\n")
    print(f"  {path}: {len(v)} verts, {len(f)} tris, {os.path.getsize(path)/1e6:.1f} MB")


def write_stl(m, path, name="DODI_Case"):
    v, f = mesh_arrays(m)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(name.encode().ljust(80, b" ")[:80])
        fh.write(np.uint32(len(f)).tobytes())
        fh.write(rec.tobytes())
    print(f"  {path}: {len(f)} tris, {os.path.getsize(path)/1e6:.1f} MB")


def header(g, fabric_gap, glue_t):
    return [
        "DODI AI Assistant Spherical Case v2 (glue-in display notch)",
        f"R={SPHERE_RADIUS}mm, wall={WALL_THICKNESS}mm, height="
        f"{SPHERE_RADIUS - BOTTOM_CUT_Y:.0f}mm, units=mm, +Y up, display faces +Z",
        f"Diamond lattice: {len(GRID_ANGLES)} families, {GRID_SPACING}deg spacing, "
        f"{GRID_LINE_WIDTH}deg width",
        f"Display: Waveshare 3.4in DSI (C), rim D{DISP_RIM_D}, "
        f"{DISPLAY_TILT_UP_DEG + BASE_ROLL_DEG:.0f}deg above horizontal when standing",
        f"Notch: wall ID {2*g['wall_in_r']:.1f}mm (fabric gap {fabric_gap}mm/side), "
        f"ledge ID {2*LEDGE_INNER_R:.1f}mm, glue {glue_t}mm, glass flush with lip",
        f"Speaker: {g['speaker']['summary']}; no Pi standoffs",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fabric-gap", type=float, default=FABRIC_GAP)
    ap.add_argument("--glue", type=float, default=GLUE_T)
    ap.add_argument("--fit-test", type=float, nargs="*", metavar="GAP",
                    help="also write notch fit-test rings for these fabric gaps")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    t = time.time()
    case, g = generate_case(args.fabric_gap, args.glue)
    print(f"  manifold status: {case.status()}, genus {case.genus()}, "
          f"volume {case.volume()/1000:.1f} cm^3")
    hdr = header(g, args.fabric_gap, args.glue)
    upright = stand_upright(case, BASE_ROLL_DEG)
    write_obj(upright, os.path.join(args.outdir, "case_v2.obj"), hdr, "DODI_Case_v2")
    write_stl(upright, os.path.join(args.outdir, "case_v2.stl"), "DODI_Case_v2")

    for gap in args.fit_test or []:
        c, gg = (case, g) if gap == args.fabric_gap else generate_case(gap, args.glue)
        ring = fit_test_ring(c, gg)
        write_stl(ring, os.path.join(args.outdir, f"fit_test_ring_gap{gap:.1f}.stl"),
                  name=f"DODI notch fit test gap {gap}")
    print(f"\nDone in {time.time()-t:.1f}s")


if __name__ == "__main__":
    main()
