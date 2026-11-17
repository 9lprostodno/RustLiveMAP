"""Rust Live Map: доступ к файлам сервера — локальная папка, FTP/FTPS или SFTP (SSH, для Linux-серверов).
Без Qt. У каждой реализации одинаковые методы, пути на сервере — со слешами «/»."""

import base64
import ftplib
import hashlib
import os
import posixpath
import shutil
import stat
import sys
from pathlib import Path


class Entry:
    __slots__ = ("name", "is_dir", "size", "mtime")

    def __init__(self, name, is_dir, size=0, mtime=0):
        self.name, self.is_dir, self.size, self.mtime = name, bool(is_dir), int(size or 0), float(mtime or 0)


def proto_of(cfg):
    """'local' | 'smb' | 'ftp' | 'sftp'. Старые конфиги знали только галочку FTP."""
    p = cfg.get("srv_proto")
    if p in ("local", "ftp", "sftp", "smb"):
        return p
    return "ftp" if cfg.get("srv_ftp") else "local"


def safe_name(name):
    """Имя файла от удалённого сервера пригодно для локального диска: без путей, «..» и диска (C:)."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in '/\\\0:')


class BaseFs:
    """Общие операции. Конкретные классы реализуют listdir/download/upload/remove_file/remove_dir/rename/mkdir."""
    host_fp = None            # отпечаток ключа SSH-сервера (только SFTP)

    # ---- пути (по умолчанию как на Linux) ----
    def join(self, a, *parts):
        return posixpath.join(a, *parts)

    def parent(self, p):
        return posixpath.dirname(p.rstrip("/")) or "/"

    def base(self, p):
        return posixpath.basename(p.rstrip("/"))

    def within(self, root, p):
        """p лежит внутри root (или это сам root)? Защита от выхода за пределы папки сервера через «..»."""
        r = posixpath.normpath(root)
        q = posixpath.normpath(p)
        return q == r or q.startswith(r.rstrip("/") + "/")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        pass

    def exists(self, path):
        parent = self.parent(path)
        return any(e.name == self.base(path) for e in self.listdir(parent))

    def replace(self, src, dst):
        """Переименовать src в dst, затирая dst, если он есть."""
        if self.exists(dst):
            self.remove_file(dst)
        self.rename(src, dst)

    def get_mode(self, path):
        return None

    def set_mode(self, path, mode):
        pass

    def delete_tree(self, path):
        for e in self.listdir(path):
            p = self.join(path, e.name)
            if e.is_dir:
                self.delete_tree(p)
            else:
                self.remove_file(p)
        self.remove_dir(path)

    def walk_files(self, path, rel=""):
        """Все файлы под path: (удалённый путь, относительный путь)."""
        for e in self.listdir(path):
            p = self.join(path, e.name)
            r = f"{rel}/{e.name}" if rel else e.name
            if e.is_dir:
                yield from self.walk_files(p, r)
            else:
                yield p, r


class LocalFs(BaseFs):
    def join(self, a, *parts):
        return os.path.join(a, *parts)

    def parent(self, p):
        return os.path.dirname(os.path.normpath(p)) or p

    def base(self, p):
        return os.path.basename(os.path.normpath(p))

    def within(self, root, p):
        r = os.path.normcase(os.path.abspath(root))
        q = os.path.normcase(os.path.abspath(p))
        return q == r or q.startswith(r.rstrip(os.sep) + os.sep)

    def listdir(self, path):
        d = Path(path)
        if not d.is_dir():
            raise FileNotFoundError(f"Папка не найдена: {d}")
        out = []
        for p in d.iterdir():
            try:
                st = p.stat()
            except OSError:
                continue
            is_dir = p.is_dir() and not p.is_symlink()      # ссылку на папку не раскрываем: удаление не должно выйти за корень
            out.append(Entry(p.name, is_dir, 0 if is_dir else st.st_size, st.st_mtime))
        return out

    def exists(self, path):
        return Path(path).exists()

    def download(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote, local)

    def upload(self, local, remote):
        shutil.copy2(local, remote)

    def remove_file(self, path):
        try:
            os.remove(path)
        except OSError:
            if os.path.islink(path) or getattr(os.path, "isjunction", lambda _p: False)(path):
                os.rmdir(path)                              # ссылка на папку в Windows удаляется как папка
            else:
                raise

    def remove_dir(self, path):
        os.rmdir(path)

    def rename(self, src, dst):
        os.rename(src, dst)

    def replace(self, src, dst):
        os.replace(src, dst)

    def mkdir(self, path):
        os.mkdir(path)

    def get_mode(self, path):
        try:
            return stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            return None

    def set_mode(self, path, mode):
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _mdtm(text):
    import calendar
    import time
    try:
        return calendar.timegm(time.strptime(str(text)[:14], "%Y%m%d%H%M%S"))
    except (ValueError, TypeError):
        return 0


class FtpFs(BaseFs):
    def __init__(self, host, port=21, user="", password="", tls=False, timeout=20):
        self.ftp = (ftplib.FTP_TLS if tls else ftplib.FTP)(timeout=timeout)
        self.ftp.connect(host, int(port or 21))
        self.ftp.login(user or "anonymous", password or "")
        if tls:
            self.ftp.prot_p()
        self.ftp.set_pasv(True)

    def close(self):
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass

    def listdir(self, path):
        f = self.ftp
        try:
            out = []
            for name, facts in f.mlsd(path, facts=["type", "size", "modify"]):
                t = facts.get("type", "file")
                if t in ("cdir", "pdir") or name in (".", ".."):
                    continue
                if safe_name(name):
                    out.append(Entry(name, t == "dir", facts.get("size") or 0, _mdtm(facts.get("modify", ""))))
            return out
        except ftplib.error_perm as e:
            if not str(e).startswith(("500", "501", "502", "504")):
                raise                                    # реальная ошибка (нет папки, нет прав), а не «нет MLSD»
        f.voidcmd("TYPE I")                              # сервер не знает MLSD — старый способ
        out = []
        for raw in f.nlst(path):
            name = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if not safe_name(name):
                continue
            full = self.join(path, name)
            is_dir = False
            here = f.pwd()
            try:
                f.cwd(full)
                is_dir = True
            except ftplib.all_errors:
                pass
            finally:
                try:
                    f.cwd(here)
                except ftplib.all_errors:
                    pass
            size = mt = 0
            if not is_dir:
                try:
                    size = f.size(full) or 0
                except ftplib.all_errors:
                    pass
                try:
                    mt = _mdtm(f.sendcmd("MDTM " + full).split()[-1])
                except ftplib.all_errors:
                    pass
            out.append(Entry(name, is_dir, size, mt))
        return out

    def download(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(local) + ".part")
        with open(tmp, "wb") as fh:
            self.ftp.retrbinary("RETR " + remote, fh.write)
        tmp.replace(local)

    def upload(self, local, remote):
        with open(local, "rb") as fh:
            self.ftp.storbinary("STOR " + remote, fh)

    def remove_file(self, path):
        self.ftp.delete(path)

    def remove_dir(self, path):
        self.ftp.rmd(path)

    def rename(self, src, dst):
        self.ftp.rename(src, dst)

    def mkdir(self, path):
        self.ftp.mkd(path)


def fingerprint(key):
    """Отпечаток ключа сервера в виде SHA256:… (как показывает ssh)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class SftpFs(BaseFs):
    """SFTP по SSH. Ключ сервера запоминается при первом подключении; если потом изменится — подключение отклоняется."""

    def __init__(self, host, port=22, user="", password="", key_file="", known_fp="", timeout=20):
        import paramiko                                      # нужен только для SFTP, поэтому импорт здесь
        self._pm = paramiko
        outer = self

        class Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = fingerprint(key)
                outer.host_fp = fp
                if known_fp and known_fp != fp:
                    raise paramiko.SSHException(
                        f"Ключ сервера изменился (был {known_fp}, стал {fp}). Если вы не меняли и не переустанавливали "
                        "сервер, это может быть подмена: подключение отклонено.")

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(Policy())
        kw = dict(hostname=host, port=int(port or 22), username=user or None, timeout=timeout,
                  banner_timeout=timeout, auth_timeout=timeout, allow_agent=False, look_for_keys=False)
        if key_file:
            kw["key_filename"] = key_file
            kw["passphrase"] = password or None              # при ключе поле «пароль» — это пароль к ключу
        else:
            kw["password"] = password or None
        self.client.connect(**kw)
        self.sftp = self.client.open_sftp()

    def close(self):
        try:
            self.sftp.close()
            self.client.close()
        except Exception:
            pass

    def listdir(self, path):
        return [Entry(a.filename, stat.S_ISDIR(a.st_mode or 0), a.st_size or 0, a.st_mtime or 0)
                for a in self.sftp.listdir_attr(path) if safe_name(a.filename)]

    def exists(self, path):
        try:
            self.sftp.stat(path)
            return True
        except OSError:
            return False

    def download(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(local) + ".part")
        self.sftp.get(remote, str(tmp))
        tmp.replace(local)

    def upload(self, local, remote):
        self.sftp.put(str(local), remote)

    def remove_file(self, path):
        self.sftp.remove(path)

    def remove_dir(self, path):
        self.sftp.rmdir(path)

    def rename(self, src, dst):
        self.sftp.rename(src, dst)

    def replace(self, src, dst):
        try:
            self.sftp.posix_rename(src, dst)                # атомарно затирает существующий файл
        except (IOError, AttributeError):
            super().replace(src, dst)

    def mkdir(self, path):
        self.sftp.mkdir(path)

    def get_mode(self, path):
        try:
            return stat.S_IMODE(self.sftp.stat(path).st_mode)
        except OSError:
            return None

    def set_mode(self, path, mode):
        try:
            self.sftp.chmod(path, mode)
        except OSError:
            pass


_SMB_OK = set()                       # (общая папка, логин): уже подключались в этом запуске

_WNET_ERRORS = {5: "доступ запрещён", 53: "сервер не найден в сети", 67: "общая папка с таким именем не найдена",
                86: "неверный пароль", 1326: "неверный логин или пароль", 1219: None, 85: None}


def unc_share(path):
    """\\\\сервер\\папка\\что-то -> \\\\сервер\\папка. None, если это не сетевой путь; ValueError, если нет имени папки."""
    p = (path or "").strip().replace("/", "\\")
    if not p.startswith("\\\\"):
        return None
    parts = [x for x in p[2:].split("\\") if x]
    if len(parts) < 2:
        raise ValueError("Укажите сетевую папку целиком: \\\\сервер\\общая_папка (одного имени сервера мало).")
    return "\\\\" + parts[0] + "\\" + parts[1]


def _wnet_connect(share, user, password):
    """Подключение к общей папке под указанной учётной записью (Windows). Пароль на диск не записывается."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class NetResource(ctypes.Structure):
        _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwDisplayType", wintypes.DWORD),
                    ("dwUsage", wintypes.DWORD), ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                    ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]

    nr = NetResource()
    nr.dwType = 1                                   # RESOURCETYPE_DISK
    nr.lpRemoteName = share
    fn = ctypes.windll.mpr.WNetAddConnection2W
    fn.argtypes = [ctypes.POINTER(NetResource), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    fn.restype = wintypes.DWORD
    rc = fn(ctypes.byref(nr), password or None, user or None, 4)      # 4 = CONNECT_TEMPORARY: не запоминать после выхода
    if rc and rc in _WNET_ERRORS and _WNET_ERRORS[rc] is None:
        return                                      # уже есть подключение к этому серверу — пробуем с ним
    if rc:
        raise OSError(f"Сетевая папка {share}: {_WNET_ERRORS.get(rc, 'ошибка Windows ' + str(rc))}.")


def make_smb(cfg):
    """Сетевая папка Windows/Samba (\\\\сервер\\папка): Windows сам ходит по SMB, мы работаем как с обычной папкой."""
    share = unc_share(cfg.get("srv_root") or cfg.get("srv_dir"))
    user, pw = (cfg.get("ftp_user") or "").strip(), cfg.get("ftp_pass") or ""
    if share and user and (share, user) not in _SMB_OK:
        _wnet_connect(share, user, pw)
        _SMB_OK.add((share, user))
    return LocalFs()


def make_backend(cfg):
    """Подключение к папке сервера по настройкам профиля. Использовать как «with make_backend(cfg) as fs:»."""
    p = proto_of(cfg)
    if p == "smb":
        return make_smb(cfg)
    if p == "ftp":
        if not cfg.get("ftp_host"):
            raise ValueError("Укажите адрес FTP-сервера.")
        return FtpFs(cfg["ftp_host"], cfg.get("ftp_port") or 21, cfg.get("ftp_user"), cfg.get("ftp_pass"),
                     bool(cfg.get("ftp_tls")))
    if p == "sftp":
        if not cfg.get("ftp_host"):
            raise ValueError("Укажите адрес SSH-сервера.")
        return SftpFs(cfg["ftp_host"], cfg.get("ftp_port") or 22, cfg.get("ftp_user"), cfg.get("ftp_pass"),
                      cfg.get("ssh_key") or "", cfg.get("ssh_hostkey") or "")
    return LocalFs()
