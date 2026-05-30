# -----------------------------------------------------------------------------
# EKS Cluster Module
# -----------------------------------------------------------------------------
# Provisions a fully managed Amazon EKS cluster for the Medical Handwriting OCR
# project, including:
#   - EKS cluster with managed node groups (general + GPU)
#   - IAM roles for cluster and worker nodes (auto-created by the module)
#   - EKS addons: vpc-cni, coredns, kube-proxy, eks-pod-identity-agent
#   - AWS Load Balancer Controller via Helm
#   - Cluster Autoscaler for dynamic node scaling
# -----------------------------------------------------------------------------

locals {
  # Standard tags merged with caller-supplied extras
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "eks"
    },
    var.tags
  )

  cluster_name = "${var.project_name}-${var.environment}"
}

# =============================================================================
# EKS Cluster
# =============================================================================
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  # -- Cluster Identity -------------------------------------------------------
  cluster_name    = local.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  cluster_endpoint_public_access  = var.cluster_endpoint_public_access
  cluster_endpoint_private_access = var.cluster_endpoint_private_access

  # Attach the security group we created in the networking module
  cluster_security_group_id = var.cluster_security_group_id

  # -- IAM roles are auto-created by the module --------------------------------

  # -- Cluster Addons ----------------------------------------------------------
  cluster_addons = {
    for name, version in var.cluster_addons : name => {
      most_recent = version == "most_recent"
      version     = version == "most_recent" ? null : version
    }
  }

  # -- Managed Node Groups ----------------------------------------------------
  eks_managed_node_groups = {

    # General-purpose nodes: run backend API, Celery workers, etc.
    general = {
      desired_size   = var.general_node_group_desired_size
      min_size       = var.general_node_group_min_size
      max_size       = var.general_node_group_max_size
      instance_types = var.general_node_instance_types
      capacity_type  = var.general_node_capacity_type
    }

    # GPU nodes: run OCR model inference (TorchServe, Triton, etc.)
    gpu = {
      desired_size   = var.gpu_node_group_desired_size
      min_size       = var.gpu_node_group_min_size
      max_size       = var.gpu_node_group_max_size
      instance_types = var.gpu_node_instance_types
      capacity_type  = "ON_DEMAND"

      # Label GPU nodes so workloads can target them via nodeSelector
      labels = {
        "nvidia.com/gpu" = "true"
      }

      # Taint ensures only GPU-tolerant pods are scheduled here
      taints = [{
        key    = "nvidia.com/gpu"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }

  tags = local.common_tags
}

# =============================================================================
# Kubernetes Provider (derived from EKS cluster)
# =============================================================================
# The cluster's OIDC provider and authentication data are used so that the
# Helm and Kubernetes providers can interact with the newly-created cluster.

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", local.cluster_name]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", local.cluster_name]
    }
  }
}

# =============================================================================
# AWS Load Balancer Controller (Helm)
# =============================================================================
# Required for Ingress resources to automatically provision Application Load
# Balancers in AWS. This replaces the deprecated in-tree cloud provider.
resource "helm_release" "aws_lb_controller" {
  count = var.enable_aws_lb_controller ? 1 : 0

  name             = "aws-load-balancer-controller"
  namespace        = "kube-system"
  create_namespace = false

  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.aws_lb_controller_chart_version

  set {
    name  = "clusterName"
    value = local.cluster_name
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.eks.eks_managed_node_groups["general"].iam_role_arn
  }

  # Wait for the cluster to be ready before installing
  depends_on = [module.eks]
}

# =============================================================================
# Cluster Autoscaler (Helm)
# =============================================================================
# Automatically adjusts the number of nodes in each node group based on
# pending pods, resource requests, and configured scaling limits.
resource "helm_release" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  name             = "cluster-autoscaler"
  namespace        = "kube-system"
  create_namespace = false

  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = var.cluster_autoscaler_chart_version

  set {
    name  = "autoDiscovery.clusterName"
    value = local.cluster_name
  }

  set {
    name  = "awsRegion"
    value = var.region
  }

  set {
    name  = "extraArgs.balance-similar-node-groups"
    value = "true"
  }

  set {
    name  = "extraArgs.skip-nodes-with-system-pods"
    value = "false"
  }

  depends_on = [module.eks]
}
