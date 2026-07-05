# =============================================================================
# Outputs — Multi-Region Module
# =============================================================================

# Primary Region
output "primary_cluster_endpoint" {
  description = "EKS cluster endpoint (primary region)"
  value       = module.primary_eks.cluster_endpoint
}

output "primary_cluster_name" {
  description = "EKS cluster name (primary)"
  value       = module.primary_eks.cluster_name
}

output "primary_db_endpoint" {
  description = "RDS PostgreSQL endpoint (primary writer)"
  value       = aws_db_instance.primary.endpoint
  sensitive   = true
}

output "primary_db_replica_endpoint" {
  description = "RDS PostgreSQL endpoint (secondary read replica)"
  value       = aws_db_instance.secondary.endpoint
  sensitive   = true
}

output "primary_alb_dns" {
  description = "Application Load Balancer DNS (primary)"
  value       = aws_lb.primary.dns_name
}

output "primary_redis_endpoint" {
  description = "ElastiCache Redis endpoint (primary)"
  value       = aws_elasticache_replication_group.primary.primary_endpoint
  sensitive   = true
}

output "primary_secret_arn" {
  description = "Secrets Manager ARN (primary)"
  value       = aws_secretsmanager_secret.primary.arn
}

# Secondary Region
output "secondary_cluster_endpoint" {
  description = "EKS cluster endpoint (secondary region)"
  value       = module.secondary_eks.cluster_endpoint
}

output "secondary_cluster_name" {
  description = "EKS cluster name (secondary)"
  value       = module.secondary_eks.cluster_name
}

output "secondary_alb_dns" {
  description = "Application Load Balancer DNS (secondary)"
  value       = aws_lb.secondary.dns_name
}

output "secondary_redis_endpoint" {
  description = "ElastiCache Redis endpoint (secondary)"
  value       = aws_elasticache_replication_group.secondary.primary_endpoint
  sensitive   = true
}

output "secondary_secret_arn" {
  description = "Secrets Manager ARN (secondary)"
  value       = aws_secretsmanager_secret.secondary.arn
}

# Global
output "domain_name" {
  description = "Route53 domain name"
  value       = var.domain_name
}

output "zone_id" {
  description = "Route53 hosted zone ID"
  value       = try(aws_route53_zone.medical_ocr[0].zone_id, "")
}

output "vpc_peering_id" {
  description = "Cross-region VPC peering connection ID"
  value       = aws_vpc_peering_connection.primary_to_secondary.id
}

output "sns_topic_arn" {
  description = "SNS topic ARN for alert notifications"
  value       = aws_sns_topic.primary_alerts.arn
}

output "primary_vpc_id" {
  description = "VPC ID (primary region)"
  value       = module.primary_vpc.vpc_id
}

output "secondary_vpc_id" {
  description = "VPC ID (secondary region)"
  value       = module.secondary_vpc.vpc_id
}

output "regions" {
  description = "List of all deployed regions"
  value = [var.primary_region, var.secondary_region]
}
