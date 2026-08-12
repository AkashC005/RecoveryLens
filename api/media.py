"""
RecoveryLens API — media.py
===========================
Serving generated audio to Twilio.

Why this route is public, and why that is uncomfortable
------------------------------------------------------
To attach audio to a WhatsApp message, Twilio fetches a URL itself. It cannot
hold a session cookie and cannot be given one. So there has to be one route that
answers without authentication — a deliberate hole in the boundary built in
`auth.py`, opened one week after closing it.

Rather than pretend that is fine, it is made as narrow as a hole can be:

  1. THE TOKEN IS THE CREDENTIAL. 256 bits of `secrets.token_urlsafe`. There is no
     enumerable id in the URL and no listing route, so there is nothing to walk.
  2. IT EXPIRES IN MINUTES, NOT DAYS. Twilio fetches within seconds of the send.
     A window measured in days exists only for the convenience of an attacker.
  3. THE AUDIO NAMES NOBODY. `spoken_text()` strips the patient reference before
     synthesis, so a leaked URL yields generic post-stroke guidance rather than
     guidance about an identifiable person. This is the mitigation that still
     works after the other two have failed.
  4. ONE ASSET PER TOKEN. Holding a token for one message grants nothing about
     any other message, patient, or check-in.
  5. NOTHING IS CACHED. `Cache-Control: no-store` and `X-Robots-Tag: noindex`, so
     the audio does not settle into a CDN or a search index.

An unknown token, an expired token and a purged token all return the same 404.
Distinguishing them would say whether a URL was ever valid, which is the one
thing a probe wants to learn.

What is NOT solved here
-----------------------
The URL must be reachable from the internet, so on a laptop this needs ngrok
exactly as the webhook does, and `RECOVERYLENS_PUBLIC_URL` must match. If it is
unset the send still goes out as text — audio is an enhancement, and a missing
base URL must not stop a check-in reaching a family.
"""

from __future__ import annotations

from datetime import timedelta
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .database import CheckIn, MediaAsset, get_db, utcnow

router = APIRouter(tags=["media"])

# Twilio fetches the media within seconds of accepting the message; WhatsApp may
# re-fetch shortly after. Fifteen minutes covers a slow retry and nothing else.
MEDIA_TTL = timedelta(minutes=15)

# Refuse to synthesise anything longer than this. TTS cost scales with length, and
# a four-minute voice note is not something a worried carer will listen to.
MAX_SPOKEN_CHARS = 900


def public_base_url() -> str:
    """Where Twilio can reach us. Empty means audio is skipped, not faked."""
    return os.getenv("RECOVERYLENS_PUBLIC_URL", "").strip().rstrip("/")


def spoken_text(body: str) -> str:
    """The message, prepared for speech and stripped of anything identifying.

    Three transformations, each for a different reason:

      - The patient reference is REMOVED. It is the one identifier in the message,
        and this audio is served from an unauthenticated URL. Text goes to a
        consented number; audio goes to whoever holds the link.
      - "Reply STOP" and the numbered list are removed. Read aloud they are
        nonsense — you cannot reply STOP to audio, and "1. 2. 3." spoken as digits
        tells the listener nothing about what to do.
      - The remainder is trimmed to MAX_SPOKEN_CHARS at a sentence boundary.

    The text message still carries all of it. This is the spoken companion, not a
    replacement.
    """
    text = re.sub(r"RecoveryLens check-in for [^\s—]+", "RecoveryLens check-in",
                  body)
    text = re.sub(r"^\s*\d+\.\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"Reply STOP.*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"Reply in your own words.*$", "", text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"\n{2,}", "\n", text).strip()

    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        stop = max(cut.rfind("."), cut.rfind("?"), cut.rfind("!"))
        text = cut[:stop + 1] if stop > MAX_SPOKEN_CHARS // 2 else cut
    return text


def store_audio(db: Session, data: bytes, mime_type: str,
                check_in_id: int | None = None,
                language: str = "en") -> MediaAsset:
    asset = MediaAsset(
        token=secrets.token_urlsafe(32), check_in_id=check_in_id,
        mime_type=mime_type or "audio/ogg", data=data, language=language,
        expires_at=utcnow() + MEDIA_TTL)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def synthesise_for(db: Session, checkin: CheckIn, body: str,
                   language: str = "en") -> tuple[str | None, dict]:
    """Turn a composed message into a fetchable audio URL.

    Returns (url or None, a record of what happened). Every failure path returns
    None and a stated reason rather than raising: the text message is the product,
    and audio failing must never stop it going out.
    """
    from voice import build_speech_provider, voice_enabled

    base = public_base_url()
    if not base:
        return None, {"attempted": False,
                      "reason": "RECOVERYLENS_PUBLIC_URL is not set, so Twilio "
                                "has no address to fetch audio from"}
    if not voice_enabled():
        return None, {"attempted": False,
                      "reason": "voice is not configured (RECOVERYLENS_VOICE)"}

    text = spoken_text(body)
    if not text:
        return None, {"attempted": False, "reason": "nothing left to speak"}

    audio = build_speech_provider().synthesise(text, language=language)
    if not audio.ok:
        return None, {"attempted": True, "provider": audio.provider,
                      "reason": audio.error or "synthesis produced no audio"}

    asset = store_audio(db, audio.data, audio.mime_type,
                        check_in_id=checkin.id, language=language)
    return f"{base}/media/{asset.token}", {
        "attempted": True, "provider": audio.provider, "language": language,
        "bytes": len(audio.data),
        "expires_at": asset.expires_at.isoformat(),
        # The spoken text is recorded so a clinician can see what was said aloud
        # without playing it, and can confirm the patient reference was stripped.
        "spoken_text": text,
    }


def purge_expired(db: Session) -> int:
    """Delete expired audio. Called by the scheduler.

    Expiry alone stops a URL working; this stops the bytes existing. For audio
    describing a stroke patient's care, "unreachable" and "deleted" are not the
    same guarantee.
    """
    removed = (db.query(MediaAsset)
               .filter(MediaAsset.expires_at < utcnow())
               .delete(synchronize_session=False))
    if removed:
        db.commit()
    return removed


@router.get("/media/{token}")
def get_media(token: str, db: Session = Depends(get_db)):
    """Serve one generated audio file. No session — see the module docstring.

    Unknown, expired and purged tokens are indistinguishable: all 404 with the
    same body. Telling them apart would reveal whether a URL was ever valid.
    """
    asset = db.query(MediaAsset).filter(MediaAsset.token == token).first()

    expires = asset.expires_at if asset else None
    if expires is not None and expires.tzinfo is None:
        from datetime import timezone
        expires = expires.replace(tzinfo=timezone.utc)

    if asset is None or expires is None or expires < utcnow():
        raise HTTPException(404, "Not found.")

    asset.fetch_count = (asset.fetch_count or 0) + 1
    db.commit()

    return Response(
        content=asset.data,
        media_type=asset.mime_type or "audio/ogg",
        headers={
            # Clinical audio must not settle into a CDN or a proxy cache.
            "Cache-Control": "no-store, private",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Content-Type-Options": "nosniff",
            # `inline` with a generic name: nothing in the filename identifies a
            # patient, and nothing invites a browser to execute it.
            "Content-Disposition": 'inline; filename="checkin.ogg"',
        },
    )
