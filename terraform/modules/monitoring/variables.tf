# -----------------------------------------------------------------------------
# Monitoring Module - Input Variables
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
  description = "AWS region for monitoring resources"
  type        = string
  default     = "us-east-1"
}

# -- SNS Topic Configuration ---------------------------------------------------

variable "alarm_email_addresses" {
  description = "List of email addresses to subscribe to the alarm SNS topic"
  type        = list(string)
  default     = []
}

variable "sns_topic_name" {
  description = "Custom name for the SNS alarm topic (default: auto-generated)"
  type        = string
  default     = ""
}

# -- CloudWatch Dashboard -----------------------------------------------------

variable "enable_dashboard" {
  description = "Create a CloudWatch dashboard for OCR application metrics"
  type        = bool
  default     = true
}

# -- Log Groups ----------------------------------------------------------------

variable "log_group_names" {
  description = "List of CloudWatch log group names to create (backend, celery, nginx, etc.)"
  type        = list(string)
  default     = []
}

variable "default_log_retention_days" {
  description = "Default retention in days for log groups not explicitly configured"
  type        = number
  default     = 14
}

# -- Alarm Thresholds ----------------------------------------------------------

variable "high_error_rate_threshold" {
  description = "Error rate threshold (%) for the high-error-rate alarm"
  type        = number
  default     = 5.0
}

variable "high_latency_threshold" {
  description = "Latency threshold (ms) for the high-latency alarm"
  type        = number
  default     = 2000
}

variable "low_health_score_threshold" {
  description = "Health score threshold (0-100) for the low-health-score alarm"
  type        = number
  default     = 80
}

variable "alarm_evaluation_periods" {
  description = "Number of consecutive periods an alarm must breach before firing"
  type        = number
  default     = 3
}

variable "alarm_period_seconds" {
  description = "Time period (seconds) over which to evaluate each alarm metric"
  type        = number
  default     = 300
}

# -- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all monitoring resources"
  type        = map(string)
  default     = {}
}
