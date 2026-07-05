# =============================================================================
# Medical Handwriting OCR — Multi-Region Terraform Module
# =============================================================================
#
# This module provides active-passive multi-region deployment with:
# - Cross-region VPC peering
# - Primary RDS with cross-region read replica
# - ElastiCache Redis Global Datastore
# - Route53 DNS-based failover
# - Per-region CloudWatch monitoring
#
# Usage:
#   module "multi_region" {
#     source = "./modules/multi-region"
#
#     primary_region   = "us-east-1"
#     secondary_region = "eu-west-1"
#     domain_name      = "api.medical-ocr.example.com"
#     environment      = "production"
#
#     alarm_email_addresses = ["oncall@example.com"]
#
#     dictionary_repo_token = var.dictionary_repo_token
#   }
#
# Requirements:
#   - Terraform >= 1.5.0
#   - AWS provider with access to both regions
#   - IAM permissions for VPC, EKS, RDS, ElastiCache, Route53, ACM, CloudWatch
#
# Failover Process:
#   1. Route53 health checks detect primary ALB failure (3 consecutive failures)
#   2. DNS automatically redirects traffic to secondary region
#   3. Secondary EKS auto-scales to handle increased load
#   4. To promote the read replica to standalone writer:
#      aws rds promote-read-replica \
#        --db-instance-identifier medical-ocr-production-secondary-replica
#   5. Update application config to point to the new primary database
# =============================================================================

# Default: single-region (backward compatible)
# Uncomment the module block below to enable multi-region:

# module "multi_region" {
#   source              = "./modules/multi-region"
#   environment         = "production"
#   primary_region      = "us-east-1"
#   secondary_region   = "eu-west-1"
#   domain_name         = "api.medical-ocr.example.com"
#   dictionary_repo_token = var.dictionary_repo_token
#   alarm_email_addresses = ["oncall@example.com"]
# }
