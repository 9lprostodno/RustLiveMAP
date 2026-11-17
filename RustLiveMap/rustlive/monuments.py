"""Rust Live Map: монументы из .map (подписи на карте). Без Qt."""

import json
from pathlib import Path

# что не подписываем: входы в метро, пещеры, озёра/болота и колодцы — только шум на карте
HIDE_KINDS = {"train_tunnel_entrance", "cave", "natural_feature", "resource_location"}


def monuments_file_name(world, seed):
    return f"monuments_{int(world)}_{seed}.json"


def compact_monuments(doc):
    """monuments.json парсера -> {"world": размер, "items": [{"n": имя, "x": X, "z": Z, "k": тип}]} (мировые X/Z, центр карты — 0)."""
    items = []
    for m in (doc or {}).get("monuments") or []:
        md = m.get("metadata") or {}
        pos = m.get("position") or {}
        if "x" not in pos or "z" not in pos:
            continue
        name = md.get("display_name") or m.get("name") or ""
        kind = (md.get("classification") or {}).get("kind") or ""
        items.append({"n": str(name), "x": round(float(pos["x"]), 1), "z": round(float(pos["z"]), 1), "k": kind})
    world = ((doc or {}).get("map") or {}).get("world_size")
    return {"world": int(world) if world else 0, "items": items}


def save_monuments(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def load_monuments(path):
    """Список {"n","x","z","k"} из кэша, который стоит подписывать; None, если файла нет или он битый."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [m for m in data["items"] if m.get("k") not in HIDE_KINDS and m.get("n")]
    except (OSError, ValueError, KeyError, TypeError):
        return None
