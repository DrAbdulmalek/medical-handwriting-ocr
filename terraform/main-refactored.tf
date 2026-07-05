# =============================================================================
# Medical Handwriting OCR - Refactored Root Module
# =============================================================================
# This file is the refactored entry point for the Medical Handwriting OCR
# infrastructure. It replaces the monolithic main.tf with a modular architecture.
#
# SWITCHING FROM main.tf:
#   1. Ensure main.tf does NOT exist (rename or remove it)
#   2. Rename this file:  mv main-refactored.tf main.tf
#   3. Run:  terraform init -reconfigure
#
# Or use the -var-file flag to keep both files separate:
#   terraform plan -var-file=terraform.tfvars
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration & Providers
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # Remote state backend — update bucket/dynamo values for your account
  backend "s3" {
    bucket         = "medical-ocr-terraform-state"
    key            = "infrastructure/terraform-refactored.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = local.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Locals — Naming Conventions & Shared Values
# -----------------------------------------------------------------------------
locals {
  project_name = "medical-ocr"

  # Full resource name prefix
  name_prefix = "${local.project_name}-${var.environment}"

  # Common tags used across all modules
  common_tags = {
    Project     = local.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # Environment-aware configuration shortcuts
  is_production = var.environment == "production"
}

# -----------------------------------------------------------------------------
# Input Variables
# -----------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment (development, staging, or production)"
  type        = string

  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be one of: development, staging, production."
  }
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

# -- Sensitive: Secret Values -------------------------------------------------

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

# -- Notification -------------------------------------------------------------

variable "alarm_email_addresses" {
  description = "Email addresses to receive CloudWatch alarm notifications"
  type        = list(string)
  default     = []
}

# =============================================================================
# Module: Networking
# =============================================================================
module "networking" {
  source = "./modules/networking"

  project_name = local.project_name
  environment  = var.environment
  region       = var.region

  vpc_cidr              = var.vpc_cidr
  single_nat_gateway    = !local.is_production  # HA NAT in production
  enable_vpc_endpoints = true

  tags = local.common_tags
}

# =============================================================================
# Module: EKS Cluster
# =============================================================================
module "eks" {
  source = "./modules/eks"

  project_name = local.project_name
  environment  = var.environment
  region       = var.region

  # Network inputs from the networking module
  vpc_id                      = module.networking.vpc_id
  subnet_ids                  = module.networking.private_subnet_ids
  cluster_security_group_id   = module.networking.cluster_security_group_id

  # Environment-based node group sizing
  general_node_group_desired_size = local.is_production ? 3 : 2
  general_node_group_min_size     = 1
  general_node_group_max_size     = local.is_production ? 10 : 5
  general_node_capacity_type      = local.is_production ? "ON_DEMAND" : "SPOT"

  gpu_node_group_desired_size = local.is_production ? 2 : 1
  gpu_node_group_min_size     = 0
  gpu_node_group_max_size     = local.is_production ? 5 : 2

  tags = local.common_tags
}

# =============================================================================
# Module: Database (RDS PostgreSQL)
# =============================================================================
module "database" {
  source = "./modules/database"

  project_name = local.project_name
  environment  = var.environment
  region       = var.region

  # Network inputs from the networking module
  subnet_ids         = module.networking.private_subnet_ids
  security_group_ids = [module.networking.rds_security_group_id]

  # CloudWatch alarm integration from the monitoring module
  # We pass the SNS topic ARN so database alarms fire to the same channel
  alarm_sns_topic_arn = module.monitoring.alarm_topic_arn

  tags = local.common_tags

  # Ensure the monitoring module's SNS topic exists before the database
  # module tries to reference it (circular dependency workaround handled
  # via the alarm_sns_topic_arn string — no resource dependency needed)
}

# =============================================================================
# Module: Secrets Manager
# =============================================================================
module "secrets" {
  source = "./modules/secrets"

  project_name = local.project_name
  environment  = var.environment

  # Sensitive secret values
  dictionary_repo_token = var.dictionary_repo_token
  umls_api_key           = var.umls_api_key
  admin_token            = var.admin_token
  minio_access_key       = var.minio_access_key
  minio_secret_key       = var.minio_secret_key

  # Database connection info — wired from the database module outputs
  db_endpoint = module.database.endpoint
  db_name     = module.database.db_name
  db_username = module.database.username
  db_password = module.database.db_password
  db_port     = module.database.port

  tags = local.common_tags
}

# =============================================================================
# Module: Monitoring (CloudWatch)
# =============================================================================
module "monitoring" {
  source = "./modules/monitoring"

  project_name = local.project_name
  environment  = var.environment
  region       = var.region

  alarm_email_addresses = var.alarm_email_addresses

  tags = local.common_tags
}

# =============================================================================
# Root Outputs
# =============================================================================
# Expose key values from each module for the operator and downstream consumers.

# -- Networking ----------------------------------------------------------------
output "vpc_id" {
  description = "VPC ID"
  value       = module.networking.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.networking.private_subnet_ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.networking.public_subnet_ids
}

# -- EKS ----------------------------------------------------------------------
output "cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_certificate_authority_data" {
  description = "EKS cluster CA certificate data (base64)"
  value       = module.eks.cluster_certificate_authority_data
}

output "configure_kubectl" {
  description = "Shell command to configure kubectl for this cluster"
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}

# -- Database -----------------------------------------------------------------
output "db_endpoint" {
  description = "RDS instance endpoint"
  value       = module.database.endpoint
  sensitive   = true
}

output "db_name" {
  description = "Database name"
  value       = module.database.db_name
}

output "db_password" {
  description = "Database master password (sensitive)"
  value       = module.database.db_password
  sensitive   = true
}

# -- Secrets ------------------------------------------------------------------
output "secret_arns" {
  description = "Map of all secret names to their ARNs"
  value       = module.secrets.secret_arns
}

# -- Monitoring ---------------------------------------------------------------
output "alarm_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarm notifications"
  value       = module.monitoring.alarm_topic_arn
}

output "dashboard_name" {
  description = "CloudWatch dashboard name"
  value       = module.monitoring.dashboard_name
}
