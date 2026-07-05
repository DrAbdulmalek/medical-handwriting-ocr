# =============================================================================
# SECONDARY REGION — Standby/Failover Stack
# =============================================================================

# -----------------------------------------------------------------------------
# Secondary AWS Provider
# -----------------------------------------------------------------------------

provider "aws" {
  alias  = "secondary"
  region = var.secondary_region
  default_tags {
    tags = merge(local.common_tags, { Region = "secondary" })
  }
}

# -----------------------------------------------------------------------------
# Secondary VPC (non-overlapping CIDR with primary)
# -----------------------------------------------------------------------------

module "secondary_vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  providers = { aws = aws.secondary }

  name = "medical-ocr-${var.environment}-secondary"
  cidr = var.secondary_vpc_cidr

  azs = [
    "${var.secondary_region}a",
    "${var.secondary_region}b",
    "${var.secondary_region}c",
  ]

  private_subnets = [
    cidrsubnet(var.secondary_vpc_cidr, 8, 1),
    cidrsubnet(var.secondary_vpc_cidr, 8, 2),
    cidrsubnet(var.secondary_vpc_cidr, 8, 3),
  ]
  public_subnets = [
    cidrsubnet(var.secondary_vpc_cidr, 8, 101),
    cidrsubnet(var.secondary_vpc_cidr, 8, 102),
    cidrsubnet(var.secondary_vpc_cidr, 8, 103),
  ]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true

  enable_flow_log                     = true
  create_flow_log_cloudwatch_log_group = true
  create_flow_log_cloudwatch_iam_role  = true
}

# -----------------------------------------------------------------------------
# Cross-region VPC Peering
# -----------------------------------------------------------------------------

resource "aws_vpc_peering_connection" "primary_to_secondary" {
  provider    = aws.primary
  vpc_id      = module.primary_vpc.vpc_id
  peer_vpc_id = module.secondary_vpc.vpc_id
  peer_region = var.secondary_region
  auto_accept = false

  tags = {
    Name = "medical-ocr-primary-to-secondary"
  }

  accepter {
    allow_remote_vpc_dns_resolution = true
  }
  requester {
    allow_remote_vpc_dns_resolution = true
  }
}

resource "aws_vpc_peering_connection_accepter" "secondary_accept" {
  provider = aws.secondary
  vpc_pcx_id = aws_vpc_peering_connection.primary_to_secondary.id
  auto_accept = true
}

# Peering routes: primary → secondary
resource "aws_route" "primary_to_secondary" {
  provider                  = aws.primary
  count                     = length(module.primary_vpc.private_route_table_ids)
  route_table_id            = module.primary_vpc.private_route_table_ids[count.index]
  destination_cidr_block    = var.secondary_vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.primary_to_secondary.id
}

# Peering routes: secondary → primary
resource "aws_route" "secondary_to_primary" {
  provider                  = aws.secondary
  count                     = length(module.secondary_vpc.private_route_table_ids)
  route_table_id            = module.secondary_vpc.private_route_table_ids[count.index]
  destination_cidr_block    = var.primary_vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.primary_to_secondary.id
}

# -----------------------------------------------------------------------------
# Secondary EKS Cluster (Standby)
# -----------------------------------------------------------------------------

module "secondary_eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"
  providers = { aws = aws.secondary }

  cluster_name    = "medical-ocr-${var.environment}-secondary"
  cluster_version = var.eks_cluster_version

  vpc_id     = module.secondary_vpc.vpc_id
  subnet_ids = module.secondary_vpc.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access  = true
  cluster_endpoint_public_access_cidrs = ["0.0.0.0/0"]

  cluster_addons = {
    vpc-cni = { most_recent = true }
    coredns = { most_recent = true }
    kube-proxy = { most_recent = true }
  }

  # Standby cluster – smaller footprint, no GPU
  eks_managed_node_groups = {
    general = {
      desired_size   = 1
      min_size       = 1
      max_size       = var.environment == "production" ? 10 : 3
      instance_types = [var.secondary_instance_types.general]
      capacity_type  = "SPOT"
    }
  }
}

# -----------------------------------------------------------------------------
# Secondary RDS Read Replica (Cross-Region)
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "secondary" {
  provider   = aws.secondary
  name       = "medical-ocr-${var.environment}-secondary"
  subnet_ids = module.secondary_vpc.private_subnets
  tags       = { Name = "medical-ocr-${var.environment}-secondary" }
}

resource "aws_db_instance" "secondary" {
  provider = aws.secondary

  identifier = "medical-ocr-${var.environment}-secondary-replica"
  engine     = "postgres"
  engine_version = "15.4"
  instance_class  = var.rds_instance_class_secondary

  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage
  storage_type          = "gp3"

  # Cross-region read replica configuration
  replicate_source_db = aws_db_instance.primary.id

  vpc_security_group_ids = [module.secondary_vpc.default_security_group_id]
  db_subnet_group_name   = aws_db_subnet_group.secondary.name

  storage_encrypted = true

  # Read replicas inherit backup settings from the source
  backup_retention_period = var.environment == "production" ? 30 : 7
  skip_final_snapshot     = true  # Read replicas don't need final snapshots

  parameter_group_name = "default.postgres15"

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = { Role = "secondary-read-replica" }
}

# -----------------------------------------------------------------------------
# Secondary ElastiCache Redis Read Replica (Global Datastore)
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "secondary" {
  provider   = aws.secondary
  name       = "medical-ocr-${var.environment}-secondary"
  subnet_ids = module.secondary_vpc.private_subnets
}

# Global Datastore for Redis cross-region replication
resource "aws_elasticache_global_replication_group" "medical_ocr" {
  provider = aws.primary
  global_replication_group_id_suffix = "medical-ocr-${var.environment}"
  primary_replication_group_id      = aws_elasticache_replication_group.primary.id

  lifecycle {
    ignore_changes = [global_replication_group_id_suffix]
  }
}

resource "aws_elasticache_replication_group" "secondary" {
  provider = aws.secondary

  replication_group_id        = "medical-ocr-${var.environment}-secondary"
  replication_group_description = "Secondary Redis cluster for Medical OCR (read replica)"
  engine                      = "redis"
  engine_version              = "7.0"
  node_type                   = "cache.t3.medium"
  num_cache_clusters          = 1
  automatic_failover_enabled  = false

  global_replication_group_id = aws_elasticache_global_replication_group.medical_ocr.global_replication_group_id

  subnet_group_name  = aws_elasticache_subnet_group.secondary.name
  security_group_ids = [module.secondary_vpc.default_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled  = true

  tags = { Role = "secondary-redis" }
}

# -----------------------------------------------------------------------------
# Secondary Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "secondary" {
  provider       = aws.secondary
  name           = "medical-ocr/${var.environment}/secondary"
  recovery_window = var.environment == "production" ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "secondary" {
  provider  = aws.secondary
  secret_id = aws_secretsmanager_secret.secondary.id
  secret_string = jsonencode({
    database_url     = "postgresql://ocr_admin_readonly@${aws_db_instance.secondary.endpoint}/medical_ocr"
    redis_url        = "rediss://:${random_password.redis_auth.result}@${aws_elasticache_replication_group.secondary.primary_endpoint}:6379/0"
    region           = var.secondary_region
    is_read_replica  = true
  })
}

# -----------------------------------------------------------------------------
# Secondary Application Load Balancer
# -----------------------------------------------------------------------------

resource "aws_lb" "secondary" {
  provider           = aws.secondary
  name               = "medical-ocr-${var.environment}-secondary-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [module.secondary_vpc.default_security_group_id]
  subnets            = module.secondary_vpc.public_subnets

  tags = { Role = "secondary-alb" }
}

resource "aws_lb_target_group" "secondary" {
  provider = aws.secondary
  name     = "medical-ocr-${var.environment}-secondary-tg"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = module.secondary_vpc.vpc_id

  health_check {
    path              = "/health"
    interval          = 30
    timeout           = 5
    healthy_threshold = 2
    unhealthy_threshold = 3
    matcher           = "200"
  }
}

resource "aws_lb_listener" "secondary_http" {
  provider = aws.secondary
  load_balancer_arn = aws_lb.secondary.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "secondary_https" {
  provider = aws.secondary
  load_balancer_arn = aws_lb.secondary.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.domain_name != "" ? aws_acm_certificate.secondary[0].arn : ""

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.secondary.arn
  }
}

resource "aws_acm_certificate" "secondary" {
  provider          = aws.secondary
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = { Role = "secondary-cert" }
}

# -----------------------------------------------------------------------------
# Secondary CloudWatch Dashboard
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "secondary" {
  provider = aws.secondary
  dashboard_name = "medical-ocr-${var.environment}-secondary"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title   = "Secondary EKS CPU"
          metrics = [["AWS/EKS", "cluster_cpu_utilization", "ClusterName", module.secondary_eks.cluster_name]]
          period  = 300
          stat    = "Average"
          region  = var.secondary_region
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title   = "Secondary RDS Replica Lag"
          metrics = [["AWS/RDS", "ReplicaLag", "DBInstanceIdentifier", aws_db_instance.secondary.id]]
          period  = 300
          stat    = "Average"
          region  = var.secondary_region
        }
      },
    ]
  })
}

# Replica lag alarm
resource "aws_cloudwatch_metric_alarm" "secondary_replica_lag" {
  provider = aws.secondary
  alarm_name          = "medical-ocr-${var.environment}-secondary-replica-lag"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ReplicaLag"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 60  # seconds
  alarm_description   = "Cross-region RDS replica lag exceeds 60 seconds"
  actions_enabled     = true
  alarm_actions       = [aws_sns_topic.primary_alerts.arn]
  dimensions = {
    DBInstanceIdentifier = aws_db_instance.secondary.id
  }
}
