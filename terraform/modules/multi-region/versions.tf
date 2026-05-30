terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.23" }
    helm       = { source = "hashicorp/helm", version = "~> 2.11" }
    random     = { source = "hashicorp/random", version = "~> 3.5" }
  }
}

# =============================================================================
# Common locals
# =============================================================================

locals {
  common_tags = merge(
    {
      Project     = "medical-ocr"
      Environment = var.environment
      ManagedBy   = "terraform"
      Tier        = "multi-region"
    },
    var.tags,
  )

  # Auto-generate non-overlapping CIDR blocks for additional regions
  additional_vpc_cidrs = {
    for i, r in var.additional_regions :
    r => cidrsubnet("10.${i + 2}.0.0/16", 8, 0)
  }
}
