# -----------------------------------------------------------------------------
# VPC Networking Module
# -----------------------------------------------------------------------------
# Creates a complete VPC networking foundation for the Medical Handwriting OCR
# project, including:
#   - VPC with configurable CIDR
#   - Public and private subnets across multiple availability zones
#   - NAT gateway(s) — single in non-production, highly-available in production
#   - Internet gateway for public subnet egress
#   - VPC endpoints for AWS services (Secrets Manager, SSM, ECR)
#   - Security groups for EKS cluster, RDS, and Load Balancer
# -----------------------------------------------------------------------------

locals {
  # Resolve AZs: use explicit list if provided, otherwise derive from region
  azs = length(var.availability_zones) > 0 ? var.availability_zones : [
    "${var.region}a",
    "${var.region}b",
    "${var.region}c",
  ]

  # Standard tags merged with caller-supplied extras
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "networking"
    },
    var.tags
  )
}

# =============================================================================
# VPC
# =============================================================================
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.project_name}-${var.environment}-vpc"
  cidr = var.vpc_cidr

  azs = local.azs

  private_subnets = var.private_subnet_cidrs
  public_subnets  = var.public_subnet_cidrs

  # NAT gateway configuration
  enable_nat_gateway = var.enable_nat_gateway
  single_nat_gateway = var.single_nat_gateway

  # DNS support for EKS and internal resolution
  enable_dns_hostnames = true
  enable_dns_support   = true

  # Tag subnets so that Kubernetes / EKS auto-discovery works
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }

  tags = local.common_tags
}

# =============================================================================
# VPC Endpoints (AWS PrivateLink)
# =============================================================================
# Interface endpoints allow private subnets to reach AWS services without
# traversing the public internet, improving security and reducing NAT costs.

resource "aws_security_group" "vpc_endpoints" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  name        = "${var.project_name}-${var.environment}-vpc-endpoints-sg"
  description = "Security group for VPC interface endpoints"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Inbound from VPC CIDR"
    from_port      = 443
    to_port        = 443
    protocol       = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint" "interface" {
  count = var.enable_vpc_endpoints ? length(var.vpc_endpoint_services) : 0

  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.region}.${var.vpc_endpoint_services[count.index]}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids         = module.vpc.private_subnets
  security_group_ids = coalesce(aws_security_group.vpc_endpoints[*].id, [])

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-${var.vpc_endpoint_services[count.index]}"
  })
}

# =============================================================================
# Security Groups
# =============================================================================

# -- EKS Cluster Security Group ------------------------------------------------
# Controls traffic to/from the EKS cluster control plane
resource "aws_security_group" "eks_cluster" {
  name        = "${var.project_name}-${var.environment}-eks-cluster-sg"
  description = var.eks_cluster_security_group_description
  vpc_id      = module.vpc.vpc_id

  # Allow all outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-eks-cluster-sg"
  })
}

# Ingress rule: allow Kubernetes API (port 443) from allowed CIDRs
resource "aws_security_group_rule" "eks_cluster_ingress_api" {
  type              = "ingress"
  description       = "Kubernetes API server access"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = var.allowed_cidr_blocks
  security_group_id = aws_security_group.eks_cluster.id
}

# -- RDS Security Group --------------------------------------------------------
# Controls traffic to/from the RDS PostgreSQL instance
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-${var.environment}-rds-sg"
  description = var.rds_security_group_description
  vpc_id      = module.vpc.vpc_id

  # PostgreSQL port
  ingress {
    description     = "PostgreSQL access from VPC"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
  }

  # Allow all outbound (for patches, backups, etc.)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-rds-sg"
  })
}

# -- Load Balancer Security Group ----------------------------------------------
# Controls traffic to/from the Application Load Balancer
resource "aws_security_group" "load_balancer" {
  name        = "${var.project_name}-${var.environment}-lb-sg"
  description = var.lb_security_group_description
  vpc_id      = module.vpc.vpc_id

  # HTTP ingress
  ingress {
    description = "HTTP from allowed CIDRs"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # HTTPS ingress
  ingress {
    description = "HTTPS from allowed CIDRs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # Allow all outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-lb-sg"
  })
}
