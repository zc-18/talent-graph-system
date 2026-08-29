"""个人资料自助维护（昵称 / 头像）的路由回归。

覆盖三件事：新字段是否真的吐给前端、头像路径白名单能不能挡住外链、
以及上传接口是不是只信文件头而不信扩展名。
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta

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

# 真实的 1x1 PNG（不是只拼个魔数），确保魔数判定走的是真图片
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF" + (64).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 56


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
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "static"))
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
    {"avatar_url": "https://evil.example.com/a.png"},   # 外部链接
    {"avatar_url": "//evil.example.com/a.png"},         # 协议相对外链
    {"avatar_url": "/avatars/../../etc/passwd"},        # 路径穿越
    {"avatar_url": "/avatars/a13.webp"},                # 不在预置图库内
    {"avatar_url": "/static/anything.png"},             # 站内但不是头像目录
    {"avatar_url": "/avatars/u1-notahash.png"},         # 冒充上传产物
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
    written = tmp_path / "static" / "avatars" / body["avatar_url"].rsplit("/", 1)[-1]
    assert written.is_file() and written.read_bytes() == content
    session.refresh(user)
    assert user.avatar_url == body["avatar_url"]


def test_uploaded_avatar_url_passes_the_patch_validator(client, session):
    """上传产出的路径必须能被 PATCH 的白名单接受，否则换头像会自相矛盾。"""
    _, headers = make_user(session, suffix="roundtrip")
    uploaded = _upload(client, headers, "me.png", PNG_BYTES).json()["avatar_url"]
    client.patch("/api/me/profile", headers=headers, json={"avatar_url": "/avatars/a01.webp"})
    response = client.patch("/api/me/profile", headers=headers, json={"avatar_url": uploaded})
    assert response.status_code == 200
    assert response.json()["avatar_url"] == uploaded


def test_avatar_upload_is_content_addressed_and_idempotent(client, session, tmp_path):
    _, headers = make_user(session, suffix="hash")
    first = _upload(client, headers, "a.png", PNG_BYTES).json()["avatar_url"]
    second = _upload(client, headers, "b.png", PNG_BYTES).json()["avatar_url"]
    assert first == second
    assert len(list((tmp_path / "static" / "avatars").iterdir())) == 1


def test_avatar_upload_rejects_extension_lies(client, session, tmp_path):
    """扩展名是白名单里的，内容不是图片 —— 必须被文件头拦下。"""
    _, headers = make_user(session, suffix="lie")
    response = _upload(client, headers, "payload.png", b"<?php system($_GET['c']); ?>")
    assert response.status_code == 422
    assert not (tmp_path / "static" / "avatars").exists()


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
    assert not (tmp_path / "static" / "avatars").exists()


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
