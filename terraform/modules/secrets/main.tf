# -----------------------------------------------------------------------------
# AWS Secrets Manager Module
# -----------------------------------------------------------------------------
# Manages application secrets for the Medical Handwriting OCR project via
# AWS Secrets Manager, including:
#   - Dictionary repository token (conditional, only when token is provided)
#   - UMLS API key (conditional, only when key is provided)
#   - Admin token (conditional, only when token is provided)
#   - MinIO object storage credentials (conditional)
#   - Database URL (constructed from RDS module outputs)
#   - A map of all secret ARNs for easy reference
# -----------------------------------------------------------------------------

locals {
  # Standard tags merged with caller-supplied extras
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "secrets"
    },
    var.tags
  )

  # Construct the PostgreSQL connection URL from RDS module outputs
  # Format: postgresql://user:password@host:port/dbname
  db_url = "postgresql://${var.db_username}:${var.db_password}@${var.db_endpoint}/${var.db_name}"
}

# =============================================================================
# Dictionary Repository Token
# =============================================================================
resource "aws_secretsmanager_secret" "dictionary_token" {
  count = var.dictionary_repo_token != "" ? 1 : 0

  name                    = "${var.project_name}/${var.environment}/dictionary-token"
  recovery_window_in_days = var.recovery_window_in_days

  description = "Authentication token for the medical dictionary repository"
  tags        = local.common_tags
}

resource "aws_secretsmanager_secret_version" "dictionary_token" {
  count      = var.dictionary_repo_token != "" ? 1 : 0
  secret_id  = aws_secretsmanager_secret.dictionary_token[0].id
  secret_string = jsonencode({
    token = var.dictionary_repo_token
  })
}

# =============================================================================
# UMLS API Key
# =============================================================================
resource "aws_secretsmanager_secret" "umls_api_key" {
  count = var.umls_api_key != "" ? 1 : 0

  name                    = "${var.project_name}/${var.environment}/umls-api-key"
  recovery_window_in_days = var.recovery_window_in_days

  description = "UMLS (Unified Medical Language System) API key for medical terminology lookup"
  tags        = local.common_tags
}

resource "aws_secretsmanager_secret_version" "umls_api_key" {
  count      = var.umls_api_key != "" ? 1 : 0
  secret_id  = aws_secretsmanager_secret.umls_api_key[0].id
  secret_string = jsonencode({
    api_key = var.umls_api_key
  })
}

# =============================================================================
# Admin Token
# =============================================================================
resource "aws_secretsmanager_secret" "admin_token" {
  count = var.admin_token != "" ? 1 : 0

  name                    = "${var.project_name}/${var.environment}/admin-token"
  recovery_window_in_days = var.recovery_window_in_days

  description = "Admin access token for the OCR application management API"
  tags        = local.common_tags
}

resource "aws_secretsmanager_secret_version" "admin_token" {
  count      = var.admin_token != "" ? 1 : 0
  secret_id  = aws_secretsmanager_secret.admin_token[0].id
  secret_string = jsonencode({
    token = var.admin_token
  })
}

# =============================================================================
# MinIO Object Storage Credentials
# =============================================================================
resource "aws_secretsmanager_secret" "minio_credentials" {
  count = var.minio_access_key != "" && var.minio_secret_key != "" ? 1 : 0

  name                    = "${var.project_name}/${var.environment}/minio-credentials"
  recovery_window_in_days = var.recovery_window_in_days

  description = "MinIO access and secret keys for object storage"
  tags        = local.common_tags
}

resource "aws_secretsmanager_secret_version" "minio_credentials" {
  count      = var.minio_access_key != "" && var.minio_secret_key != "" ? 1 : 0
  secret_id  = aws_secretsmanager_secret.minio_credentials[0].id
  secret_string = jsonencode({
    access_key = var.minio_access_key
    secret_key = var.minio_secret_key
  })
}

# =============================================================================
# Database Connection URL
# =============================================================================
resource "aws_secretsmanager_secret" "db_url" {
  name                    = "${var.project_name}/${var.environment}/database-url"
  recovery_window_in_days = var.recovery_window_in_days

  description = "PostgreSQL connection URL for the Medical OCR application"
  tags        = local.common_tags
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = jsonencode({
    url      = local.db_url
    endpoint = var.db_endpoint
    database = var.db_name
    username = var.db_username
  })
}
