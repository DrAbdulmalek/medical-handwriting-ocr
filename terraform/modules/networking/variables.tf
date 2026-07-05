# -----------------------------------------------------------------------------
# Networking Module - Input Variables
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
  description = "AWS region for resource deployment"
  type        = string
  default     = "us-east-1"
}

# -- VPC Configuration --------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of availability zones to use for subnets"
  type        = list(string)
  default     = []
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
}

variable "enable_nat_gateway" {
  description = "Whether to provision NAT gateways for private subnets"
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Use a single NAT gateway (cost saving); set false in production for HA"
  type        = bool
  default     = true
}

variable "enable_vpc_endpoints" {
  description = "Create VPC endpoints (AWS PrivateLink) for specified services"
  type        = bool
  default     = true
}

variable "vpc_endpoint_services" {
  description = "List of AWS service names for which to create VPC interface endpoints"
  type        = list(string)
  default     = ["secretsmanager", "ssm", "ssmmessages", "ecr.api", "ecr.dkr"]
}

# -- Security Groups ----------------------------------------------------------

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access cluster and LB security groups"
  type        = list(string)
  default     = ["10.0.0.0/16"]
}

variable "eks_cluster_security_group_description" {
  description = "Description for the EKS cluster security group"
  type        = string
  default     = "Security group for EKS cluster control plane"
}

variable "rds_security_group_description" {
  description = "Description for the RDS security group"
  type        = string
  default     = "Security group for RDS PostgreSQL instance"
}

variable "lb_security_group_description" {
  description = "Description for the Load Balancer security group"
  type        = string
  default     = "Security group for Application Load Balancer"
}

# -- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all networking resources"
  type        = map(string)
  default     = {}
}
