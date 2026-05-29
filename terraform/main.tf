terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.23" }
    helm = { source = "hashicorp/helm", version = "~> 2.11" }
  }
  backend "s3" {
    bucket         = "medical-ocr-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

variable "environment" {
  type    = string
  default = "staging"
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be development, staging, or production."
  }
}

variable "region" { type = string; default = "us-east-1" }
variable "dictionary_repo_token" { type = string; sensitive = true; default = "" }

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "medical-ocr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# VPC
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  name    = "medical-ocr-${var.environment}"
  cidr    = "10.0.0.0/16"
  azs     = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
  enable_nat_gateway   = true
  single_nat_gateway   = var.environment != "production"
  enable_dns_hostnames = true
}

# EKS
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"
  cluster_name    = "medical-ocr-${var.environment}"
  cluster_version = "1.28"
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets
  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    general = {
      desired_size = var.environment == "production" ? 3 : 2
      min_size     = 1
      max_size     = var.environment == "production" ? 10 : 5
      instance_types = ["m6i.xlarge"]
      capacity_type  = var.environment == "production" ? "ON_DEMAND" : "SPOT"
    }
    gpu = {
      desired_size = var.environment == "production" ? 2 : 1
      min_size     = 0
      max_size     = var.environment == "production" ? 5 : 2
      instance_types = ["g4dn.xlarge"]
      capacity_type  = "ON_DEMAND"
      labels = { "nvidia.com/gpu" = "true" }
      taints = [{ key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }]
    }
  }
}

# RDS
resource "aws_db_instance" "main" {
  identifier     = "medical-ocr-${var.environment}"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.environment == "production" ? "db.r6g.xlarge" : "db.t3.medium"
  allocated_storage     = var.environment == "production" ? 100 : 20
  max_allocated_storage = var.environment == "production" ? 1000 : 100
  db_name  = "medical_ocr"
  username = "ocr_admin"
  password = random_password.db_password.result
  backup_retention_period = var.environment == "production" ? 30 : 7
  deletion_protection     = var.environment == "production"
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

# Secrets
resource "aws_secretsmanager_secret" "dictionary_token" {
  count = var.dictionary_repo_token != "" ? 1 : 0
  name  = "medical-ocr/dictionary-token-${var.environment}"
}

resource "aws_secretsmanager_secret_version" "dictionary_token" {
  count         = var.dictionary_repo_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.dictionary_token[0].id
  secret_string = var.dictionary_repo_token
}

output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "db_endpoint" { value = aws_db_instance.main.endpoint; sensitive = true }