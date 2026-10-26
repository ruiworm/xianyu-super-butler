"""Distribute only the reviewed desktop connector sources, never local settings."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response


BRIDGE_SOURCE_DIR = Path(__file__).resolve().parent.parent / 'desktop_bridge'
PACKAGE_FILES = ('bridge.py', 'app.py', 'queries.py', 'protocol.py', 'start.bat', 'requirements.txt', 'README.md')


def build_desktop_bridge_archive(source_dir: Path = BRIDGE_SOURCE_DIR) -> bytes:
    """Use an explicit allowlist so .venv, credentials and logs cannot be shipped."""
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name in PACKAGE_FILES:
            path = source_dir / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Desktop connector source is incomplete')
            content = path.read_bytes()
            if name.endswith('.bat'):
                content = content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            info = ZipInfo(f'XianyuDesktopBridge/{name}', date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def create_desktop_bridge_download_router() -> APIRouter:
    router = APIRouter()

    @router.get('/desktop-bridge/download')
    def download_desktop_bridge():
        try:
            archive = build_desktop_bridge_archive()
        except (OSError, ValueError):
            raise HTTPException(status_code=503, detail='连接工具文件不完整，请更新服务器代码或镜像') from None
        return Response(
            archive,
            media_type='application/zip',
            headers={
                'Content-Disposition': 'attachment; filename="xianyu-desktop-bridge.zip"',
                'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff',
            },
        )

    return router
