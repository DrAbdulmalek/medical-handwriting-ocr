# =============================================================================
# Medical Handwriting OCR — Multi-Region Terraform Module
# =============================================================================
# Provides active-passive multi-region deployment with cross-region
# database replication, global DNS failover via Route53, and
# per-region VPC/networking isolation.
#
# Architecture:
#   Primary Region (e.g., us-east-1):
#     - Full EKS cluster (general + GPU node groups)
#     - RDS PostgreSQL (writer instance)
#     - ElastiCache Redis (primary)
#     - MinIO (S3-compatible object storage)
#     - Application Load Balancer (ALB)
#
#   Secondary Region (e.g., eu-west-1):
#     - Standby EKS cluster (scaled down, no GPU)
#     - RDS PostgreSQL read replica (cross-region)
#     - ElastiCache Redis read replica (Global Datastore)
#     - MinIO gateway to S3 (cross-region replication)
#     - Standby ALB (Route53 health-check driven)
#
# Failover:
#   - Route53 health checks monitor primary ALB
#   - On primary failure, DNS automatically shifts traffic to secondary
#   - RDS read replica can be promoted to standalone writer
#   - Kubernetes pods auto-scale in secondary region
# =============================================================================

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "environment" {
  type        = string
  description = "Deployment environment (development, staging, production)"
  default     = "production"

  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be development, staging, or production."
  }
}

variable "primary_region" {
  type        = string
  description = "AWS region for the primary deployment"
  default     = "us-east-1"
}

variable "secondary_region" {
  type        = string
  description = "AWS region for the secondary (failover) deployment"
  default     = "eu-west-1"
}

variable "additional_regions" {
  type        = list(string)
  description = "Optional additional regions for read-only caching or future expansion"
  default     = []
}

variable "domain_name" {
  type        = string
  description = "Route53 hosted zone domain name for the application"
  default     = ""
}

variable "eks_cluster_version" {
  type        = string
  description = "Kubernetes version for EKS clusters"
  default     = "1.28"
}

variable "primary_vpc_cidr" {
  type        = string
  description = "CIDR block for the primary region VPC"
  default     = "10.0.0.0/16"
}

variable "secondary_vpc_cidr" {
  type        = string
  description = "CIDR block for the secondary region VPC"
  default     = "10.1.0.0/16"
}

variable "dictionary_repo_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "enable_gpu" {
  type        = bool
  description = "Enable GPU node groups in primary EKS cluster"
  default     = true
}

variable "primary_instance_types" {
  type        = map(string)
  description = "Instance types per node group in the primary region"
  default = {
    general = "m6i.xlarge"
    gpu     = "g4dn.xlarge"
  }
}

variable "secondary_instance_types" {
  type        = map(string)
  description = "Instance types per node group in the secondary region (standby)"
  default = {
    general = "m6i.large"
  }
}

variable "rds_instance_class_primary" {
  type    = string
  default = "db.r6g.xlarge"
}

variable "rds_instance_class_secondary" {
  type    = string
  default = "db.r6g.large"
}

variable "rds_allocated_storage" {
  type    = number
  default = 100
}

variable "rds_max_allocated_storage" {
  type    = number
  default = 1000
}

variable "alarm_email_addresses" {
  type        = list(string)
  description = "Email addresses for CloudWatch alarm notifications"
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources"
  default     = {}
}
