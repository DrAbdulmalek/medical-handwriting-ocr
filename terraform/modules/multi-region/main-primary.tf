# =============================================================================
# PRIMARY REGION — Full Application Stack
# =============================================================================

# -----------------------------------------------------------------------------
# Primary AWS Provider
# -----------------------------------------------------------------------------

provider "aws" {
  alias  = "primary"
  region = var.primary_region
  default_tags {
    tags = local.common_tags
  }
}

# -----------------------------------------------------------------------------
# Primary VPC
# -----------------------------------------------------------------------------

module "primary_vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  providers = { aws = aws.primary }

  name = "medical-ocr-${var.environment}-primary"
  cidr = var.primary_vpc_cidr

  azs = [
    "${var.primary_region}a",
    "${var.primary_region}b",
    "${var.primary_region}c",
  ]

  private_subnets = [
    cidrsubnet(var.primary_vpc_cidr, 8, 1),  # 10.0.1.0/24
    cidrsubnet(var.primary_vpc_cidr, 8, 2),  # 10.0.2.0/24
    cidrsubnet(var.primary_vpc_cidr, 8, 3),  # 10.0.3.0/24
  ]
  public_subnets = [
    cidrsubnet(var.primary_vpc_cidr, 8, 101),  # 10.0.101.0/24
    cidrsubnet(var.primary_vpc_cidr, 8, 102),  # 10.0.102.0/24
    cidrsubnet(var.primary_vpc_cidr, 8, 103),  # 10.0.103.0/24
  ]

  enable_nat_gateway   = true
  single_nat_gateway   = var.environment != "production"
  enable_dns_hostnames = true
  enable_vpn_gateway   = false  # Use VPC peering or Transit Gateway instead

  # Allow cross-region VPC peering
  enable_flow_log                       = true
  create_flow_log_cloudwatch_log_group   = true
  create_flow_log_cloudwatch_iam_role    = true
  flow_log_log_format                    = jsonencode({ version = 2, account_id = "$${aws_account_id}", region = "$${aws_region}", vpc_id = "$${vpc_id}", subnet_id = "$${subnet_id}", type = "VPCFlowLog" })
}

# -----------------------------------------------------------------------------
# Primary EKS Cluster
# -----------------------------------------------------------------------------

module "primary_eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"
  providers = { aws = aws.primary }

  cluster_name    = "medical-ocr-${var.environment}-primary"
  cluster_version = var.eks_cluster_version

  vpc_id     = module.primary_vpc.vpc_id
  subnet_ids = module.primary_vpc.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access  = true
  cluster_endpoint_public_access_cidrs = ["0.0.0.0/0"]

  cluster_addons = {
    vpc-cni = {
      most_recent = true
    }
    coredns = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent              = true
      service_account_role_arn = module.primary_iam.ebs_csi_irsa_arn
    }
  }

  eks_managed_node_groups = {
    general = {
      desired_size   = var.environment == "production" ? 3 : 2
      min_size       = 1
      max_size       = var.environment == "production" ? 10 : 5
      instance_types = [var.primary_instance_types.general]
      capacity_type  = var.environment == "production" ? "ON_DEMAND" : "SPOT"
    }
    gpu = {
      desired_size   = var.enable_gpu ? (var.environment == "production" ? 2 : 1) : 0
      min_size       = 0
      max_size       = var.enable_gpu ? (var.environment == "production" ? 5 : 2) : 0
      instance_types = [var.primary_instance_types.gpu]
      capacity_type  = "ON_DEMAND"
      labels = { "nvidia.com/gpu" = "true" }
      taints = [
        { key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }
      ]
    }
  }
}

# -----------------------------------------------------------------------------
# Primary IAM Roles
# -----------------------------------------------------------------------------

module "primary_iam" {
  source  = "terraform-aws-modules/iam/aws//modules/eks-blueprint"
  version = "~> 5.0"
  providers = { aws = aws.primary }
}

# -----------------------------------------------------------------------------
# Primary RDS PostgreSQL (Writer)
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "primary" {
  provider   = aws.primary
  name       = "medical-ocr-${var.environment}-primary"
  subnet_ids = module.primary_vpc.private_subnets
  tags       = { Name = "medical-ocr-${var.environment}-primary" }
}

resource "random_password" "primary_db" {
  provider = aws.primary
  length   = 32
  special  = false
}

resource "aws_db_instance" "primary" {
  provider = aws.primary

  identifier     = "medical-ocr-${var.environment}-primary"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.rds_instance_class_primary

  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage
  storage_type          = "gp3"

  db_name  = "medical_ocr"
  username = "ocr_admin"
  password = random_password.primary_db.result

  vpc_security_group_ids = [module.primary_vpc.default_security_group_id]
  db_subnet_group_name   = aws_db_subnet_group.primary.name

  backup_retention_period = var.environment == "production" ? 30 : 7
  deletion_protection     = var.environment == "production"
  multi_az                = var.environment == "production"
  storage_encrypted       = true

  # Performance Insights
  performance_insights_enabled          = var.environment == "production"
  performance_insights_retention_period   = var.environment == "production" ? 7 : 0

  # Enable cross-region read replica
  replication_source_db = null  # This is the primary (writer)

  parameter_group_name = "default.postgres15"

  # CloudWatch logs
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  skip_final_snapshot = var.environment != "production"
  final_snapshot_identifier = var.environment != "production" ? null : "medical-ocr-${var.environment}-primary-final"

  tags = { Role = "primary-writer" }
}

# -----------------------------------------------------------------------------
# Primary ElastiCache Redis
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "primary" {
  provider   = aws.primary
  name       = "medical-ocr-${var.environment}-primary"
  subnet_ids = module.primary_vpc.private_subnets
}

resource "aws_elasticache_replication_group" "primary" {
  provider = aws.primary

  replication_group_id       = "medical-ocr-${var.environment}-primary"
  replication_group_description = "Primary Redis cluster for Medical OCR"
  engine                      = "redis"
  engine_version              = "7.0"
  node_type                   = var.environment == "production" ? "cache.r6g.large" : "cache.t3.medium"
  num_cache_clusters          = var.environment == "production" ? 3 : 1
  automatic_failover_enabled  = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  subnet_group_name  = aws_elasticache_subnet_group.primary.name
  security_group_ids = [module.primary_vpc.default_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled  = true
  auth_token                  = random_password.redis_auth.result

  tags = { Role = "primary-redis" }
}

resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

# -----------------------------------------------------------------------------
# Primary Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "primary" {
  provider       = aws.primary
  name           = "medical-ocr/${var.environment}/primary"
  recovery_window = var.environment == "production" ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "primary" {
  provider  = aws.primary
  secret_id = aws_secretsmanager_secret.primary.id
  secret_string = jsonencode({
    database_url      = "postgresql://ocr_admin:${random_password.primary_db.result}@${aws_db_instance.primary.endpoint}/medical_ocr"
    redis_url         = "rediss://:${random_password.redis_auth.result}@${aws_elasticache_replication_group.primary.primary_endpoint}:6379/0"
    dictionary_token  = var.dictionary_repo_token
    environment       = var.environment
    region            = var.primary_region
  })
}

# -----------------------------------------------------------------------------
# Primary Application Load Balancer
# -----------------------------------------------------------------------------

resource "aws_lb" "primary" {
  provider           = aws.primary
  name               = "medical-ocr-${var.environment}-primary-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [module.primary_vpc.default_security_group_id]
  subnets            = module.primary_vpc.public_subnets

  enable_deletion_protection = var.environment == "production"

  tags = { Role = "primary-alb" }
}

resource "aws_lb_target_group" "primary" {
  provider = aws.primary
  name     = "medical-ocr-${var.environment}-primary-tg"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = module.primary_vpc.vpc_id

  health_check {
    path              = "/health"
    interval          = 30
    timeout           = 5
    healthy_threshold = 2
    unhealthy_threshold = 3
    matcher           = "200"
  }
}

resource "aws_lb_listener" "primary_http" {
  provider = aws.primary
  load_balancer_arn = aws_lb.primary.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "primary_https" {
  provider = aws.primary
  load_balancer_arn = aws_lb.primary.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.domain_name != "" ? aws_acm_certificate.primary[0].arn : ""

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.primary.arn
  }
}

# ACM certificate (only if domain_name is provided)
resource "aws_acm_certificate" "primary" {
  provider          = aws.primary
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = { Role = "primary-cert" }
}

# -----------------------------------------------------------------------------
# Primary CloudWatch Dashboard & Alarms
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "primary" {
  provider = aws.primary
  dashboard_name = "medical-ocr-${var.environment}-primary"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "EKS Cluster CPU"
          metrics = [
            ["AWS/EKS", "cluster_cpu_utilization", "ClusterName", "medical-ocr-${var.environment}--primary"],
          ]
          period = 300
          stat   = "Average"
          region = var.primary_region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "RDS Database Connections"
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.primary.id],
          ]
          period = 300
          stat   = "Average"
          region = var.primary_region
        }
      },
    ]
  })
}

# SNS topic for alerts
resource "aws_sns_topic" "primary_alerts" {
  provider = aws.primary
  name     = "medical-ocr-${var.environment}-primary-alerts"
}

resource "aws_sns_topic_subscription" "primary_email" {
  provider  = aws.primary
  for_each  = toset(var.alarm_email_addresses)
  topic_arn = aws_sns_topic.primary_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# RDS CPU alarm
resource "aws_cloudwatch_metric_alarm" "primary_rds_cpu" {
  provider = aws.primary
  alarm_name          = "medical-ocr-${var.environment}-primary-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Primary RDS CPU utilization exceeds 80%"
  actions_enabled     = true
  alarm_actions       = [aws_sns_topic.primary_alerts.arn]
  ok_actions          = [aws_sns_topic.primary_alerts.arn]
  dimensions = {
    DBInstanceIdentifier = aws_db_instance.primary.id
  }
}

# ALB 5xx alarm
resource "aws_cloudwatch_metric_alarm" "primary_alb_5xx" {
  provider = aws.primary
  alarm_name          = "medical-ocr-${var.environment}-primary-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Primary ALB 5xx errors exceed threshold"
  actions_enabled     = true
  alarm_actions       = [aws_sns_topic.primary_alerts.arn]
  ok_actions          = [aws_sns_topic.primary_alerts.arn]
  dimensions = {
    LoadBalancer = aws_lb.primary.arn_suffix
  }
}
