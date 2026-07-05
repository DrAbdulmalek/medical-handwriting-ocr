# -----------------------------------------------------------------------------
# RDS PostgreSQL Module
# -----------------------------------------------------------------------------
# Provisions an Amazon RDS PostgreSQL database for the Medical Handwriting OCR
# project, including:
#   - RDS instance with environment-based instance sizing
#   - Custom parameter group with PostgreSQL optimizations for OCR workloads
#   - DB subnet group placed in private subnets
#   - Random password generation for the master user
#   - CloudWatch alarms for CPU, free storage, and connection count
# -----------------------------------------------------------------------------

locals {
  # Standard tags merged with caller-supplied extras
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "database"
    },
    var.tags
  )

  # Environment-based sizing: fall back to sensible defaults if not overridden
  resolved_instance_class = var.instance_class != "" ? var.instance_class : (
    var.environment == "production" ? "db.r6g.xlarge" : "db.t3.medium"
  )

  resolved_allocated_storage = var.allocated_storage > 0 ? var.allocated_storage : (
    var.environment == "production" ? 100 : 20
  )

  resolved_max_allocated_storage = var.max_allocated_storage > 0 ? var.max_allocated_storage : (
    var.environment == "production" ? 1000 : 100
  )

  resolved_backup_retention = var.backup_retention_period > 0 ? var.backup_retention_period : (
    var.environment == "production" ? 30 : 7
  )

  resolved_deletion_protection = var.deletion_protection != null ? var.deletion_protection : (
    var.environment == "production"
  )

  db_identifier = "${var.project_name}-${var.environment}"
}

# =============================================================================
# Random Password
# =============================================================================
resource "random_password" "db_password" {
  length           = 32
  special          = false
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Store the generated password in Secrets Manager so operators can retrieve it
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.project_name}/${var.environment}/db-password"
  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id = aws_secretsmanager_secret.db_password.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db_password.result
  })
}

# =============================================================================
# DB Parameter Group (PostgreSQL Optimizations for OCR Workload)
# =============================================================================
# Tune PostgreSQL for the Medical OCR workload: larger work_mem for complex
# queries (e.g., full-text search on handwriting transcription data),
# effective_cache_size for better query planning, and connection pooling.

resource "aws_db_parameter_group" "ocr_optimized" {
  family = "postgres15"
  name   = "${local.db_identifier}-pg-params"

  description = "PostgreSQL parameter group optimized for Medical OCR workloads"

  # Increase work_mem for complex queries (full-text search, aggregations)
  parameter {
    name  = "work_mem"
    value = "64MB"
  }

  # Tell the planner more memory is available (typically 75% of system RAM)
  parameter {
    name  = "effective_cache_size"
    value = var.environment == "production" ? "6GB" : "2GB"
  }

  # Connection-related optimizations
  parameter {
    name  = "max_connections"
    value = var.environment == "production" ? "200" : "100"
  }

  # Enable parallel query execution for large scans
  parameter {
    name  = "max_parallel_workers_per_gather"
    value = var.environment == "production" ? "4" : "2"
  }

  # Log slow queries (useful for identifying performance bottlenecks)
  parameter {
    name  = "log_min_duration_statement"
    value = "500" # Log queries slower than 500ms
  }

  # Auto-vacuum tuning for high-insert/update OCR data workflows
  parameter {
    name  = "autovacuum_max_workers"
    value = var.environment == "production" ? "4" : "2"
  }

  tags = local.common_tags
}

# =============================================================================
# DB Subnet Group
# =============================================================================
resource "aws_db_subnet_group" "main" {
  name       = "${local.db_identifier}-subnet-group"
  subnet_ids = var.subnet_ids

  description = "Database subnet group for ${local.db_identifier}"

  tags = local.common_tags
}

# =============================================================================
# RDS Instance
# =============================================================================
resource "aws_db_instance" "main" {
  identifier = local.db_identifier

  # Engine
  engine         = var.engine
  engine_version = var.engine_version

  # Instance sizing
  instance_class         = local.resolved_instance_class
  allocated_storage      = local.resolved_allocated_storage
  max_allocated_storage  = local.resolved_max_allocated_storage
  storage_type           = var.storage_type
  iops                  = var.iops

  # Credentials
  db_name  = var.db_name
  username = var.db_username
  password = random_password.db_password.result

  # Network placement
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = var.security_group_ids

  # Parameter group
  parameter_group_name = aws_db_parameter_group.ocr_optimized.name

  # High availability: multi-AZ in production
  multi_az = var.environment == "production"

  # Backup configuration
  backup_retention_period = local.resolved_backup_retention
  skip_final_snapshot     = var.environment != "production" ? true : var.skip_final_snapshot
  deletion_protection     = local.resolved_deletion_protection

  # Performance Insights (free tier for db.t3, paid for larger instances)
  performance_insights_enabled = var.environment == "production"
  performance_insights_retention_period = var.environment == "production" ? 7 : null

  # Enable encryption at rest
  storage_encrypted = true

  tags = local.common_tags
}

# =============================================================================
# CloudWatch Alarms
# =============================================================================
# Alerts when database health metrics exceed configured thresholds.

resource "aws_cloudwatch_metric_alarm" "cpu" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.db_identifier}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300  # 5 minutes
  statistic           = "Average"
  threshold           = var.cpu_threshold
  alarm_description   = "RDS CPU utilization exceeds ${var.cpu_threshold}%"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "free_storage" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.db_identifier}-low-storage"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.storage_threshold * 1073741824 # Convert GB to bytes
  alarm_description   = "RDS free storage space below ${var.storage_threshold} GB"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "connections" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.db_identifier}-high-connections"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.connections_threshold
  alarm_description   = "RDS database connections exceed ${var.connections_threshold}"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  tags = local.common_tags
}
