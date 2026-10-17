"""Rendu 3D physique (Mitsuba 3) d'un design de l'Atelier : un vrai carreau par case, couleurs de la palette Atelier."""
import json, glob, sys, os
import numpy as np
import mitsuba as mi

try:
    mi.set_variant('llvm_ad_rgb')
except Exception:
    mi.set_variant('scalar_rgb')

WORK = os.environ.get('ATELIER_WORK', '/tmp/atelier-sync')
os.makedirs(WORK, exist_ok=True)
P = {"mim-103":"#F8F6F0","mim-101":"#1A1816","mim-106":"#8C8C8C","mim-220":"#2E4A36","mim-221":"#9AD18C",
     "mim-320":"#1E4FA8","mim-314":"#8FE0ED","mim-335":"#1EC6DA","mim-302":"#FDCB2E","mim-231":"#F2A838",
     "mim-336":"#DCC49C","mim-176":"#F2654C","mim-304":"#F5691A","mim-172":"#A8452E","mim-210":"#3E2718",
     "mim-212":"#9C6B45","mim-255":"#F6C2CC","mim-115":"#39A68E"}

def lin(h):
    c = np.array([int(h[k:k+2], 16) for k in (1, 3, 5)])/255.0
    return np.where(c <= 0.04045, c/12.92, ((c+0.055)/1.055)**2.4)

def rounded_tile(N=10):
    """Carreau unité : 1 x 1 x 0.33, arêtes arrondies (rayon 0.11), dessus très légèrement bombé."""
    h = np.array([0.5, 0.5, 0.165]); r = 0.11
    # points sur la surface d'un cube [-1,1]^3, resserrés près des arêtes
    t = np.linspace(-1, 1, N); t = np.sign(t)*np.abs(t)**0.6
    verts, faces = [], []
    for axis in range(3):
        for sgn in (-1, 1):
            a, b = [i for i in range(3) if i != axis]
            base = len(verts)
            for i in range(N):
                for j in range(N):
                    q = np.zeros(3); q[axis] = sgn; q[a] = t[i]; q[b] = t[j]
                    verts.append(q)
            for i in range(N-1):
                for j in range(N-1):
                    v0 = base+i*N+j; v1 = v0+1; v2 = v0+N; v3 = v2+1
                    tri = [(v0, v2, v1), (v1, v2, v3)]
                    # orientation vers l'extérieur
                    if (sgn > 0) != ((axis == 1)):
                        tri = [(x, z, y) for x, y, z in tri]
                    faces += tri
    q = np.array(verts)*h
    inner = np.clip(q, -(h-r), h-r)
    d = q-inner; n = np.linalg.norm(d, axis=1, keepdims=True); n[n == 0] = 1
    p = inner+r*d/n
    # dessus légèrement bombé (verre coulé)
    top = p[:, 2] > 0
    p[top, 2] += 0.012*(1-(2*p[top, 0])**2)*(1-(2*p[top, 1])**2)
    return p, np.array(faces)

def fix_orientation(p, f):
    # oriente chaque triangle vers l'extérieur (le centre du carreau est à l'origine)
    a, b, c = p[f[:, 0]], p[f[:, 1]], p[f[:, 2]]
    nrm = np.cross(b-a, c-a); cen = (a+b+c)/3
    bad = (nrm*cen).sum(1) < 0
    f[bad] = f[bad][:, [0, 2, 1]]
    return f

def build(d, seed=3):
    w = d['gridW']; c = d['cells']; idx = [i for i, v in enumerate(c) if v]
    gx0 = min(i % w for i in idx); gy0 = min(i//w for i in idx)
    cw = max(i % w for i in idx)-gx0+1; ch = max(i//w for i in idx)-gy0+1
    rng = np.random.default_rng(seed)
    tp, tf = rounded_tile(); tf = fix_orientation(tp, tf)
    pitch = 1.04  # carreau 1.0 + joint 0.04 (pose serrée)
    V, F, C, Rg = [], [], [], []
    for k, i in enumerate(idx):
        x = (i % w-gx0-(cw-1)/2)*pitch; y = -((i//w-gy0)-(ch-1)/2)*pitch
        ang = np.radians(rng.normal(0, 0.35))
        rx, ry = np.radians(rng.normal(0, 0.8, 2))
        Rz = np.array([[np.cos(ang), -np.sin(ang), 0], [np.sin(ang), np.cos(ang), 0], [0, 0, 1]])
        Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
        Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
        s = 1+rng.normal(0, 0.004)
        pv = (tp*[s, s, 1+rng.normal(0, 0.04)])@(Rz@Rx@Ry).T + [x+rng.normal(0, 0.004), y+rng.normal(0, 0.004), 0.165+0.02+rng.normal(0, 0.006)]
        base = lin(P[c[i]])*(1+rng.normal(0, 0.04))*(1+rng.normal(0, 0.02, 3))
        F.append(tf+len(V)*len(tp)); V.append(pv)
        C.append(np.tile(np.clip(base, 0, 1), (len(tp), 1)))
        Rg.append(np.full(len(tp), np.clip(rng.normal(0.16, 0.06), 0.05, 0.35)))
    V = np.concatenate(V); F = np.concatenate(F); C = np.concatenate(C); Rg = np.concatenate(Rg)
    # 3 groupes de rugosité (verre plus ou moins satiné d'un carreau à l'autre)
    per = len(tp); ntile = len(V)//per
    grp = np.digitize(Rg[::per], [0.12, 0.2])
    mesh = []
    for g in range(3):
        sel = np.where(grp == g)[0]
        if not len(sel): continue
        vi = (sel[:, None]*per+np.arange(per)).ravel()
        remap = np.full(len(V), -1); remap[vi] = np.arange(len(vi))
        fsel = F[np.isin(F[:, 0]//per, sel)]
        Vg, Cg, Fg = V[vi], C[vi], remap[fsel]
        path = WORK+f'/tiles{g}.ply'
        with open(path, 'wb') as fh:
            fh.write((f"ply\nformat binary_little_endian 1.0\nelement vertex {len(Vg)}\nproperty float x\nproperty float y\nproperty float z\n"
                      f"property float r\nproperty float g\nproperty float b\n"
                      f"element face {len(Fg)}\nproperty list uchar int vertex_indices\nend_header\n").encode())
            fh.write(np.concatenate([Vg, Cg], 1).astype('<f4').tobytes())
            fd = np.zeros(len(Fg), dtype=[('n', 'u1'), ('i', '<i4', 3)]); fd['n'] = 3; fd['i'] = Fg
            fh.write(fd.tobytes())
        mesh.append((path, [0.08, 0.16, 0.26][g]))
    return mesh, cw*pitch, ch*pitch

def paper_texture(path, n=1024, seed=5):
    rng = np.random.default_rng(seed)
    from PIL import Image, ImageFilter
    a = rng.normal(0.5, 0.5, (n, n))
    img = Image.fromarray(((a*0.5+0.25).clip(0, 1)*255).astype('uint8')).filter(ImageFilter.GaussianBlur(1.2))
    img.save(path)

def render(d, out, S=1200, spp=256):
    mesh, W, H = build(d)
    tex = WORK+'/paper.png'
    if not os.path.exists(tex): paper_texture(tex)
    span = max(W, H)*1.10
    dist = 120.0
    fov = np.degrees(2*np.arctan(span/2/dist))
    bsdf = ({'type': 'principled',
        'base_color': {'type': 'mesh_attribute', 'name': 'vertex_color'},
        'specular': 0.55, 'clearcoat': 0.35, 'clearcoat_gloss': 0.75})
    sd = ({
        'type': 'scene',
        'integrator': {'type': 'path', 'max_depth': 6},
        'sensor': {'type': 'perspective', 'fov': float(fov), 'fov_axis': 'x',
                   'to_world': mi.ScalarTransform4f().look_at(origin=[0.0, -span*0.02, dist], target=[0, 0, 0], up=[0, 1, 0]),
                   'film': {'type': 'hdrfilm', 'width': S, 'height': S, 'rfilter': {'type': 'gaussian'}},
                   'sampler': {'type': 'independent', 'sample_count': spp}},
        'wall': {'type': 'rectangle', 'to_world': mi.ScalarTransform4f().scale([span*3, span*3, 1]),
                 'bsdf': {'type': 'bumpmap',
                          'bump': {'type': 'bitmap', 'filename': tex, 'raw': True,
                                   'to_uv': mi.ScalarTransform4f().scale([span*0.9, span*0.9, 1])},
                          'scale': 0.03,
                          'nested': {'type': 'diffuse', 'reflectance': {'type': 'rgb', 'value': [0.82, 0.80, 0.76]}}}},
        # fenêtre en haut à gauche : grande source douce
        'window': {'type': 'rectangle',
                   'to_world': mi.ScalarTransform4f().look_at(origin=[-span*1.6, span*2.2, span*1.8], target=[0, 0, 0], up=[0, 0, 1]).scale([span*0.8, span*1.2, 1]),
                   'emitter': {'type': 'area', 'radiance': {'type': 'rgb', 'value': [7.8, 7.5, 7.1]}}},
        # grande boîte à lumière près de l'appareil : reflets dans le verre, différents selon l'inclinaison de chaque carreau
        'softbox': {'type': 'rectangle',
                   'to_world': mi.ScalarTransform4f().look_at(origin=[-span*0.35, span*0.45, dist*0.9], target=[0, 0, 0], up=[0, 1, 0]).scale([span*0.25, span*0.12, 1]),
                   'emitter': {'type': 'area', 'radiance': {'type': 'rgb', 'value': [6.0, 6.0, 6.0]}}},
        'env': {'type': 'constant', 'radiance': {'type': 'rgb', 'value': [0.47, 0.48, 0.50]}},
    })
    for k, (path, rough) in enumerate(mesh):
        b = dict(bsdf); b['roughness'] = rough
        sd[f'tiles{k}'] = {'type': 'ply', 'filename': path, 'face_normals': True, 'bsdf': b}
    scene = mi.load_dict(sd)
    img = mi.render(scene)
    bmp = mi.Bitmap(img).convert(mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.UInt8, srgb_gamma=True)
    bmp.write(out)

def mesh_with_bsdf(mesh, bsdf):
    mesh.set_bsdf(bsdf)
    return mesh

