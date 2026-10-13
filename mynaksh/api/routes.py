from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from mynaksh.astrology import astrology_attributes, sun_sign
from mynaksh.brain.base import BrainError
from mynaksh.models import ChatRequest, ChatResponse, Memory, MemoryStatus, UserProfile, UserProfileIn, utcnow

router = APIRouter()


def _svc(request: Request):
    return request.app.state.chat_service


@router.get("/health")
def health(request: Request):
    svc = _svc(request)
    try:
        svc.brain.ping()
        brain = "ok"
    except BrainError:
        brain = "unavailable"
    body = {"status": "ok" if brain == "ok" else "degraded", "brain": brain, "llm": svc.llm.name}
    return JSONResponse(body, status_code=200 if brain == "ok" else 503)


@router.post("/users", response_model=UserProfile, status_code=201)
def upsert_user(payload: UserProfileIn, request: Request):
    brain = _svc(request).brain
    try:
        current = brain.get_profile(payload.user_id) or UserProfile(user_id=payload.user_id)
        data = current.model_dump()
        data.update(payload.model_dump(exclude_unset=True, exclude_none=True))
        if data.get("date_of_birth"):
            data["zodiac_sign"] = sun_sign(data["date_of_birth"])
        data["updated_at"] = utcnow()
        profile = UserProfile.model_validate(data)
        brain.save_profile(profile)
    except BrainError:
        raise HTTPException(503, detail="shared brain unavailable") from None
    return profile


@router.get("/users/{user_id}", response_model=UserProfile)
def get_user(user_id: str, request: Request):
    try:
        profile = _svc(request).brain.get_profile(user_id)
    except BrainError:
        raise HTTPException(503, detail="shared brain unavailable") from None
    if profile is None:
        raise HTTPException(404, detail="user not found")
    return profile


@router.get("/users/{user_id}/memories", response_model=list[Memory])
def get_memories(user_id: str, request: Request, include_history: bool = Query(False)):
    try:
        status = None if include_history else MemoryStatus.ACTIVE
        return _svc(request).brain.list_memories(user_id, status=status)
    except BrainError:
        raise HTTPException(503, detail="shared brain unavailable") from None


@router.get("/users/{user_id}/brain")
def get_brain(user_id: str, request: Request):
    """Everything the Shared Brain holds about a user, grouped for inspection."""
    brain = _svc(request).brain
    try:
        profile = brain.get_profile(user_id)
        if profile is None:
            raise HTTPException(404, detail="user not found")
        memories = brain.list_memories(user_id)
        focus = brain.get_life_area_focus(user_id, limit=10)
    except BrainError:
        raise HTTPException(503, detail="shared brain unavailable") from None
    grouped: dict[str, list[dict]] = {}
    for m in memories:
        grouped.setdefault(m.type.value, []).append(
            {"memory_id": m.memory_id, "key": m.key, "value": m.render(), "life_area": m.life_area,
             "confidence": m.confidence, "mentions": m.mention_count})
    return {
        "profile": profile.model_dump(mode="json"),
        "astrology": astrology_attributes(profile.zodiac_sign),
        "life_area_focus": [f.model_dump(mode="json") | {"importance": f.importance} for f in focus],
        "memories": grouped,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request):
    return _svc(request).chat(payload)
