# -----------------------------------------------------------------------------
# Networking Module - Outputs
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "ID of the created VPC"
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs"
  value       = module.vpc.private_subnets
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = module.vpc.public_subnets
}

output "cluster_security_group_id" {
  description = "Security group ID assigned to the EKS cluster"
  value       = aws_security_group.eks_cluster.id
}

output "rds_security_group_id" {
  description = "Security group ID assigned to the RDS instance"
  value       = aws_security_group.rds.id
}

output "lb_security_group_id" {
  description = "Security group ID assigned to the Load Balancer"
  value       = aws_security_group.load_balancer.id
}

output "nat_gateway_ids" {
  description = "List of NAT gateway IDs"
  value       = module.vpc.nat_gateway_ids
}

output "vpc_cidr_block" {
  description = "CIDR block of the VPC"
  value       = module.vpc.vpc_cidr_block
}

output "availability_zones" {
  description = "Availability zones used by this module"
  value       = local.azs
}
