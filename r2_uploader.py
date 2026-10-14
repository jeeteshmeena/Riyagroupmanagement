"""
r2_uploader.py
--------------
Cloudflare R2 Direct Uploader with S3 SigV4 & boto3 fallback.
Zero external dependency required (uses standard urllib/hashlib/hmac with optional boto3).
Applies watermark to images before upload.
"""

import datetime
import hashlib
import hmac
import io
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Cloudflare R2 Credentials
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "riya-posters").strip()
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "https://pub-d738aa13a7944050a7edb60cc5cd91bb.r2.dev").strip().rstrip("/")


def is_r2_configured() -> bool:
    """Check if Cloudflare R2 credentials are set in environment."""
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def upload_bytes_to_r2(data_bytes: bytes, file_name: str, content_type: str = "image/jpeg") -> Optional[str]:
    """
    Upload raw bytes to Cloudflare R2 using AWS S3 Signature Version 4.
    Returns the public CDN / R2 URL on success, or None on failure.
    """
    if not is_r2_configured():
        logger.warning("[R2] Cloudflare R2 is not configured in .env (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)")
        return None

    # First attempt: boto3 if installed
    try:
        import boto3
        from botocore.config import Config
        endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        s3 = boto3.client(
            service_name="s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(s3={"addressing_style": "path"})
        )
        s3.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=file_name,
            Body=data_bytes,
            ContentType=content_type
        )
        public_url = f"{R2_PUBLIC_URL}/{file_name.lstrip('/')}"
        logger.info(f"[R2] Uploaded via boto3: {public_url}")
        return public_url
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[R2] boto3 upload attempt failed ({e}), trying native SigV4...")

    # Second attempt: Pure Python HTTP PUT with AWS SigV4
    try:
        method = "PUT"
        service = "s3"
        host = f"{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        endpoint = f"https://{host}/{R2_BUCKET_NAME}/{file_name}"
        region = "auto"

        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        canonical_uri = f"/{R2_BUCKET_NAME}/{urllib.parse.quote(file_name)}"
        canonical_querystring = ""
        payload_hash = hashlib.sha256(data_bytes).hexdigest()

        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"

        canonical_request = (
            f"{method}\n"
            f"{canonical_uri}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = _get_signature_key(R2_SECRET_ACCESS_KEY, date_stamp, region, service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"{algorithm} Credential={R2_ACCESS_KEY_ID}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Content-Type": content_type,
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization_header,
        }

        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                public_url = f"{R2_PUBLIC_URL}/{file_name.lstrip('/')}"
                logger.info(f"[R2] Uploaded via SigV4 PUT: {public_url}")
                return public_url
            else:
                logger.error(f"[R2] Upload failed status: {resp.status}")
                return None
    except Exception as ex:
        logger.error(f"[R2] Native S3 SigV4 error: {ex}")
        return None


def apply_watermark_and_upload_r2(raw_img_bytes: bytes, story_name: str = "") -> Tuple[bytes, Optional[str]]:
    """
    1. Applies the standard bot watermark to the image.
    2. Uploads the watermarked image to Cloudflare R2.
    3. Returns (watermarked_bytes, r2_public_url).
    """
    watermarked_bytes = raw_img_bytes

    # Step 1: Apply watermark using post_builder or poster_bot
    try:
        from post_builder import apply_watermark
        watermarked_bytes = apply_watermark(raw_img_bytes)
        logger.info(f"[R2 Watermark] Applied watermark ({len(watermarked_bytes)} bytes)")
    except Exception as e:
        logger.warning(f"[R2 Watermark] Failed to apply watermark: {e}")
        try:
            from plugins.poster_bot import apply_watermark_to_image
            buf = apply_watermark_to_image(raw_img_bytes)
            watermarked_bytes = buf.getvalue()
        except Exception:
            watermarked_bytes = raw_img_bytes

    # Step 2: Generate unique filename
    clean_slug = "".join(c for c in story_name.lower().replace(" ", "_") if c.isalnum() or c == "_")[:30]
    ts = int(time.time())
    file_name = f"posters/{clean_slug}_{ts}.jpg" if clean_slug else f"posters/cover_{ts}.jpg"

    # Step 3: Upload to Cloudflare R2
    r2_url = upload_bytes_to_r2(watermarked_bytes, file_name, content_type="image/jpeg")
    return watermarked_bytes, r2_url
