"""Bake the zone's NPCs/monsters as POSED STATIC meshes into a .glb built with the
EXACT same pipeline as the map (export_map.Glb + the ROSE_zone root). Because it
reuses the map's proven, 1:1-with-the-web import path, the NPCs land at the right
place / size / orientation in UE5 with ZERO per-actor tweaking — no skeletal glTF
import quirks (which silently rescale/rotate skinned meshes).

Each unique character is skinned to a frame of its idle animation (a natural
standing pose, not a T-pose), then instanced at every MOB placement and ringed at
every REGEN spawn point — same world matrices the web viewer uses.

Output: <bundle>/npcs_posed.glb   (import like the map: static meshes at origin).
Trade-off vs the skeletal route: the NPCs are posed (not live-animated), but they
are pixel-1:1 with the viewer. Live animation can be layered on later in UE.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export_map                       # Glb builder + ROSE_zone root + compose()
import export_npcs                      # placement data (compute)
import export_npc_models as NM          # rig/parts loaders + math
from rose_zms import read_zms

POSE_FRAME = 0          # idle frame to freeze (0 = natural standing pose)


def _anim_globals(bones, anim, K):
    """World matrix of every bone at idle frame K (file scale)."""
    A = [None] * len(bones)
    for i, b in enumerate(bones):
        q = b["rot"]
        p = b["pos"]
        if anim:
            rf = anim["rot"][i] if i < len(anim["rot"]) else None
            pf = anim["pos"][i] if i < len(anim["pos"]) else None
            if rf:
                q = rf[min(K, len(rf) - 1)]
            if pf:
                p = pf[min(K, len(pf) - 1)]
        L = NM._quat_xyzw_to_mat(*q)
        L[0, 3], L[1, 3], L[2, 3] = p
        par = b["parent"]
        A[i] = (A[par] @ L) if (0 <= par < i and A[par] is not None) else L
    return A


def _posed_part(zms, skin_mats):
    """Skin one part's mesh by the per-bone skin matrices -> (pos, nrm, uv, idx)."""
    nv = len(zms.positions)
    scale = 100.0 if zms.version >= 7 else 1.0
    pos = np.array(zms.positions, dtype=np.float64) * scale
    nrm = (np.array(zms.normals, dtype=np.float64) if zms.normals
           else np.zeros((nv, 3), np.float64))
    uv = (np.array(zms.uvs[0], dtype=np.float32) if zms.uvs else np.zeros((nv, 2), np.float32))
    idx = np.array(zms.faces, dtype=np.uint32).reshape(-1)

    nb = skin_mats.shape[0]
    B = np.zeros((nv, 4), dtype=np.int64)
    W = np.zeros((nv, 4), dtype=np.float64)
    if zms.bones and zms.weights and zms.bone_indices:
        pal = zms.bone_indices
        for v in range(nv):
            bi, w = zms.bones[v], zms.weights[v]
            for k in range(4):
                pi = bi[k] if k < len(bi) else 0
                gj = pal[pi] if 0 <= pi < len(pal) else 0
                B[v, k] = gj if 0 <= gj < nb else 0
                W[v, k] = w[k] if k < len(w) else 0.0
        s = W.sum(1, keepdims=True)
        W = np.where(s > 0, W / s, np.array([1.0, 0, 0, 0]))
    else:
        W[:, 0] = 1.0       # rigid -> root bone

    posh = np.concatenate([pos, np.ones((nv, 1))], axis=1)     # (nv,4)
    out_pos = np.zeros((nv, 3))
    out_nrm = np.zeros((nv, 3))
    for k in range(4):
        M = skin_mats[B[:, k]]                                 # (nv,4,4)
        out_pos += W[:, k:k + 1] * np.einsum("nij,nj->ni", M, posh)[:, :3]
        out_nrm += W[:, k:k + 1] * np.einsum("nij,nj->ni", M[:, :3, :3], nrm)
    n = np.linalg.norm(out_nrm, axis=1, keepdims=True)
    out_nrm = np.where(n > 1e-9, out_nrm / np.maximum(n, 1e-9), [0, 0, 1])
    return out_pos.astype(np.float32), out_nrm.astype(np.float32), uv, idx


def _char_meshes(glb, char_id, chrf, zsc, cache):
    """Build (and cache) the posed glTF mesh indices for one character."""
    if char_id in cache:
        return cache[char_id]
    out = []
    if 0 <= char_id < len(chrf.characters):
        ch = chrf.characters[char_id]
        if ch and ch.objects:
            bones, anim = NM._char_bones_anim(chrf, ch)
            if bones:
                G = NM._bone_globals(bones)
                A = _anim_globals(bones, anim, POSE_FRAME)
                nb = len(bones)
                skin = np.zeros((nb, 4, 4))
                for i in range(nb):
                    skin[i] = A[i] @ np.linalg.inv(G[i])
                for (mesh_rel, mat_rel, two_sided) in NM._char_parts(zsc, ch):
                    ab = NM._resolve(mesh_rel)
                    if not ab:
                        continue
                    try:
                        zms = read_zms(ab)
                    except Exception:
                        continue
                    if not zms.positions or not zms.faces:
                        continue
                    try:
                        pp, nn, uu, ii = _posed_part(zms, skin)
                    except Exception:
                        continue
                    mat = glb.material_for_texture(NM._resolve(mat_rel) if mat_rel else None,
                                                   alpha=False, mode="OPAQUE", double=bool(two_sided),
                                                   kind="npc")
                    mi = glb.mesh(glb.add_vec3(pp), glb.add_indices(ii), mat,
                                  glb.add_vec3(nn), glb.add_vec2(uu))
                    out.append(mi)
    cache[char_id] = out
    return out


def _quat_z(theta):
    return (0.0, 0.0, float(np.sin(theta / 2)), float(np.cos(theta / 2)))   # xyzw


def build(key, out_glb):
    import rose_chr
    chrp = NM._resolve("NPC/LIST_NPC.CHR")
    zscp = NM._resolve("NPC/PART_NPC.ZSC")
    if not chrp or not zscp:
        raise RuntimeError("LIST_NPC.CHR / PART_NPC.ZSC not found")
    chrf = rose_chr.read_chr(chrp)
    from rose_zsc import read_zsc
    zsc = read_zsc(zscp)

    data = export_npcs.compute(key)
    glb = export_map.Glb()
    cache = {}
    root_children = []
    n_npc = n_mob = 0

    for npc in data["npcs"]:
        if npc["kind"] == "NPC":
            meshes = _char_meshes(glb, npc["object_id"], chrf, zsc, cache)
            if not meshes:
                continue
            M = export_map.compose(npc["pos"], npc["rot"], npc["scale"])
            for mi in meshes:
                root_children.append(glb.node(mesh=mi, matrix=M.flatten(order="F")))
            n_npc += 1
        else:
            seen, mobs = set(), []
            for mb in npc.get("mobs", []):
                if mb["id"] not in seen:
                    seen.add(mb["id"]); mobs.append(mb["id"])
            mobs = [m for m in mobs if _char_meshes(glb, m, chrf, zsc, cache)]
            nm = min(len(mobs), 8)
            ring = max(800.0, nm * 280.0)
            for k in range(nm):
                ang = (k / float(nm)) * 2.0 * np.pi if nm > 1 else 0.0
                ox = np.cos(ang) * ring if nm > 1 else 0.0
                oy = np.sin(ang) * ring if nm > 1 else 0.0
                p = [npc["pos"][0] + ox, npc["pos"][1] + oy, npc["pos"][2]]
                M = export_map.compose(p, _quat_z(ang + np.pi), [1, 1, 1])
                for mi in _char_meshes(glb, mobs[k], chrf, zsc, cache):
                    root_children.append(glb.node(mesh=mi, matrix=M.flatten(order="F")))
                n_mob += 1

    glb.write(out_glb, root_children)
    return {"npcs": n_npc, "monsters": n_mob, "characters": len([c for c in cache.values() if c]),
            "nodes": len(root_children), "bytes": os.path.getsize(out_glb)}


if __name__ == "__main__":
    import json
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k, "npcs_posed.glb")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(json.dumps(build(k, out), indent=2))
