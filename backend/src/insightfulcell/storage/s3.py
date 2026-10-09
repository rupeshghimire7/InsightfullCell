"""S3 adapter (AWS S3 or any S3-compatible server). boto3 is synchronous, so network calls
run in a worker thread; presigning is local arithmetic and is called inline."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from insightfulcell.config import Settings
from insightfulcell.domain.errors import StorageUnavailable
from insightfulcell.storage.port import (
    CompletedPart,
    InvalidPartsError,
    MultipartNotFoundError,
    ObjectInfo,
    PartSpec,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

T = TypeVar("T")

_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}
_BAD_PARTS = {"InvalidPart", "InvalidPartOrder", "EntityTooSmall", "MalformedXML"}
# Errors the callers handle themselves; every other storage failure becomes StorageUnavailable.
_EXPECTED = _NOT_FOUND | _BAD_PARTS | {"NoSuchUpload"}


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = self._make_client(settings, settings.s3_endpoint_url)
        public = settings.s3_public_endpoint_url or settings.s3_endpoint_url
        # Presigned URLs embed the host, so they are signed with the browser-facing endpoint.
        self._signer = (
            self._client
            if public == settings.s3_endpoint_url
            else self._make_client(settings, public)
        )

    @staticmethod
    def _make_client(settings: Settings, endpoint_url: str | None) -> S3Client:
        key = settings.s3_access_key_id
        secret = settings.s3_secret_access_key
        return boto3.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=endpoint_url,
            aws_access_key_id=key.get_secret_value() if key else None,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=3,
                read_timeout=15,
            ),
        )

    async def _call(self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except (ClientError, BotoCoreError) as exc:
            if isinstance(exc, ClientError) and _code(exc) in _EXPECTED:
                raise
            raise StorageUnavailable(
                "Object storage did not respond as expected.",
                hint="Try again in a moment.",
            ) from exc

    async def create_multipart(self, key: str) -> str:
        response = await self._call(
            lambda: self._client.create_multipart_upload(Bucket=self._bucket, Key=key)
        )
        return response["UploadId"]

    def presign_parts(
        self, key: str, upload_id: str, parts: Sequence[PartSpec], *, expires_in: int
    ) -> Mapping[int, str]:
        return {
            part.part_number: self._signer.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part.part_number,
                    "ContentLength": part.content_length,
                },
                ExpiresIn=expires_in,
            )
            for part in parts
        }

    async def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[CompletedPart]
    ) -> None:
        payload: Any = {"Parts": [{"PartNumber": p.part_number, "ETag": p.etag} for p in parts]}
        try:
            await self._call(
                lambda: self._client.complete_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id, MultipartUpload=payload
                )
            )
        except ClientError as exc:
            code = _code(exc)
            if code == "NoSuchUpload":
                raise MultipartNotFoundError from exc
            if code in _BAD_PARTS:
                raise InvalidPartsError(code) from exc
            raise

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            await self._call(
                lambda: self._client.abort_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id
                )
            )
        except ClientError as exc:
            if _code(exc) != "NoSuchUpload":
                raise

    async def head(self, key: str) -> ObjectInfo | None:
        try:
            response = await self._call(
                lambda: self._client.head_object(Bucket=self._bucket, Key=key)
            )
        except ClientError:
            return None
        return ObjectInfo(size=int(response["ContentLength"]), etag=str(response["ETag"]))

    async def read_range(self, key: str, start: int, end: int) -> bytes:
        def _read() -> bytes:
            response = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes={start}-{end}"
            )
            body: bytes = response["Body"].read()
            return body

        return await self._call(_read)

    async def copy(self, source_key: str, dest_key: str) -> None:
        await self._call(
            lambda: self._client.copy_object(
                Bucket=self._bucket,
                Key=dest_key,
                CopySource={"Bucket": self._bucket, "Key": source_key},
            )
        )

    async def delete(self, key: str) -> None:
        await self._call(lambda: self._client.delete_object(Bucket=self._bucket, Key=key))


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
