import uuid
from minio import Minio
from minio.error import S3Error
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class StorageService:
    def __init__(self):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        self.bucket = settings.MINIO_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Create bucket if not exists"""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created bucket: {self.bucket}")
        except S3Error as e:
            logger.error(f"MinIO error: {e}")

    def upload_crop(self, image_bytes: bytes, filename: str = None) -> str:
        """Upload word crop and return path"""
        if filename is None:
            filename = f"{uuid.uuid4()}.png"

        object_name = f"crops/{filename}"

        try:
            self.client.put_object(
                self.bucket,
                object_name,
                data=image_bytes,
                length=len(image_bytes),
                content_type="image/png"
            )
            return object_name
        except S3Error as e:
            logger.error(f"Upload failed: {e}")
            raise

    def get_crop_url(self, object_name: str, expires: int = 3600) -> str:
        """Get presigned URL for crop"""
        try:
            return self.client.presigned_get_object(self.bucket, object_name, expires)
        except S3Error:
            return None

    def download_crop(self, object_name: str) -> bytes:
        """Download crop bytes"""
        try:
            response = self.client.get_object(self.bucket, object_name)
            return response.read()
        except S3Error as e:
            logger.error(f"Download failed: {e}")
            return None


storage = StorageService()
