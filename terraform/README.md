# Medical Handwriting OCR — Terraform Infrastructure

## Architecture Overview

This directory contains the Terraform configuration for provisioning the full AWS
infrastructure for the **Medical Handwriting OCR** project. The infrastructure
includes:

- **VPC** with public/private subnets, NAT gateways, and VPC endpoints
- **Amazon EKS** cluster with general-purpose and GPU node groups
- **Amazon RDS** PostgreSQL database tuned for OCR workloads
- **AWS Secrets Manager** for all application secrets
- **CloudWatch** monitoring with dashboards, log groups, and alarms

---

## Module Structure

```
terraform/
├── main.tf                     # Original monolithic config (preserved)
├── main-refactored.tf          # NEW — modular root entry point
├── terraform.tfvars.example    # Example variable values
├── README.md                   # This file
│
└── modules/
    ├── networking/             # VPC, subnets, security groups, endpoints
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── versions.tf
    │
    ├── eks/                    # EKS cluster, node groups, Helm releases
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── versions.tf
    │
    ├── database/               # RDS PostgreSQL, parameter groups, alarms
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── versions.tf
    │
    ├── secrets/                # Secrets Manager (tokens, keys, DB URL)
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── versions.tf
    │
    └── monitoring/             # CloudWatch dashboards, alarms, log groups
        ├── main.tf
        ├── variables.tf
        ├── outputs.tf
        └── versions.tf
```

### Module Dependencies

```
networking
  └── eks              (vpc_id, private_subnet_ids, cluster_security_group_id)
  └── database         (private_subnet_ids, rds_security_group_id)
  └── monitoring        (no direct dependency)

monitoring
  └── database         (alarm_topic_arn for CloudWatch alarms)

database
  └── secrets          (endpoint, db_name, username, password)
```

---

## How to Use

### Prerequisites

1. **AWS CLI** configured with appropriate credentials
2. **Terraform** >= 1.5.0 installed
3. An S3 bucket and DynamoDB table for remote state (or modify the backend)

### Switching from `main.tf` to `main-refactored.tf`

The original `main.tf` has been **preserved** for reference and rollback. To
switch to the modular configuration:

#### Option A — Replace main.tf (recommended once validated)

```bash
cd terraform/

# Backup the original
cp main.tf main.tf.backup

# Replace with the refactored version
mv main-refactored.tf main.tf

# Re-initialize (module sources changed)
terraform init -reconfigure

# Plan and apply
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

#### Option B — Run main-refactored.tf alongside main.tf (testing only)

> **Warning:** This approach creates all resources **duplicated**. Use only
> for validating the module structure, not for production deployment.

```bash
# Terraform processes all .tf files in the directory — do NOT run this
# with main.tf present unless you want duplicated resources.
```

### Standard Workflow

```bash
cd terraform/

# 1. Initialize providers and download modules
terraform init

# 2. See what will be created
terraform plan -var-file=terraform.tfvars

# 3. Apply changes
terraform apply -var-file=terraform.tfvars

# 4. After deployment, configure kubectl
aws eks update-kubeconfig --name medical-ocr-staging --region us-east-1

# 5. Destroy everything (when done)
terraform destroy -var-file=terraform.tfvars
```

### Providing Sensitive Variables

Sensitive values (tokens, API keys) should **not** be committed to version
control. Use one of these approaches:

```bash
# Option 1: Environment variables (TF_VAR_ prefix)
export TF_VAR_dictionary_repo_token="your-token"
export TF_VAR_umls_api_key="your-key"

# Option 2: Separate secrets file (add to .gitignore)
echo "secrets.tfvars" >> .gitignore
# Edit secrets.tfvars with sensitive values
terraform plan -var-file=terraform.tfvars -var-file=secrets.tfvars
```

---

## Variables Reference

### Root Module Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `environment` | string | (required) | `development`, `staging`, or `production` |
| `region` | string | `us-east-1` | AWS region for all resources |
| `vpc_cidr` | string | `10.0.0.0/16` | CIDR block for the VPC |
| `dictionary_repo_token` | string, sensitive | `""` | Token for medical dictionary repo |
| `umls_api_key` | string, sensitive | `""` | UMLS API key |
| `admin_token` | string, sensitive | `""` | Admin access token |
| `minio_access_key` | string, sensitive | `""` | MinIO access key |
| `minio_secret_key` | string, sensitive | `""` | MinIO secret key |
| `alarm_email_addresses` | list(string) | `[]` | Emails for alarm notifications |

### Environment-Based Defaults

The modules automatically adjust sizing based on the `environment` variable:

| Resource | Development | Staging | Production |
|---|---|---|---|
| NAT Gateways | 1 (single) | 1 (single) | 3 (HA) |
| General Nodes (desired) | 2 | 2 | 3 |
| General Nodes (max) | 5 | 5 | 10 |
| GPU Nodes (desired) | 1 | 1 | 2 |
| GPU Nodes (max) | 2 | 2 | 5 |
| Node Capacity | SPOT | SPOT | ON_DEMAND |
| RDS Instance | db.t3.medium | db.t3.medium | db.r6g.xlarge |
| RDS Storage | 20 GB | 20 GB | 100 GB |
| Backup Retention | 7 days | 7 days | 30 days |
| Deletion Protection | false | false | true |
| RDS Multi-AZ | false | false | true |

---

## Outputs Reference

| Output | Description |
|---|---|
| `vpc_id` | VPC ID |
| `private_subnet_ids` | Private subnet IDs |
| `public_subnet_ids` | Public subnet IDs |
| `cluster_endpoint` | EKS cluster API endpoint |
| `cluster_name` | EKS cluster name |
| `cluster_certificate_authority_data` | EKS cluster CA certificate (base64) |
| `configure_kubectl` | Shell command to configure kubectl |
| `db_endpoint` | RDS endpoint (sensitive) |
| `db_name` | Database name |
| `db_password` | Database password (sensitive) |
| `secret_arns` | Map of all secret names → ARNs |
| `alarm_topic_arn` | SNS topic ARN for alarms |
| `dashboard_name` | CloudWatch dashboard name |

---

## Cost Optimization Tips

1. **Development/Staging**: Uses SPOT instances for general nodes (up to 70% savings)
2. **Single NAT Gateway**: Non-production environments use one NAT gateway to
   reduce hourly costs (~$32/month savings vs. multi-AZ NAT)
3. **GPU Auto-scaling**: GPU nodes scale to 0 when idle (no GPU instances running
   when no OCR jobs are queued)
4. **gp3 Storage**: Default storage type for RDS, 20% cheaper than gp2 with
   better baseline performance

---

## Troubleshooting

### Terraform state conflicts

If switching from the original `main.tf` to the refactored modules, the state
may contain resources that no longer exist in the new configuration. Use
`terraform state rm` to clean up:

```bash
terraform state rm module.vpc
terraform state rm module.eks
terraform state rm aws_db_instance.main
terraform state rm random_password.db_password
```

### Module not found errors

Ensure you're running commands from the `terraform/` directory (not the repo root):

```bash
cd terraform/
terraform init
```

### Provider version mismatches

If you see version conflicts, run a full dependency refresh:

```bash
terraform init -upgrade
```
