# ============================================================
# Contoso Financial — Terraform Variables
# Target: AWS (us-east-1 primary, us-west-2 DR)
# ============================================================

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "Primary AWS region"
}

variable "environment" {
  type        = string
  default     = "prod"
  description = "Deployment environment"
  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "environment must be one of: prod, staging, dev"
  }
}

variable "project" {
  type        = string
  default     = "contoso-migration"
  description = "Project name — used as prefix for all resource names"
}

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "CIDR block for the VPC"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
  description = "CIDR blocks for public subnets (one per AZ)"
}

variable "private_subnet_cidrs" {
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
  description = "CIDR blocks for private subnets (one per AZ)"
}

variable "db_instance_class" {
  type        = string
  default     = "db.t3.medium"
  description = "RDS instance class — right-size after 6mo of CloudWatch metrics"
}

variable "db_name" {
  type        = string
  default     = "contoso"
  description = "Name of the Postgres database"
}

variable "db_username" {
  type        = string
  default     = "contoso"
  sensitive   = true
  description = "Master username for RDS — password managed by AWS Secrets Manager"
}

variable "db_multi_az" {
  type        = bool
  default     = true
  description = "Enable Multi-AZ for RDS (set false for dev/staging to save cost)"
}

variable "webapp_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag for the web app — use git SHA in CI/CD"
}

variable "batch_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag for the batch worker"
}

variable "webapp_cpu" {
  type        = number
  default     = 512
  description = "Fargate CPU units for web app task (1024 = 1 vCPU)"
}

variable "webapp_memory" {
  type        = number
  default     = 1024
  description = "Fargate memory (MB) for web app task"
}

variable "webapp_desired_count" {
  type        = number
  default     = 2
  description = "Number of web app ECS tasks — minimum 2 for HA"
}

variable "batch_cpu" {
  type        = number
  default     = 256
  description = "Fargate CPU units for batch worker task"
}

variable "batch_memory" {
  type        = number
  default     = 512
  description = "Fargate memory (MB) for batch worker task"
}

variable "reconciliation_bucket_name" {
  type        = string
  description = "S3 bucket for reconciliation files — must be globally unique. Suggested: contoso-reconciliation-<account-id>"
}

variable "redis_node_type" {
  type        = string
  default     = "cache.t3.micro"
  description = "ElastiCache node type — upgrade if session latency exceeds 5ms p99"
}

variable "tags" {
  type = map(string)
  default = {
    Project     = "contoso-migration"
    ManagedBy   = "terraform"
    Environment = "prod"
  }
  description = "Default tags applied to all resources"
}
