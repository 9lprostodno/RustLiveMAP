"""Rust Live Map: пароли в config.json хранятся зашифрованными (Windows DPAPI: ключ привязан к вашей учётной записи Windows).
Без Qt и без сторонних пакетов. Не Windows — пароли остаются как есть (там шифровать нечем)."""

import base64
import sys

PREFIX = "dpapi:"
SECRET_KEYS = ("rcon_password", "ftp_pass")       # поля настроек, которые шифруем
_ENTROPY = b"RustLiveMap/v1"


def available():
    return sys.platform == "win32"


def _blob_api():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    ptr = ctypes.POINTER(Blob)
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        fn.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
        fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return ctypes, Blob, make, crypt32, kernel32


def _call(encrypt, data):
    ctypes, Blob, make, crypt32, kernel32 = _blob_api()
    src, keep1 = make(data)
    ent, keep2 = make(_ENTROPY)
    out = Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI: операция не удалась")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
        del keep1, keep2


def _encrypt(data):
    return _call(True, data)


def _decrypt(data):
    return _call(False, data)


def is_sealed(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(text):
    """Строка -> 'dpapi:<base64>'. Пустое и уже зашифрованное не трогаем; без DPAPI возвращаем как есть."""
    if not text or is_sealed(text) or not available():
        return text
    try:
        return PREFIX + base64.b64encode(_encrypt(text.encode("utf-8"))).decode("ascii")
    except Exception:
        return text                  # шифрование не сработало: лучше сохранить настройки, чем потерять их


def unseal(value):
    """'dpapi:…' -> пароль. Если расшифровать нельзя (конфиг с другого компьютера или учётной записи) — пустая строка."""
    if not is_sealed(value):
        return value
    try:
        return _decrypt(base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    except Exception:
        return ""


def _dicts(cfg):
    yield cfg
    for p in cfg.get("profiles") or []:
        if isinstance(p, dict) and isinstance(p.get("settings"), dict):
            yield p["settings"]


def open_secrets(cfg):
    """После чтения файла: расшифровывает пароли в самом cfg. Возвращает (были_открытые_пароли, не_удалось_расшифровать)."""
    plain = failed = False
    for d in _dicts(cfg):
        for k in SECRET_KEYS:
            v = d.get(k)
            if is_sealed(v):
                d[k] = unseal(v)
                failed = failed or not d[k]
            elif v:
                plain = True
    return plain, failed


def sealed_copy(cfg):
    """Копия cfg для записи на диск: пароли зашифрованы (сам cfg остаётся с открытыми)."""
    out = dict(cfg)
    if isinstance(out.get("profiles"), list):
        out["profiles"] = [dict(p, settings=dict(p["settings"])) if isinstance(p.get("settings"), dict) else p
                           for p in out["profiles"]]
    for d in _dicts(out):
        for k in SECRET_KEYS:
            if k in d:
                d[k] = seal(d[k])
    return out


def has_plain(raw):
    """В прочитанном json есть пароль в открытом виде?"""
    return any(d.get(k) and not is_sealed(d.get(k)) for d in _dicts(raw) for k in SECRET_KEYS)
