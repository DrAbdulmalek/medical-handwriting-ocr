# -----------------------------------------------------------------------------
# EKS Module - Outputs
# -----------------------------------------------------------------------------

output "cluster_endpoint" {
  description = "Endpoint URL for the EKS cluster API server"
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = module.eks.cluster_name
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded certificate authority data for the EKS cluster"
  value       = module.eks.cluster_certificate_authority_data
}

output "node_group_role_arn" {
  description = "ARN of the IAM role attached to the general-purpose node group"
  value       = module.eks.eks_managed_node_groups["general"].iam_role_arn
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL for the EKS cluster (used for IAM identity mapping)"
  value       = module.eks.cluster_oidc_issuer_url
}

output "cluster_security_group_id" {
  description = "Security group ID for the EKS cluster (from the EKS module)"
  value       = module.eks.cluster_security_group_id
}

output "cluster_version" {
  description = "Kubernetes version of the EKS cluster"
  value       = module.eks.cluster_version
}
