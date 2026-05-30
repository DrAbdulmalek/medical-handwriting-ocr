# -----------------------------------------------------------------------------
# Secrets Module - Input Variables
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

# -- Secret Values (sensitive inputs) -----------------------------------------

variable "dictionary_repo_token" {
  description = "Authentication token for the medical dictionary repository"
  type        = string
  sensitive   = true
  default     = ""
}

variable "umls_api_key" {
  description = "UMLS (Unified Medical Language System) API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "admin_token" {
  description = "Admin access token for the OCR application"
  type        = string
  sensitive   = true
  default     = ""
}

variable "minio_access_key" {
  description = "MinIO access key for object storage"
  type        = string
  sensitive   = true
  default     = ""
}

variable "minio_secret_key" {
  description = "MinIO secret key for object storage"
  type        = string
  sensitive   = true
  default     = ""
}

# -- Database Connection (constructed from RDS outputs) -----------------------

variable "db_endpoint" {
  description = "RDS instance endpoint (hostname:port), from the database module"
  type        = string
}

variable "db_name" {
  description = "Database name, from the database module"
  type        = string
}

variable "db_username" {
  description = "Database username, from the database module"
  type        = string
}

variable "db_password" {
  description = "Database password, from the database module (sensitive)"
  type        = string
  sensitive   = true
}

variable "db_port" {
  description = "Database port"
  type        = number
  default     = 5432
}

# -- Recovery Window -----------------------------------------------------------

variable "recovery_window_in_days" {
  description = "Number of days to retain deleted secrets before permanent deletion"
  type        = number
  default     = 7
}

# -- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all secret resources"
  type        = map(string)
  default     = {}
}
