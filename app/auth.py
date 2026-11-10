# 비밀번호 해시와 JWT.
#
# - 비밀번호: Argon2id. 해시할 때마다 무작위 salt를 만들어 해시 문자열 안에 함께 저장한다
#   ("$argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>"). 원문 비밀번호는 저장하지 않는다.
# - JWT: HS256, sub = user.id (서비스 내부 식별자), 만료 JWT_EXPIRE_MINUTES분.
# - current_user_id: 로그인이 필요한 엔드포인트에 Depends로 붙인다.

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import JWT_EXPIRE_MINUTES, JWT_SECRET

_hasher = PasswordHasher()

# 없는 아이디로 로그인할 때도 같은 시간이 걸리도록 비교에 쓰는 가짜 해시 (아이디가 있는지 응답 시간으로 알 수 없게)
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    # 맞으면 True. password_hash가 None(없는 사용자)이어도 같은 비용으로 비교한 뒤 False.
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int) -> str:
    if not JWT_SECRET:
        raise RuntimeError(".env에 JWT_SECRET이 없습니다.")
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int:
    # 토큰을 검증하고 user.id를 돌려준다. 만료·위조면 jwt.InvalidTokenError.
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"require": ["sub", "exp"]})
    return int(payload["sub"])


_bearer = HTTPBearer(
    auto_error=False,
    description="POST /login으로 받은 access_token. 헤더 형식: Authorization: Bearer <access_token>",
)


def current_user_id(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> int:
    # 엔드포인트 의존성: "Authorization: Bearer <토큰>" 헤더를 검증하고 user.id를 돌려준다. 실패하면 401.
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 만료되었습니다. 다시 로그인하세요.",
                            headers={"WWW-Authenticate": "Bearer"})
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "유효하지 않은 토큰입니다.", headers={"WWW-Authenticate": "Bearer"})
