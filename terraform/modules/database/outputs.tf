# -----------------------------------------------------------------------------
# Database Module - Outputs
# -----------------------------------------------------------------------------

output "endpoint" {
  description = "RDS instance connection endpoint (hostname:port)"
  value       = aws_db_instance.main.endpoint
}

output "port" {
  description = "Database port"
  value       = aws_db_instance.main.port
}

output "db_name" {
  description = "Name of the default database"
  value       = aws_db_instance.main.db_name
}

output "username" {
  description = "Master database username"
  value       = aws_db_instance.main.username
}

output "db_password" {
  description = "Master database password (sensitive)"
  value       = random_password.db_password.result
  sensitive   = true
}

output "instance_id" {
  description = "RDS instance identifier"
  value       = aws_db_instance.main.id
}

output "db_password_secret_arn" {
  description = "ARN of the Secrets Manager secret containing the DB credentials"
  value       = aws_secretsmanager_secret.db_password.arn
}

output "db_subnet_group_name" {
  description = "Name of the DB subnet group"
  value       = aws_db_subnet_group.main.name
}

output "parameter_group_name" {
  description = "Name of the DB parameter group"
  value       = aws_db_parameter_group.ocr_optimized.name
}
