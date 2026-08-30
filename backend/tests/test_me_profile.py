"""个人资料自助维护（昵称 / 头像）的路由回归。

覆盖三件事：新字段是否真的吐给前端、头像路径白名单能不能挡住外链、
以及上传接口是不是只信文件头而不信扩展名。
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import io

from PIL import Image

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import guards, models
from app.auth import token_hash
from app.db import Base, get_db
from app.main import app
from app.schemas import AVATAR_PRESETS

# 真实的 1x1 PNG；JPEG / WebP 同样由 Pillow 编码，保证完整解码路径可用。
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _image_bytes(format_name: str, *, size: tuple[int, int] = (1, 1)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (39, 86, 142)).save(output, format=format_name)
    return output.getvalue()


JPEG_BYTES = _image_bytes("JPEG")
WEBP_BYTES = _image_bytes("WEBP")


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine)()
    try:
        yield value
    finally:
        value.close()


@pytest.fixture()
def client(monkeypatch, session, tmp_path):
    # READ_ONLY 打开：个人资料属于用户私有数据，不受知识图谱写闸限制
    monkeypatch.setattr(guards.settings, "read_only", True, raising=False)
    static_dir = tmp_path / "static"
    preset_dir = static_dir / "avatars"
    preset_dir.mkdir(parents=True)
    for preset in AVATAR_PRESETS:
        (preset_dir / preset.rsplit("/", 1)[-1]).write_bytes(WEBP_BYTES)
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("AVATAR_UPLOAD_DIR", str(tmp_path / "user_uploads" / "avatars"))
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def make_user(session, *, role: str = "user", suffix: str = "01"):
    user = models.AppUser(username=f"profile-{role}-{suffix}", password_hash="unused",
                          role=role, status="active")
    session.add(user)
    session.flush()
    raw = f"profile-token-{role}-{suffix}"
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash(raw),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, {"Authorization": f"Bearer {raw}"}


# ----------------------------- 字段可见性 -----------------------------

def test_auth_me_exposes_profile_fields(client, session):
    user, headers = make_user(session)
    body = client.get("/api/auth/me", headers=headers).json()
    # 未设置昵称时用 username 兜底，前端永远拿得到可渲染的值
    assert body["nickname"] == user.username
    assert body["avatar_url"] is None

    user.nickname = "林知遥"
    user.avatar_url = AVATAR_PRESETS[0]
    session.commit()
    body = client.get("/api/auth/me", headers=headers).json()
    assert body["nickname"] == "林知遥"
    assert body["avatar_url"] == AVATAR_PRESETS[0]


def test_avatar_presets_listing(client, session):
    _, headers = make_user(session, suffix="presets")
    body = client.get("/api/me/avatar-presets", headers=headers).json()
    assert body["total"] == 12 and len(body["items"]) == 12
    assert body["items"][0] == "/avatars/a01.webp"
    assert body["items"][-1] == "/avatars/a12.webp"
    assert body["max_upload_bytes"] == 2 * 1024 * 1024


# ----------------------------- PATCH /api/me/profile -----------------------------

def test_patch_profile_updates_nickname_and_preset_avatar(client, session):
    user, headers = make_user(session, suffix="patch")
    response = client.patch("/api/me/profile", headers=headers,
                            json={"nickname": "  周砚清  ", "avatar_url": "/avatars/a04.webp"})
    assert response.status_code == 200
    body = response.json()
    assert body["nickname"] == "周砚清"          # 首尾空白被去掉
    assert body["avatar_url"] == "/avatars/a04.webp"
    session.refresh(user)
    assert user.nickname == "周砚清" and user.avatar_url == "/avatars/a04.webp"


def test_patch_profile_allows_partial_update(client, session):
    user, headers = make_user(session, suffix="partial")
    client.patch("/api/me/profile", headers=headers, json={"avatar_url": "/avatars/a02.webp"})
    client.patch("/api/me/profile", headers=headers, json={"nickname": "沈叙白"})
    session.refresh(user)
    assert user.nickname == "沈叙白"
    assert user.avatar_url == "/avatars/a02.webp"   # 只改昵称不会清掉头像


@pytest.mark.parametrize("payload", [
    {},                                             # 一个字段都没给
    {"nickname": "   "},                            # 纯空白
    {"nickname": "x" * 65},                         # 超长
    {"nickname": "换\n行"},                          # 控制字符
    {"nickname": "退\x1b格"},                         # 其他 C0 控制字符
    {"nickname": "隐\x85藏"},                         # C1 控制字符
    {"avatar_url": "https://evil.example.com/a.png"},   # 外部链接
    {"avatar_url": "//evil.example.com/a.png"},         # 协议相对外链
    {"avatar_url": "/avatars/../../etc/passwd"},        # 路径穿越
    {"avatar_url": "/avatars/a13.webp"},                # 不在预置图库内
    {"avatar_url": "/static/anything.png"},             # 站内但不是头像目录
    {"avatar_url": "/avatars/u1-notahash.png"},         # 冒充上传产物
    {"avatar_url": "/avatars/u1-0123456789abcdef.jpeg"}, # 非规范上传扩展名
    {"avatar_url": "/avatars//a01.webp"},                # 双斜杠
    {"avatar_url": "/avatars/%2e%2e/a01.webp"},          # 编码穿越
    {"avatar_url": "/avatars/a01.webp?x=.png"},          # query
    {"avatar_url": "/avatars/a01.webp#x"},               # fragment
    {"avatar_url": "/avatars／a01.webp"},                # Unicode 全角斜杠
])
def test_patch_profile_rejects_bad_input(client, session, payload):
    user, headers = make_user(session, suffix=f"bad{abs(hash(str(payload))) % 1000}")
    assert client.patch("/api/me/profile", headers=headers, json=payload).status_code == 422
    session.refresh(user)
    assert user.nickname is None and user.avatar_url is None


def test_patch_profile_requires_authentication(client, session):
    make_user(session, suffix="anon")
    assert client.patch("/api/me/profile", json={"nickname": "x"}).status_code == 401


def test_patch_profile_works_for_hr_and_admin(client, session):
    for role in ("hr", "admin"):
        user, headers = make_user(session, role=role, suffix="roles")
        response = client.patch("/api/me/profile", headers=headers,
                                json={"nickname": f"{role}-昵称"})
        assert response.status_code == 200
        session.refresh(user)
        assert user.nickname == f"{role}-昵称"


# ----------------------------- POST /api/me/avatar -----------------------------

def _upload(client, headers, filename, content, content_type="image/png"):
    return client.post("/api/me/avatar", headers=headers,
                       files={"file": (filename, content, content_type)})


@pytest.mark.parametrize("filename,content,expected", [
    ("me.png", PNG_BYTES, "png"),
    ("me.jpg", JPEG_BYTES, "jpg"),
    ("me.jpeg", JPEG_BYTES, "jpg"),
    ("me.webp", WEBP_BYTES, "webp"),
])
def test_avatar_upload_accepts_real_images(client, session, tmp_path,
                                           filename, content, expected):
    user, headers = make_user(session, suffix=f"up{expected}{filename[-4:]}")
    response = _upload(client, headers, filename, content)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == expected and body["size"] == len(content)
    assert body["avatar_url"].startswith(f"/avatars/u{user.id}-")
    assert body["avatar_url"].endswith(f".{expected}")
    assert body["user"]["avatar_url"] == body["avatar_url"]
    written = tmp_path / "user_uploads" / "avatars" / body["avatar_url"].rsplit("/", 1)[-1]
    assert written.is_file() and written.read_bytes() == content
    session.refresh(user)
    assert user.avatar_url == body["avatar_url"]


def test_uploaded_avatar_url_passes_the_patch_validator(client, session):
    """上传产出的路径必须能被 PATCH 的白名单接受，否则换头像会自相矛盾。"""
    _, headers = make_user(session, suffix="roundtrip")
    uploaded = _upload(client, headers, "me.png", PNG_BYTES).json()["avatar_url"]
    response = client.patch("/api/me/profile", headers=headers, json={"avatar_url": uploaded})
    assert response.status_code == 200
    assert response.json()["avatar_url"] == uploaded


def test_patch_cannot_select_another_users_upload(client, session):
    _, headers_a = make_user(session, suffix="owner-a")
    user_b, headers_b = make_user(session, suffix="owner-b")
    foreign_url = _upload(client, headers_b, "me.png", PNG_BYTES).json()["avatar_url"]

    response = client.patch("/api/me/profile", headers=headers_a,
                            json={"avatar_url": foreign_url})
    assert response.status_code == 422
    session.refresh(user_b)
    assert user_b.avatar_url == foreign_url


def test_patch_rejects_missing_owned_upload(client, session):
    user, headers = make_user(session, suffix="missing-upload")
    missing = f"/avatars/u{user.id}-{'0' * 64}.png"
    assert client.patch("/api/me/profile", headers=headers,
                        json={"avatar_url": missing}).status_code == 422


def test_avatar_upload_is_content_addressed_and_idempotent(client, session, tmp_path):
    _, headers = make_user(session, suffix="hash")
    first = _upload(client, headers, "a.png", PNG_BYTES).json()["avatar_url"]
    second = _upload(client, headers, "b.png", PNG_BYTES).json()["avatar_url"]
    assert first == second
    assert len(first.rsplit("-", 1)[-1].split(".", 1)[0]) == 64
    assert len(list((tmp_path / "user_uploads" / "avatars").iterdir())) == 1


def test_failed_commit_keeps_installed_content_addressed_file(client, session, tmp_path,
                                                               monkeypatch):
    """Rollback cleanup must not delete a file an identical concurrent request may reference."""
    _, headers = make_user(session, suffix="commit-race")
    real_commit = session.commit

    def fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        _upload(client, headers, "me.png", PNG_BYTES)
    monkeypatch.setattr(session, "commit", real_commit)

    user = session.query(models.AppUser).filter_by(username="profile-user-commit-race").one()
    expected_name = f"u{user.id}-{hashlib.sha256(PNG_BYTES).hexdigest()}.png"
    assert (tmp_path / "user_uploads" / "avatars" / expected_name).read_bytes() == PNG_BYTES


def test_replacing_upload_removes_old_file_and_bounds_disk(client, session, tmp_path):
    _, headers = make_user(session, suffix="replace")
    first = _upload(client, headers, "first.png", PNG_BYTES).json()["avatar_url"]
    second_bytes = _image_bytes("PNG", size=(2, 1))
    second = _upload(client, headers, "second.png", second_bytes).json()["avatar_url"]
    upload_dir = tmp_path / "user_uploads" / "avatars"
    assert first != second
    assert not (upload_dir / first.rsplit("/", 1)[-1]).exists()
    assert [path.name for path in upload_dir.iterdir()] == [second.rsplit("/", 1)[-1]]


def test_selecting_preset_removes_previous_upload(client, session, tmp_path):
    _, headers = make_user(session, suffix="preset-clean")
    uploaded = _upload(client, headers, "me.png", PNG_BYTES).json()["avatar_url"]
    upload_path = (tmp_path / "user_uploads" / "avatars"
                   / uploaded.rsplit("/", 1)[-1])
    assert upload_path.is_file()
    response = client.patch("/api/me/profile", headers=headers,
                            json={"avatar_url": "/avatars/a01.webp"})
    assert response.status_code == 200
    assert not upload_path.exists()


def test_patch_never_follows_or_deletes_an_owned_name_symlink(client, session, tmp_path):
    user, headers = make_user(session, suffix="symlink")
    upload_dir = tmp_path / "user_uploads" / "avatars"
    upload_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    url = f"/avatars/u{user.id}-{'1' * 64}.png"
    link = upload_dir / url.rsplit("/", 1)[-1]
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不允许创建测试符号链接")
    response = client.patch("/api/me/profile", headers=headers, json={"avatar_url": url})
    assert response.status_code == 422
    assert outside.read_bytes() == PNG_BYTES and link.is_symlink()


def test_avatar_upload_rejects_extension_lies(client, session, tmp_path):
    """扩展名是白名单里的，内容不是图片 —— 必须被完整解码拦下。"""
    _, headers = make_user(session, suffix="lie")
    response = _upload(client, headers, "payload.png", b"<?php system($_GET['c']); ?>")
    assert response.status_code == 422
    upload_dir = tmp_path / "user_uploads" / "avatars"
    assert not upload_dir.exists() or not list(upload_dir.iterdir())


def test_avatar_upload_rejects_magic_only_and_truncated_images(client, session):
    _, headers = make_user(session, suffix="truncated")
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    fake_webp = b"RIFF" + (64).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 56
    assert _upload(client, headers, "fake.jpg", fake_jpeg).status_code == 422
    assert _upload(client, headers, "fake.webp", fake_webp).status_code == 422
    assert _upload(client, headers, "truncated.png", PNG_BYTES[:-10]).status_code == 422


def test_avatar_upload_rejects_pixel_bombs_before_decode(client, session, monkeypatch):
    _, headers = make_user(session, suffix="pixel-cap")
    monkeypatch.setattr("app.routers.me.AVATAR_MAX_PIXELS", 1)
    response = _upload(client, headers, "large.png", _image_bytes("PNG", size=(2, 1)))
    assert response.status_code == 422


def test_avatar_upload_rejects_animated_images(client, session):
    _, headers = make_user(session, suffix="animated")
    output = io.BytesIO()
    frames = [Image.new("RGB", (1, 1), color) for color in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    assert _upload(client, headers, "animated.webp", output.getvalue()).status_code == 422


def test_avatar_upload_rejects_format_mismatch(client, session):
    _, headers = make_user(session, suffix="mismatch")
    response = _upload(client, headers, "actually-png.webp", PNG_BYTES)
    assert response.status_code == 422


@pytest.mark.parametrize("filename", ["shell.exe", "note.txt", "avatar.svg", "noext"])
def test_avatar_upload_rejects_bad_extension(client, session, filename):
    _, headers = make_user(session, suffix=f"ext{len(filename)}")
    assert _upload(client, headers, filename, PNG_BYTES).status_code == 422


def test_avatar_upload_rejects_empty_file(client, session):
    _, headers = make_user(session, suffix="empty")
    assert _upload(client, headers, "empty.png", b"").status_code == 422


def test_avatar_upload_enforces_two_megabyte_cap(client, session, tmp_path):
    _, headers = make_user(session, suffix="big")
    oversized = PNG_BYTES + b"\x00" * (2 * 1024 * 1024 + 1)
    response = _upload(client, headers, "big.png", oversized)
    assert response.status_code == 413
    upload_dir = tmp_path / "user_uploads" / "avatars"
    assert not upload_dir.exists() or not list(upload_dir.iterdir())


def test_uploaded_avatar_is_explicitly_served_outside_frontend_static(client, session,
                                                                      tmp_path):
    _, headers = make_user(session, suffix="serve")
    avatar_url = _upload(client, headers, "me.png", PNG_BYTES).json()["avatar_url"]
    response = client.get(avatar_url)
    assert response.status_code == 200 and response.content == PNG_BYTES
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "sandbox" in response.headers["content-security-policy"]
    assert client.head(avatar_url).status_code == 200
    assert client.post(avatar_url).status_code == 405
    assert not (tmp_path / "static" / "avatars" / avatar_url.rsplit("/", 1)[-1]).exists()


@pytest.mark.parametrize("path", [
    "/avatars/random.txt",
    "/avatars/u1-0123456789abcdef.svg",
    "/avatars/u1-0123456789abcdef.png/extra",
    "/avatars/",
])
def test_avatar_static_mount_only_serves_canonical_upload_names(client, path):
    assert client.get(path).status_code == 404


def test_avatar_static_mount_serves_presets_without_spa_fallback(client):
    # This exercises the FastAPI *main app*, not me.router in isolation.  It proves that
    # APIRouter.mount is included at /avatars before main.py's SPA catch-all.
    response = client.get(AVATAR_PRESETS[0])
    assert response.status_code == 200 and response.content == WEBP_BYTES
    assert response.headers["content-type"].startswith("image/webp")
    assert response.headers["cache-control"] == "public, max-age=3600, must-revalidate"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "sandbox" in response.headers["content-security-policy"]
    assert client.get("/avatars/a13.webp").status_code == 404


def test_avatar_static_mount_rejects_normalized_or_encoded_paths(client):
    paths = (
        "/avatars//a01.webp",
        "/avatars/a/%2e%2e/a01.webp",
        "/avatars/%2e%2e/a01.webp",
        "/avatars/a01.webp%3Fx",
        "/avatars/a01.webp%23x",
        "/avatars/%61%30%31.webp",
        "/avatars%2fa01.webp",
        "/avatars%5ca01.webp",
    )
    for path in paths:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, (path, response.status_code, response.text)


def test_avatar_roots_do_not_share_mutable_staticfiles_state(client, session):
    _, headers = make_user(session, suffix="parallel-static")
    uploaded_url = _upload(client, headers, "me.png", PNG_BYTES).json()["avatar_url"]

    def request(url: str) -> tuple[int, bytes, str]:
        response = client.get(url)
        return response.status_code, response.content, response.headers["content-type"]

    urls = [AVATAR_PRESETS[0], uploaded_url] * 25
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, urls))
    for url, (status, content, content_type) in zip(urls, results):
        assert status == 200
        expected = WEBP_BYTES if url == AVATAR_PRESETS[0] else PNG_BYTES
        assert content == expected
        assert content_type.startswith("image/webp" if url == AVATAR_PRESETS[0] else "image/png")


def test_avatar_upload_requires_authentication(client, session):
    make_user(session, suffix="upanon")
    response = client.post("/api/me/avatar",
                           files={"file": ("me.png", PNG_BYTES, "image/png")})
    assert response.status_code == 401


def test_avatar_paths_are_namespaced_per_user(client, session):
    """同一张图两个人上传，落成两个文件名 —— 头像路径里的 user_id 不可伪造成别人的。"""
    user_a, headers_a = make_user(session, suffix="nsa")
    user_b, headers_b = make_user(session, suffix="nsb")
    url_a = _upload(client, headers_a, "me.png", PNG_BYTES).json()["avatar_url"]
    url_b = _upload(client, headers_b, "me.png", PNG_BYTES).json()["avatar_url"]
    assert url_a != url_b
    assert f"u{user_a.id}-" in url_a and f"u{user_b.id}-" in url_b


def test_profile_endpoints_are_strictly_actor_scoped(client, session):
    user_a, headers_a = make_user(session, suffix="scope-a")
    user_b, headers_b = make_user(session, suffix="scope-b")
    assert client.patch("/api/me/profile", headers=headers_a,
                        json={"nickname": "用户 A"}).status_code == 200
    assert _upload(client, headers_b, "me.png", PNG_BYTES).status_code == 200
    session.refresh(user_a)
    session.refresh(user_b)
    assert user_a.nickname == "用户 A" and user_a.avatar_url is None
    assert user_b.nickname is None and user_b.avatar_url is not None
