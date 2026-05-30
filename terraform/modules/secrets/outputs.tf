# -----------------------------------------------------------------------------
# Secrets Module - Outputs
# -----------------------------------------------------------------------------

output "secret_arns" {
  description = "Map of secret names to their ARNs for all provisioned secrets"
  value = merge(
    # Always present: database URL
    {
      "database_url" = aws_secretsmanager_secret.db_url.arn
    },
    # Conditional secrets (only included when created)
    {
      "dictionary_token"   = try(aws_secretsmanager_secret.dictionary_token[0].arn, null)
      "umls_api_key"        = try(aws_secretsmanager_secret.umls_api_key[0].arn, null)
      "admin_token"         = try(aws_secretsmanager_secret.admin_token[0].arn, null)
      "minio_credentials"   = try(aws_secretsmanager_secret.minio_credentials[0].arn, null)
    }
  )
}

output "db_url_secret_arn" {
  description = "ARN of the database URL secret"
  value       = aws_secretsmanager_secret.db_url.arn
}

output "dictionary_token_secret_arn" {
  description = "ARN of the dictionary token secret (null if not created)"
  value       = try(aws_secretsmanager_secret.dictionary_token[0].arn, null)
}

output "umls_api_key_secret_arn" {
  description = "ARN of the UMLS API key secret (null if not created)"
  value       = try(aws_secretsmanager_secret.umls_api_key[0].arn, null)
}

output "admin_token_secret_arn" {
  description = "ARN of the admin token secret (null if not created)"
  value       = try(aws_secretsmanager_secret.admin_token[0].arn, null)
}

output "minio_credentials_secret_arn" {
  description = "ARN of the MinIO credentials secret (null if not created)"
  value       = try(aws_secretsmanager_secret.minio_credentials[0].arn, null)
}
