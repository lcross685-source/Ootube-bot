"""YouTube Data API client.

Handles OAuth, resumable upload, scheduled publication and the synthetic-media
disclosure. Two details are load-bearing:

* ``status.publishAt`` with ``privacyStatus: private`` is what makes the
  channel's public cadence independent of when the bot actually runs.
* ``status.containsSyntheticMedia`` is the API equivalent of the "altered or
  synthetic content" toggle in YouTube Studio. Setting it is both a policy
  requirement for realistic synthetic content and cheap insurance for a
  channel whose narration is AI-generated.
"""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import PublishPlan, PublishResult, VideoAsset

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

# Transient conditions worth retrying; anything else is a real error.
RETRIABLE_STATUS = {500, 502, 503, 504}


class YouTubeError(RuntimeError):
    pass


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class YouTubeClient:
    """Thin wrapper over ``googleapiclient`` with dry-run support."""

    def __init__(
        self,
        *,
        client_secrets: str = "",
        token_file: str = "",
        dry_run: bool = False,
    ):
        self.dry_run = dry_run
        self.client_secrets = client_secrets or os.environ.get(
            "YOUTUBE_CLIENT_SECRETS", "client_secret.json"
        )
        self.token_file = token_file or os.environ.get(
            "YOUTUBE_TOKEN_FILE", "token.json"
        )
        self._service: Any = None

    # ------------------------------------------------------------------
    def _credentials(self) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = None
        # CI supplies the refresh token through the environment rather than a
        # file, so a scheduled run needs no interactive consent.
        refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
        client_id = os.environ.get("YOUTUBE_CLIENT_ID")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
        if refresh_token and client_id and client_secret:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=SCOPES,
            )
            creds.refresh(Request())
            return creds

        if Path(self.token_file).exists():
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(self.token_file).write_text(creds.to_json())
            return creds

        if not Path(self.client_secrets).exists():
            raise YouTubeError(
                "No usable credentials. Set YOUTUBE_REFRESH_TOKEN / "
                "YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET, or run "
                "`ootube auth` once locally to create a token file."
            )
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets, SCOPES)
        creds = flow.run_local_server(port=0)
        Path(self.token_file).write_text(creds.to_json())
        return creds

    def service(self) -> Any:
        if self._service is not None:
            return self._service
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise YouTubeError(
                "google-api-python-client missing. "
                "Install with: pip install 'ootube[publish]'"
            ) from exc
        self._service = build("youtube", "v3", credentials=self._credentials())
        return self._service

    # ------------------------------------------------------------------
    def _body(self, plan: PublishPlan) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": plan.privacy_status,
            "selfDeclaredMadeForKids": plan.made_for_kids,
            # The API-side equivalent of the "altered or synthetic content"
            # toggle in YouTube Studio.
            "containsSyntheticMedia": plan.contains_synthetic_media,
        }
        # publishAt is only meaningful on a private video, and YouTube rejects
        # a time in the past.
        if plan.privacy_status == "private" and plan.publish_at:
            status["publishAt"] = _rfc3339(plan.publish_at)

        return {
            "snippet": {
                "title": plan.title,
                "description": plan.description,
                "tags": plan.tags,
                "categoryId": plan.category_id,
                "defaultLanguage": plan.language,
                "defaultAudioLanguage": plan.language,
            },
            "status": status,
        }

    # ------------------------------------------------------------------
    def upload(self, asset: VideoAsset, plan: PublishPlan) -> PublishResult:
        if self.dry_run:
            log.info("[dry-run] would upload %s as %r", asset.video_path, plan.title)
            return PublishResult(
                topic_key=plan.topic_key,
                video_id="dry-run",
                url="https://youtube.com/watch?v=dry-run",
                scheduled_for=plan.publish_at,
                dry_run=True,
            )

        if plan.publish_at and plan.publish_at <= datetime.now(timezone.utc):
            raise YouTubeError(
                f"publish_at {plan.publish_at.isoformat()} is in the past; "
                "YouTube rejects backdated scheduling"
            )
        if not Path(asset.video_path).exists():
            raise YouTubeError(f"video file missing: {asset.video_path}")

        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            asset.video_path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/*"
        )
        request = self.service().videos().insert(
            part="snippet,status", body=self._body(plan), media_body=media
        )

        response = None
        attempt = 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except HttpError as exc:
                if exc.resp.status in RETRIABLE_STATUS and attempt < 5:
                    attempt += 1
                    sleep_for = (2 ** attempt) + random.random()
                    log.warning(
                        "upload chunk failed (%s); retry %d in %.1fs",
                        exc.resp.status, attempt, sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise YouTubeError(f"upload failed: {exc}") from exc
            except (OSError, ConnectionError) as exc:
                if attempt < 5:
                    attempt += 1
                    time.sleep((2 ** attempt) + random.random())
                    continue
                raise YouTubeError(f"upload failed: {exc}") from exc

        video_id = response.get("id", "")
        if not video_id:
            raise YouTubeError(f"upload returned no video id: {response}")

        log.info("uploaded %s, scheduled for %s", video_id, plan.publish_at.isoformat())
        return PublishResult(
            topic_key=plan.topic_key,
            video_id=video_id,
            url=f"https://youtube.com/watch?v={video_id}",
            scheduled_for=plan.publish_at,
        )

    # ------------------------------------------------------------------
    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> bool:
        if self.dry_run:
            log.info("[dry-run] would set thumbnail %s", thumbnail_path)
            return True
        if not thumbnail_path or not Path(thumbnail_path).exists():
            return False
        # Custom thumbnails require a verified channel; failing here must not
        # undo an otherwise successful upload.
        try:
            from googleapiclient.http import MediaFileUpload

            self.service().thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("thumbnail upload failed for %s: %s", video_id, exc)
            return False

    def add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        if self.dry_run:
            log.info("[dry-run] would add %s to playlist %s", video_id, playlist_id)
            return True
        if not playlist_id:
            return False
        try:
            self.service().playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("playlist insert failed for %s: %s", video_id, exc)
            return False
