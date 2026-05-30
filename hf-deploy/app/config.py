"""
Minimal configuration for HuggingFace Spaces deployment.
"""


class Settings:
    UPLOAD_DIR: str = "./uploads"
    CROP_DIR: str = "./crops"
    TEMP_DIR: str = "./tmp"


settings = Settings()
