"""Сведения о VRM прямо из файла: название, автор, лицензия, превью. Без сторонних библиотек."""
import json
import struct
from pathlib import Path

LICENSE_RU = {
    "everyone": "аватар — всем", "onlyAuthor": "аватар — только автору", "author": "аватар — только автору",
    "onlySeparatelyLicensedPerson": "аватар — по отдельной лицензии", "explicitlyLicensedPerson": "аватар — по договорённости",
    "Everyone": "аватар — всем", "OnlyAuthor": "аватар — только автору", "ExplicitlyLicensedPerson": "аватар — по договорённости",
}


def _glb(path):
    b = Path(path).read_bytes()
    if b[:4] != b"glTF":
        raise ValueError("не GLB")
    n = struct.unpack("<I", b[12:16])[0]
    js = json.loads(b[20:20 + n])
    off = 20 + n
    bin_ = b""
    if off + 8 <= len(b):
        ln = struct.unpack("<I", b[off:off + 4])[0]
        bin_ = b[off + 8:off + 8 + ln]
    return js, bin_


def read(path, thumbs_dir):
    js, bin_ = _glb(path)
    ext = js.get("extensions", {})
    v1 = (ext.get("VRMC_vrm") or {}).get("meta")
    v0 = (ext.get("VRM") or {}).get("meta")
    m = v1 or v0 or {}
    info = {"name": Path(path).name, "spec": "1.0" if v1 else "0.x",
            "title": m.get("name") or m.get("title") or Path(path).stem,
            "author": ", ".join(m.get("authors") or []) or m.get("author") or "",
            "credit": (m.get("creditNotation") == "required") if v1 else None,
            "avatar": LICENSE_RU.get(m.get("avatarPermission") or m.get("allowedUserName") or "", ""),
            "license": m.get("licenseUrl") or m.get("licenseName") or m.get("otherLicenseUrl") or "",
            "commercial": m.get("commercialUsage") or m.get("commercialUssageName") or "",
            "size_mb": round(Path(path).stat().st_size / 2**20, 1), "thumb": None}
    # превью: VRM1 — meta.thumbnailImage (индекс картинки), VRM0 — meta.texture (индекс текстуры)
    img_idx = m.get("thumbnailImage")
    if img_idx is None and m.get("texture") is not None and js.get("textures"):
        try:
            img_idx = js["textures"][m["texture"]]["source"]
        except (IndexError, KeyError):
            img_idx = None
    if img_idx is not None and bin_:
        try:
            img = js["images"][img_idx]
            bv = js["bufferViews"][img["bufferView"]]
            data = bin_[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
            suffix = ".png" if "png" in img.get("mimeType", "png") else ".jpg"
            Path(thumbs_dir).mkdir(exist_ok=True)
            out = Path(thumbs_dir) / (Path(path).stem + suffix)
            if not out.exists() or out.stat().st_mtime < Path(path).stat().st_mtime:
                out.write_bytes(data)
            info["thumb"] = f"/models/.thumbs/{out.name}"
        except Exception:
            pass
    return info
