import asyncio
from functools import partial
from uuid import uuid4

import boto3

from app.config import get_settings


async def upload_photo(file_bytes: bytes, filename: str) -> str:
    settings = get_settings()

    generated_filename = f"{uuid4()}.jpg"
    endpoint_url = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    def _upload() -> None:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        )
        client.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=generated_filename,
            Body=file_bytes,
            ContentType="image/jpeg",
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _upload)

    return settings.R2_PUBLIC_URL + "/" + generated_filename
