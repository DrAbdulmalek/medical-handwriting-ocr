# -----------------------------------------------------------------------------
# EKS Module - Input Variables
# -----------------------------------------------------------------------------

# -- Naming & Environment -----------------------------------------------------

variable "project_name" {
  description = "Name of the project, used for resource naming conventions"
  type        = string
  default     = "medical-ocr"
}

variable "environment" {
  description = "Deployment environment (development, staging, or production)"
  type        = string

  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be one of: development, staging, production."
  }
}

variable "region" {
  description = "AWS region for EKS deployment"
  type        = string
  default     = "us-east-1"
}

# -- Network Inputs (from networking module) ---------------------------------

variable "vpc_id" {
  description = "VPC ID where the EKS cluster will be deployed"
  type        = string
}

variable "subnet_ids" {
  description = "List of private subnet IDs for the EKS cluster and worker nodes"
  type        = list(string)
}

variable "cluster_security_group_id" {
  description = "Security group ID to attach to the EKS cluster"
  type        = string
}

# -- Cluster Configuration ----------------------------------------------------

variable "cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.28"
}

variable "cluster_endpoint_public_access" {
  description = "Enable public access to the EKS cluster API endpoint"
  type        = bool
  default     = true
}

variable "cluster_endpoint_private_access" {
  description = "Enable private access to the EKS cluster API endpoint"
  type        = bool
  default     = true
}

# -- Node Group: General Purpose ----------------------------------------------

variable "general_node_group_desired_size" {
  description = "Desired number of general-purpose worker nodes"
  type        = number
  default     = 2
}

variable "general_node_group_min_size" {
  description = "Minimum number of general-purpose worker nodes"
  type        = number
  default     = 1
}

variable "general_node_group_max_size" {
  description = "Maximum number of general-purpose worker nodes"
  type        = number
  default     = 5
}

variable "general_node_instance_types" {
  description = "EC2 instance types for general-purpose node group"
  type        = list(string)
  default     = ["m6i.xlarge"]
}

variable "general_node_capacity_type" {
  description = "Capacity type for general-purpose nodes (ON_DEMAND or SPOT)"
  type        = string
  default     = "SPOT"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.general_node_capacity_type)
    error_message = "Capacity type must be ON_DEMAND or SPOT."
  }
}

# -- Node Group: GPU (for OCR model inference) ---------------------------------

variable "gpu_node_group_desired_size" {
  description = "Desired number of GPU worker nodes"
  type        = number
  default     = 1
}

variable "gpu_node_group_min_size" {
  description = "Minimum number of GPU worker nodes"
  type        = number
  default     = 0
}

variable "gpu_node_group_max_size" {
  description = "Maximum number of GPU worker nodes"
  type        = number
  default     = 2
}

variable "gpu_node_instance_types" {
  description = "EC2 instance types for GPU node group (must have NVIDIA GPU)"
  type        = list(string)
  default     = ["g4dn.xlarge"]
}

# -- Cluster Addons ------------------------------------------------------------

variable "cluster_addons" {
  description = "Map of EKS cluster addons to enable (name => version)"
  type        = map(string)
  default = {
    "vpc-cni"                  = "v1.14.1-eksbuild.1167"
    "coredns"                  = "v1.10.1-eksbuild.8"
    "kube-proxy"               = "v1.28.4-eksbuild.1167"
    "eks-pod-identity-agent"   = "v1.2.0-eksbuild.1"
  }
}

# -- Helm: AWS Load Balancer Controller ---------------------------------------

variable "enable_aws_lb_controller" {
  description = "Install the AWS Load Balancer Controller via Helm"
  type        = bool
  default     = true
}

variable "aws_lb_controller_chart_version" {
  description = "Helm chart version for the AWS Load Balancer Controller"
  type        = string
  default     = "1.7.1"
}

# -- Cluster Autoscaler --------------------------------------------------------

variable "enable_cluster_autoscaler" {
  description = "Deploy the Kubernetes Cluster Autoscaler"
  type        = bool
  default     = true
}

variable "cluster_autoscaler_chart_version" {
  description = "Helm chart version for the Cluster Autoscaler"
  type        = string
  default     = "9.37.0"
}

# -- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all EKS resources"
  type        = map(string)
  default     = {}
}
