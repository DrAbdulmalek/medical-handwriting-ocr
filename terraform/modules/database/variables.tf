# -----------------------------------------------------------------------------
# Database Module - Input Variables
# -----------------------------------------------------------------------------

# -- Naming & Environment -----------------------------------------------------

variable "project_name" {
  description = "Name of the project, used for resource naming conventions"
  type        = string
  default     = "medical-ocr"
}

variable "environment" {
  description = "Deployment environment (development, staging, or production)"
  type        = string

  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be one of: development, staging, production."
  }
}

variable "region" {
  description = "AWS region for RDS deployment"
  type        = string
  default     = "us-east-1"
}

# -- Network Inputs (from networking module) ----------------------------------

variable "subnet_ids" {
  description = "List of private subnet IDs for the RDS subnet group"
  type        = list(string)
}

variable "security_group_ids" {
  description = "List of security group IDs to attach to the RDS instance"
  type        = list(string)
}

# -- Instance Configuration ---------------------------------------------------

variable "engine" {
  description = "Database engine"
  type        = string
  default     = "postgres"
}

variable "engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "15.4"
}

variable "instance_class" {
  description = "RDS instance class. Leave empty to auto-select based on environment."
  type        = string
  default     = ""
}

variable "allocated_storage" {
  description = "Allocated storage in GB. Leave empty to auto-select based on environment."
  type        = number
  default     = 0
}

variable "max_allocated_storage" {
  description = "Maximum allocated storage in GB for auto-scaling. Leave empty to auto-select."
  type        = number
  default     = 0
}

variable "storage_type" {
  description = "Storage type (gp2, gp3, or io1)"
  type        = string
  default     = "gp3"
}

variable "iops" {
  description = "Provisioned IOPS (only valid for io1 storage type)"
  type        = number
  default     = 0
}

# -- Database Credentials & Naming --------------------------------------------

variable "db_name" {
  description = "Name of the default database to create"
  type        = string
  default     = "medical_ocr"
}

variable "db_username" {
  description = "Username for the master database user"
  type        = string
  default     = "ocr_admin"
}

# -- Backup & Protection -------------------------------------------------------

variable "backup_retention_period" {
  description = "Days to retain automated backups. Leave empty to auto-select based on environment."
  type        = number
  default     = 0
}

variable "deletion_protection" {
  description = "Enable deletion protection. Leave empty to auto-select based on environment."
  type        = bool
  default     = null
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot when deleting the instance (false in production)"
  type        = bool
  default     = false
}

# -- CloudWatch Alarms --------------------------------------------------------

variable "enable_cloudwatch_alarms" {
  description = "Create CloudWatch alarms for the RDS instance"
  type        = bool
  default     = true
}

variable "cpu_threshold" {
  description = "CPU utilization threshold (%) for CloudWatch alarm"
  type        = number
  default     = 80
}

variable "storage_threshold" {
  description = "Free storage space threshold (GB) for CloudWatch alarm"
  type        = number
  default     = 5
}

variable "connections_threshold" {
  description = "Database connections threshold for CloudWatch alarm"
  type        = number
  default     = 100
}

variable "alarm_sns_topic_arn" {
  description = "ARN of the SNS topic to notify when alarms fire"
  type        = string
  default     = ""
}

# -- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all database resources"
  type        = map(string)
  default     = {}
}
